"""Shared plumbing for the unofficial YouTube scrapers in this repo.

YouTube embeds big JSON blobs in its HTML (`ytInitialData`,
`ytInitialPlayerResponse`) and exposes the same InnerTube API its own frontend
calls (`/youtubei/v1/...` with a public web key). Neither needs a developer API
key or login for public data.

No token is required. `YT_COOKIE` in tokens.conf (a raw Cookie header from a
logged-in browser) is only useful for age-restricted videos or a region stuck
behind a consent wall.

Unofficial, undocumented, rate limited, subject to change, and against
YouTube's Terms of Service. For anything reliable use the Data API v3.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import threading
import time

import requests


# --------------------------------------------------------------------------- #
# tokens.conf loader (KEY = value lines; env vars win)
# --------------------------------------------------------------------------- #

def _load_tokens_conf() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        path = os.path.join(here, "tokens.conf")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key, val = key.strip(), val.strip().strip("\"'")
                    if key and val and key not in os.environ:
                        os.environ[key] = val
            return
        parent = os.path.dirname(here)
        if parent == here:
            return
        here = parent


_load_tokens_conf()


# --------------------------------------------------------------------------- #
# client-side rate limiting: sleep a random PACE_MIN..PACE_MAX s between requests
# --------------------------------------------------------------------------- #

class PacedSession(requests.Session):
    """requests.Session that paces every request (shared across instances)."""

    _lock = threading.Lock()
    _next_at = 0.0

    def request(self, *args, **kwargs):
        lo = float(os.environ.get("PACE_MIN", 3))
        hi = float(os.environ.get("PACE_MAX", 7))
        with PacedSession._lock:
            now = time.monotonic()
            if now < PacedSession._next_at:
                time.sleep(PacedSession._next_at - now)
            PacedSession._next_at = time.monotonic() + random.uniform(lo, hi)
        return super().request(*args, **kwargs)

# Public InnerTube key from the youtube.com web client. Not a secret.
INNERTUBE_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
INNERTUBE_CLIENT = {"clientName": "WEB", "clientVersion": "2.20240726.00.00"}

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_VIDEO_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
_CHANNEL_ID_RE = re.compile(r"UC[A-Za-z0-9_-]{22}")


class YTScrapeError(RuntimeError):
    """Any failure to retrieve or parse data from YouTube."""


def _cookie_dict() -> dict[str, str]:
    raw = os.environ.get("YT_COOKIE", "")
    out = {}
    for part in raw.split(";"):
        if "=" in part:
            k, _, v = part.strip().partition("=")
            out[k] = v
    return out


def have_login() -> bool:
    return bool(os.environ.get("YT_COOKIE"))


def _sapisid_hash(origin: str = "https://www.youtube.com") -> str | None:
    """Google's SAPISIDHASH auth token, derived from the SAPISID cookie."""
    cookies = _cookie_dict()
    sapisid = (
        cookies.get("SAPISID")
        or cookies.get("__Secure-3PAPISID")
        or cookies.get("__Secure-1PAPISID")
    )
    if not sapisid:
        return None
    ts = int(time.time())
    digest = hashlib.sha1(f"{ts} {sapisid} {origin}".encode()).hexdigest()
    return f"SAPISIDHASH {ts}_{digest}"


def new_session() -> requests.Session:
    session = PacedSession()
    session.headers.update(
        {
            "User-Agent": _UA,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/json",
        }
    )
    # Skip the EU cookie-consent interstitial and force English.
    session.cookies.set("SOCS", "CAI", domain=".youtube.com")
    session.cookies.set("CONSENT", "YES+1", domain=".youtube.com")
    session.cookies.set("PREF", "hl=en&gl=US", domain=".youtube.com")

    raw_cookie = os.environ.get("YT_COOKIE")
    if raw_cookie:
        session.headers["Cookie"] = raw_cookie
        auth = _sapisid_hash()
        if auth:
            session.headers["Authorization"] = auth
            session.headers["X-Origin"] = "https://www.youtube.com"
            session.headers["X-Goog-AuthUser"] = "0"
    return session


def _bot_gated(text_or_reason: str) -> bool:
    t = (text_or_reason or "").lower()
    return (
        "confirm you" in t
        or "not a bot" in t
        or "login_required" in t
        or "sign in to confirm" in t
    )


# --------------------------------------------------------------------------- #
# id / url parsing
# --------------------------------------------------------------------------- #

def video_id_from(value: str) -> str:
    """Accept a bare id or any watch / youtu.be / shorts / embed URL."""
    value = value.strip()
    if re.fullmatch(_VIDEO_ID_RE, value):
        return value
    patterns = (
        r"(?:v=|/shorts/|/embed/|/live/|youtu\.be/)([A-Za-z0-9_-]{11})",
    )
    for pat in patterns:
        m = re.search(pat, value)
        if m:
            return m.group(1)
    raise ValueError(f"could not find a video id in: {value!r}")


def channel_ident(value: str) -> str:
    """Normalise a channel reference to something fetchable.

    Returns either a `UC...` id or a `@handle`. Accepts ids, handles, and
    channel/custom/user URLs.
    """
    value = value.strip().rstrip("/")
    if re.fullmatch(_CHANNEL_ID_RE, value):
        return value
    m = _CHANNEL_ID_RE.search(value)
    if m:
        return m.group(0)
    if "/@" in value:
        return "@" + value.split("/@", 1)[1].split("/")[0]
    if value.startswith("@"):
        return value.split("/")[0]
    for tag in ("/c/", "/user/"):
        if tag in value:
            return value  # keep the full URL; resolve_channel handles it
    if value.startswith("http"):
        return value
    return "@" + value  # bare word -> assume handle


# --------------------------------------------------------------------------- #
# fetching
# --------------------------------------------------------------------------- #

