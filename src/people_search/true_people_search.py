"""TruePeopleSearch: name / phone / address lookups.

truepeoplesearch.com sits behind Cloudflare bot management, so a plain HTTP
request (requests/httpx) gets a 403 - the TLS fingerprint and missing browser
headers give it away. This module drives a real headless Chromium via Playwright
instead, which passes the challenge and returns the rendered results page.

    python true_people_search.py name  "John Doe" [--citystatezip "TX"] [--json]
    python true_people_search.py phone 214-555-0123 [--json]
    python true_people_search.py address "123 Main St, Dallas, TX" [--json]
    python true_people_search.py raw   "<full results URL>"        # dump HTML

Add --headful to watch the browser, --timeout SECONDS to change the page budget.

Importable: search_name, search_phone, search_address, fetch_html.

Setup (one time):
    pip install playwright
    playwright install chromium
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import urllib.parse

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

BASE = "https://www.truepeoplesearch.com"

# A recent real Chrome UA - keep roughly in sync with the bundled Chromium.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


# ========================================================================== #
# browser
# ========================================================================== #

def fetch_html(url: str, *, headless: bool = True, timeout: float = 45.0) -> str:
    """Load `url` in headless Chromium and return the rendered HTML.

    Raises RuntimeError if Cloudflare never clears or the page times out.
    """
    time.sleep(random.uniform(3, 7))  # human-ish pacing before hitting the site

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = browser.new_context(
            user_agent=_UA,
            locale="en-US",
            viewport={"width": 1280, "height": 900},
            java_script_enabled=True,
        )
        # Strip the most obvious automation tell before any page script runs.
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)

            # Cloudflare interstitial: title is "Just a moment..." until it clears.
            deadline = timeout * 1000
            try:
                page.wait_for_function(
                    "!/just a moment|attention required|checking your browser/i"
                    ".test(document.title)",
                    timeout=deadline,
                )
            except PWTimeout:
                pass  # fall through; maybe the content is already there

            # Results container or the "no results" banner.
            try:
                page.wait_for_selector(
                    "div.card-summary, #personDetails, .no-results, .content-center",
                    timeout=15000,
                )
            except PWTimeout:
                pass

            html = page.content()
        finally:
            context.close()
            browser.close()

    lowered = html.lower()
    challenge = (
        "just a moment" in lowered
        or "attention required" in lowered
        or "<title>captcha</title>" in lowered
        or "captchatoken" in lowered
    )
    if challenge:
        raise RuntimeError(
            "TruePeopleSearch served an anti-bot challenge (Cloudflare / Turnstile "
            "captcha) instead of results. This is IP-based - datacenter IPs "
            "(cloud, CI, Codespaces) are almost always blocked. Use a residential "
            "proxy, run from a residential connection, or plug in a captcha-solving "
            "service. --headful and a longer --timeout alone will not clear it."
        )
    return html


# ========================================================================== #
# URL builders
# ========================================================================== #

def _results_url(**params: str) -> str:
    query = {k: v for k, v in params.items() if v}
    return f"{BASE}/results?" + urllib.parse.urlencode(query)


# ========================================================================== #
# parsing
# ========================================================================== #

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _text(html_fragment: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", html_fragment)).strip()


# Labels TruePeopleSearch uses on a summary card, in render order.
_STOPS = ["Used to live in", "Related to", "AKA", "Has lived in", "View Details", "View All Details"]


def _between(text: str, start: str, stops: list[str]) -> str | None:
    i = text.find(start)
    if i < 0:
        return None
    i += len(start)
    end = len(text)
    for s in stops:
        j = text.find(s, i)
        if 0 <= j < end:
            end = j
    return text[i:end].strip(" •,–-") or None


def _parse_results(html: str) -> list[dict]:
    """Pull the summary cards out of a /results page.

    Each hit is
    <div class="card ... card-summary" data-detail-link="/find/person/ID">
      <div class="content-header">Name</div>
      <span>Age </span><span class="content-value">34</span> • <span class="content-value">City, ST</span>
      <span class="content-label">Used to live in </span><span class="content-value">...</span>
      <span class="content-label">Related to </span><span class="content-value">...</span>
    Selectors drift; keep this loose.
    """
    # Split on each card's opening <div ... card-summary ...>, keeping the tag.
    chunks = re.split(r'(?=<div[^>]*class="[^"]*card-summary[^"]*")', html)
    out: list[dict] = []
    for chunk in chunks:
        link = re.search(r'data-detail-link="([^"]+)"', chunk)
        if not link:
            continue
        header = re.search(
            r'class="[^"]*content-header[^"]*"[^>]*>(.*?)</div>', chunk, flags=re.S
        )
        name = _text(header.group(1)) if header else None
        if not name:
            continue  # ad / "related people" card without a real result
        flat = _text(chunk)
        age = re.search(r"\bAge\s+(\d{1,3})\b", flat)
        if age:
            lives_in = _between(flat, f"Age {age.group(1)}", _STOPS)
        else:
            lives_in = _between(flat, "Lives in", _STOPS)
        out.append(
            {
                "name": name,
                "age": int(age.group(1)) if age else None,
                "lives_in": lives_in,
                "used_to_live_in": _between(flat, "Used to live in", _STOPS),
                "related_to": _between(flat, "Related to", _STOPS),
                "detail_url": BASE + link.group(1),
            }
        )
    return out


def _parse_detail(html: str) -> dict:
    """Pull the fields off a /find/person/<id> detail page.

    Layout here is far less stable than the results list, so this grabs what
    it can via loose regexes and always keeps raw_text so nothing is lost if
    a selector no longer matches.
    """
    flat = _text(html)

    header = re.search(r'id="personDetails"[^>]*>(.*?)</', html, flags=re.S)
    name = _text(header.group(1)) if header else None

    age = re.search(r"\bAge\s+(\d{1,3})\b", flat)
    phones = sorted(set(re.findall(r'href="tel:([\d+()\-. ]+)"', html)))
    emails = sorted(set(re.findall(r'href="mailto:([^"]+)"', html)))

    stops = ["Previous Addresses", "Used to live in", "Phone", "Email", "Relatives", "Associates", "Related to"]
    current_address = (
        _between(flat, "Current Address", stops)
        or _between(flat, "Lives in", stops)
    )

    return {
        "name": name,
        "age": int(age.group(1)) if age else None,
        "current_address": current_address,
        "phones": phones,
        "emails": emails,
        "raw_text": flat,
    }


def fetch_detail(url: str, **kw) -> dict:
    """Load a person's detail page and parse it. Same pacing/kwargs as fetch_html."""
    return _parse_detail(fetch_html(url, **kw))


