"""Shared setup for the GitHub lookups - a thin wrapper over the real REST API.

This is the one platform in the repo with a proper public API
(https://docs.github.com/rest). No token is required, but unauthenticated
requests are capped at 60/hour per IP. Drop a personal access token in
`tokens.conf` as `GITHUB_TOKEN` (no scopes needed for public data) to get
5,000/hour.

Because it's a real API with published limits there's no artificial request
pacing here - the client just honours the `X-RateLimit-*` headers and raises a
clear error when the quota is exhausted. Set `PACE_MIN` / `PACE_MAX` if you
want the same 3-7 s throttle the other platforms use.
"""

from __future__ import annotations

import os
import random
import threading
import time

import requests

API = "https://api.github.com"


# --------------------------------------------------------------------------- #
# tokens.conf loader (KEY = value lines; real env vars win)
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
# optional 3-7 s pacing (off unless PACE_MIN/PACE_MAX are set)
# --------------------------------------------------------------------------- #

class _Session(requests.Session):
    _lock = threading.Lock()
    _next_at = 0.0

    def request(self, *args, **kwargs):
        if "PACE_MIN" in os.environ or "PACE_MAX" in os.environ:
            lo = float(os.environ.get("PACE_MIN", 3))
            hi = float(os.environ.get("PACE_MAX", 7))
            with _Session._lock:
                now = time.monotonic()
                if now < _Session._next_at:
                    time.sleep(_Session._next_at - now)
                _Session._next_at = time.monotonic() + random.uniform(lo, hi)
        return super().request(*args, **kwargs)


class GitHubError(RuntimeError):
    """Any failure talking to the GitHub API."""


def have_token() -> bool:
    return bool(os.environ.get("GITHUB_TOKEN"))


_session: _Session | None = None


def session() -> _Session:
    global _session
    if _session is None:
        s = _Session()
        s.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "dossier",
            }
        )
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            s.headers["Authorization"] = f"Bearer {token}"
        _session = s
    return _session


def _raise_for(resp: requests.Response, path: str) -> None:
    if resp.status_code == 404:
        raise GitHubError(f"not found: {path}")
    if resp.status_code in (403, 429):
        remaining = resp.headers.get("X-RateLimit-Remaining")
        if remaining == "0":
            reset = resp.headers.get("X-RateLimit-Reset")
            when = ""
            if reset and reset.isdigit():
                mins = max(0, (int(reset) - int(time.time())) // 60)
                when = f" (resets in ~{mins} min)"
            hint = "" if have_token() else "; set GITHUB_TOKEN in tokens.conf for 5000/hr"
            raise GitHubError(f"GitHub rate limit exhausted{when}{hint}")
        raise GitHubError(f"GitHub refused the request (HTTP {resp.status_code})")
    if resp.status_code == 401:
        raise GitHubError("GITHUB_TOKEN is invalid (HTTP 401)")
    resp.raise_for_status()


def api(path: str, **params):
    """GET one API path (absolute or '/users/x') and return parsed JSON."""
    url = path if path.startswith("http") else API + path
    resp = session().get(url, params=params or None, timeout=20)
    _raise_for(resp, path)
    return resp.json()


def paginate(path: str, *, limit: int, per_page: int = 100, **params):
    """Yield items across pages until `limit` is reached or results run out."""
    url = path if path.startswith("http") else API + path
    got = 0
    params = {**params, "per_page": min(per_page, limit or per_page)}
    while url and (not limit or got < limit):
        resp = session().get(url, params=params, timeout=20)
        _raise_for(resp, path)
        page = resp.json()
        if isinstance(page, dict) and "items" in page:  # search endpoints
            page = page["items"]
        for item in page:
            yield item
            got += 1
            if limit and got >= limit:
                return
        url = resp.links.get("next", {}).get("url")
        params = None  # the "next" link already carries them


def count(path: str, **params) -> int:
    """Cheap total for a listing: request 1 item, read the last-page number."""
    import re

    url = path if path.startswith("http") else API + path
    resp = session().get(
        url, params={**params, "per_page": 1}, timeout=20
    )
    _raise_for(resp, path)
    last = resp.links.get("last", {}).get("url")
    if last:
        m = re.search(r"[?&]page=(\d+)", last)
        if m:
            return int(m.group(1))
    return len(resp.json())  # single page (0 or 1 items when per_page=1... )


def rate_limit() -> dict:
    return api("/rate_limit").get("resources", {}).get("core", {})