def _match_object(html: str, brace: int) -> str | None:
    """Return the balanced {...} substring starting at index `brace`."""
    depth, in_str, esc = 0, False, False
    for i in range(brace, len(html)):
        ch = html[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return html[brace : i + 1]
    return None


def _json_var(html: str, name: str) -> dict | None:
    """Find `[var] <name> = { ... }` in a page and parse it.

    Tries every occurrence of the assignment, returning the first that is valid
    JSON (some pages carry a decoy match in an inline comment or template).
    """
    pattern = re.compile(r"(?:var\s+)?" + re.escape(name) + r"\s*=\s*\{")
    for m in pattern.finditer(html):
        frag = _match_object(html, m.end() - 1)
        if not frag:
            continue
        try:
            return json.loads(frag)
        except json.JSONDecodeError:
            continue
    return None


def get_html(session: requests.Session, url: str) -> str:
    resp = session.get(url, params={"bpctr": "9999999999", "hl": "en"}, timeout=20)
    if resp.status_code == 429:
        raise YTScrapeError("rate limited by YouTube (HTTP 429); back off")
    resp.raise_for_status()
    return resp.text


def watch_data(session: requests.Session, video_id: str) -> tuple[dict, dict]:
    """Return (ytInitialPlayerResponse, ytInitialData) for a video's watch page."""
    html = get_html(session, f"https://www.youtube.com/watch?v={video_id}")
    player = _json_var(html, "ytInitialPlayerResponse") or {}
    data = _json_var(html, "ytInitialData") or {}

    status = player.get("playabilityStatus") or {}
    reason = f"{status.get('status', '')} {status.get('reason', '')}".strip()
    if _bot_gated(reason) or (not player and _bot_gated(html[:20000])):
        extra = (
            "" if have_login()
            else " - set YT_COOKIE in tokens.conf (YouTube bot-gates "
            "datacenter/VPN IPs)"
        )
        raise YTScrapeError(f"YouTube demanded sign-in for {video_id}{extra}")
    if not player and not data:
        raise YTScrapeError("could not parse watch page (layout changed?)")
    return player, data


def page_data(session: requests.Session, url: str) -> dict:
    """Return ytInitialData for any YouTube page."""
    html = get_html(session, url)
    data = _json_var(html, "ytInitialData")
    if not data:
        raise YTScrapeError(f"could not parse ytInitialData from {url}")
    return data


def innertube(session: requests.Session, endpoint: str, body: dict) -> dict:
    """POST an InnerTube endpoint (e.g. 'next', 'browse', 'player')."""
    payload = {
        "context": {"client": {**INNERTUBE_CLIENT, "hl": "en", "gl": "US"}},
        **body,
    }
    resp = session.post(
        f"https://www.youtube.com/youtubei/v1/{endpoint}",
        params={"key": INNERTUBE_KEY, "prettyPrint": "false"},
        json=payload,
        headers={
            "Content-Type": "application/json",
            "X-YouTube-Client-Name": "1",
            "X-YouTube-Client-Version": INNERTUBE_CLIENT["clientVersion"],
            "Origin": "https://www.youtube.com",
        },
        timeout=20,
    )
    if resp.status_code == 429:
        raise YTScrapeError("rate limited by YouTube (HTTP 429); back off")
    resp.raise_for_status()
    return resp.json()


def resolve_channel_id(session: requests.Session, ident: str) -> str:
    """Turn a handle / custom URL / id into a canonical `UC...` channel id."""
    ident = channel_ident(ident)
    if re.fullmatch(_CHANNEL_ID_RE, ident):
        return ident

    if ident.startswith("@"):
        url = f"https://www.youtube.com/{ident}"
    elif ident.startswith("http"):
        url = ident
    else:
        url = f"https://www.youtube.com/{ident.lstrip('/')}"

    data = page_data(session, url)
    cid = (
        deep_find(data.get("metadata", {}), "externalId")
        or deep_find(data, "externalChannelId")
        or deep_find(data, "channelId")
    )
    if not cid or not re.fullmatch(_CHANNEL_ID_RE, str(cid)):
        raise YTScrapeError(f"could not resolve channel: {ident!r}")
    return cid


# --------------------------------------------------------------------------- #
# JSON helpers
# --------------------------------------------------------------------------- #

def iter_dicts(obj):
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def deep_find(obj, *keys):
    for cur in iter_dicts(obj):
        for key in keys:
            if key in cur:
                return cur[key]
    return None


def text_of(node) -> str:
    """Flatten a YouTube text node (simpleText / runs / {content}) to a string."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if "simpleText" in node:
            return node["simpleText"]
        if "content" in node:
            return node["content"]
        if "runs" in node:
            return "".join(r.get("text", "") for r in node["runs"])
        if "accessibility" in node:
            return (
                node.get("accessibility", {})
                .get("accessibilityData", {})
                .get("label", "")
            )
    return ""


_MULT = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000, "T": 1_000_000_000_000}


def parse_count(text: str | None) -> int | None:
    """'1,234,567 views' -> 1234567 ; '1.2M subscribers' -> 1200000 ; 'No' -> 0."""
    if text is None:
        return None
    text = str(text).strip()
    if not text:
        return None
    if re.match(r"(?i)^(no|none)\b", text):
        return 0
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*([KMBT])?", text, re.I)
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    suffix = (m.group(2) or "").upper()
    return int(round(num * _MULT.get(suffix, 1)))


def first_int(text: str | None) -> int | None:
    """Pull the first plain integer (with thousands separators) out of a string."""
    if not text:
        return None
    m = re.search(r"(\d[\d,]*)", text)
    return int(m.group(1).replace(",", "")) if m else None


def human_duration(seconds: int | None) -> str:
    if not seconds:
        return "0:00"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