# ========================================================================== #
# public API
# ========================================================================== #

def search_name(name: str, citystatezip: str = "", **kw) -> list[dict]:
    url = _results_url(name=name, citystatezip=citystatezip)
    return _parse_results(fetch_html(url, **kw))


def search_phone(phone: str, **kw) -> list[dict]:
    digits = re.sub(r"\D", "", phone)
    url = _results_url(phoneno=digits)
    return _parse_results(fetch_html(url, **kw))


def search_address(address: str, **kw) -> list[dict]:
    # TruePeopleSearch splits street from city/state/zip on the first comma.
    street, _, csz = address.partition(",")
    url = _results_url(streetaddress=street.strip(), citystatezip=csz.strip())
    return _parse_results(fetch_html(url, **kw))


def _describe(r: dict) -> str:
    bits = [r["name"] or "?"]
    if r["age"]:
        bits.append(f"age {r['age']}")
    if r["lives_in"]:
        bits.append(r["lives_in"])
    return " - ".join(bits)


def _prompt_choice(results: list[dict]) -> dict:
    """Print numbered results and prompt on stdin for which one to keep."""
    for i, r in enumerate(results, 1):
        print(f"[{i}] {_describe(r)}")
    while True:
        choice = input(f"pick 1-{len(results)}: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(results):
            return results[int(choice) - 1]
        print("invalid choice")


# ========================================================================== #
# CLI
# ========================================================================== #

def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--headful", action="store_true", help="show the browser window")
    common.add_argument("--timeout", type=float, default=45.0, help="page load budget, seconds")
    common.add_argument("--json", action="store_true", help="emit JSON")

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_name = sub.add_parser("name", help="search by person name", parents=[common])
    p_name.add_argument("query")
    p_name.add_argument("--citystatezip", default="")
    p_name.add_argument("--pick", action="store_true", help="prompt to choose one match")

    p_phone = sub.add_parser("phone", help="reverse phone lookup", parents=[common])
    p_phone.add_argument("query")
    p_phone.add_argument("--pick", action="store_true", help="prompt to choose one match")

    p_addr = sub.add_parser("address", help="reverse address lookup", parents=[common])
    p_addr.add_argument("query")
    p_addr.add_argument("--pick", action="store_true", help="prompt to choose one match")

    p_raw = sub.add_parser("raw", help="dump rendered HTML for a full URL", parents=[common])
    p_raw.add_argument("url")

    args = ap.parse_args(argv)
    kw = {"headless": not args.headful, "timeout": args.timeout}

    try:
        if args.cmd == "raw":
            sys.stdout.write(fetch_html(args.url, **kw))
            return 0
        if args.cmd == "name":
            results = search_name(args.query, args.citystatezip, **kw)
        elif args.cmd == "phone":
            results = search_phone(args.query, **kw)
        else:
            results = search_address(args.query, **kw)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if getattr(args, "pick", False) and results:
        chosen = _prompt_choice(results) if len(results) > 1 else results[0]
        try:
            chosen = {**chosen, "detail": fetch_detail(chosen["detail_url"], **kw)}
        except RuntimeError as e:
            print(f"error fetching detail: {e}", file=sys.stderr)
        results = [chosen]

    if args.json:
        print(json.dumps(results, indent=2))
    elif not results:
        print("no results")
    else:
        for r in results:
            print(_describe(r))
            if r["detail_url"]:
                print(f"  {r['detail_url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
