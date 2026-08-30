"""Shared plumbing for the unofficial Reddit scrapers in this repo.

Reddit's public `.json` endpoints (append `.json` to any URL) still work from
residential IPs, but Reddit now 403s datacenter/VPN ranges. To get past that,
register an app at https://www.reddit.com/prefs/apps ("script" or "installed"
type) and put its client id in `tokens.conf`:

    REDDIT_CLIENT_ID = ...
    REDDIT_CLIENT_SECRET =        # only for a "script" app; leave blank otherwise

With those set, requests go through OAuth (oauth.reddit.com, 100 req/min).
Without them, the scripts fall back to the public `.json` host.

Unofficial use is governed by Reddit's API terms. The token loader and 3-7 s
request pacing are inlined so this folder needs no sibling files.
"""

from __future__ import annotations

import os
import random
import threading
import time

import requests

_UA = "dossier/0.1 (github scraper; contact via repo)"


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
# paced session (random PACE_MIN..PACE_MAX s between requests)
# --------------------------------------------------------------------------- #

class PacedSession(requests.Session):
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


class RedditError(RuntimeError):
    """Any failure to retrieve data from Reddit."""


_session: PacedSession | None = None
_base = "https://www.reddit.com"


def _make_session() -> PacedSession:
    global _base
    s = PacedSession()
    s.headers.update({"User-Agent": _UA, "Accept": "application/json"})

    cid = os.environ.get("REDDIT_CLIENT_ID")
    if cid:
        secret = os.environ.get("REDDIT_CLIENT_SECRET", "")
        if secret:
            data = {"grant_type": "client_credentials"}
        else:
            data = {
                "grant_type": "https://oauth.reddit.com/grants/installed_client",
                "device_id": "DO_NOT_TRACK_THIS_DEVICE",
            }
        resp = s.post(
            "https://www.reddit.com/api/v1/access_token",
            data=data,
            auth=(cid, secret),
            timeout=15,
        )
        if resp.status_code == 401:
            raise RedditError("REDDIT_CLIENT_ID / _SECRET rejected (HTTP 401)")
        resp.raise_for_status()
        token = resp.json().get("access_token")
        if not token:
            raise RedditError(f"no access_token in response: {resp.text[:200]}")
        s.headers["Authorization"] = f"bearer {token}"
        _base = "https://oauth.reddit.com"
    return s


def session() -> PacedSession:
    global _session
    if _session is None:
        _session = _make_session()
    return _session


def logged_in() -> bool:
    return _base.startswith("https://oauth")


def api_get(path: str, **params) -> dict | list:
    """GET a Reddit API path (e.g. '/user/spez/about'). Returns parsed JSON."""
    s = session()
    url = _base + path
    if not logged_in():
        url += ".json"
    params.setdefault("raw_json", 1)
    resp = s.get(url, params=params, timeout=20)

    if resp.status_code in (403, 429) and not logged_in():
        raise RedditError(
            f"Reddit blocked the request (HTTP {resp.status_code}). This IP needs "
            f"OAuth - set REDDIT_CLIENT_ID in tokens.conf (see src/reddit/tokens.md)."
        )
    if resp.status_code == 404:
        raise RedditError("not found (HTTP 404)")
    if resp.status_code == 429:
        raise RedditError("rate limited by Reddit (HTTP 429); back off")
    resp.raise_for_status()
    return resp.json()


def clean_name(value: str, kind: str) -> str:
    """Normalise a user or subreddit reference. kind is 'user' or 'subreddit'."""
    v = value.strip().rstrip("/")
    if "reddit.com" in v:
        v = v.split("reddit.com", 1)[1]
    for pfx in ("/u/", "u/", "/user/", "user/", "/r/", "r/", "/"):
        if v.startswith(pfx):
            v = v[len(pfx):]
    v = v.split("/")[0].lstrip("@")
    if not v or not all(c.isalnum() or c in "_-" for c in v):
        raise ValueError(f"invalid {kind} name: {value!r}")
    return v
