"""Shared setup for the Instagram scrapers - a logged-in instagrapi client.

instagrapi (https://github.com/subzeroid/instagrapi) does the heavy lifting:
it speaks Instagram's private mobile API, which - unlike the logged-out web
endpoints - keeps working from datacenter IPs as long as you hand it a real
`sessionid` cookie.

Put the cookie in `tokens.conf` (repo root) as `IG_SESSIONID`; see
src/instagram/tokens.md for how to get it. Use a throwaway account - this is
against Instagram's Terms of Service and accounts do get checkpointed.

The token loader and the 3-7 s request pacing are inlined below so this folder
has no dependency on sibling files.
"""

from __future__ import annotations

import os
import re

from instagrapi import Client
from instagrapi.exceptions import (
    ClientError,
    ClientForbiddenError,
    LoginRequired,
    MediaNotFound,
    PleaseWaitFewMinutes,
    PrivateAccount,
    UserNotFound,
)

_USERNAME_RE = re.compile(r"[A-Za-z0-9._]{1,30}")


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

# instagrapi sleeps a random delay in this range between requests.
PACE_RANGE = [
    float(os.environ.get("PACE_MIN", 3)),
    float(os.environ.get("PACE_MAX", 7)),
]


class IGScrapeError(RuntimeError):
    """Any failure to retrieve data from Instagram."""


def clean_username(username: str) -> str:
    username = username.strip().lstrip("@").strip().rstrip("/")
    if "/" in username:
        username = username.rstrip("/").split("/")[-1]
    if not _USERNAME_RE.fullmatch(username):
        raise ValueError(f"invalid Instagram username: {username!r}")
    return username


_client: Client | None = None


def get_client() -> Client:
    """Return a cached, logged-in instagrapi Client."""
    global _client
    if _client is not None:
        return _client

    sessionid = os.environ.get("IG_SESSIONID")
    if not sessionid:
        raise IGScrapeError(
            "no IG_SESSIONID - add it to tokens.conf (see src/instagram/tokens.md)"
        )

    cl = Client()
    cl.delay_range = PACE_RANGE
    try:
        cl.login_by_sessionid(sessionid)
    except (ClientError, LoginRequired) as exc:
        raise IGScrapeError(
            f"Instagram rejected IG_SESSIONID ({exc}); log in again and refresh it"
        ) from exc

    _client = cl
    return cl


def wrap_errors(fn, *args, **kwargs):
    """Run an instagrapi call, translating its exceptions to IGScrapeError."""
    try:
        return fn(*args, **kwargs)
    except (UserNotFound, MediaNotFound) as exc:
        raise IGScrapeError(str(exc) or "not found") from exc
    except PrivateAccount as exc:
        raise IGScrapeError(f"private account: {exc}") from exc
    except PleaseWaitFewMinutes as exc:
        raise IGScrapeError(f"Instagram rate limit: {exc}") from exc
    except (ClientForbiddenError, LoginRequired) as exc:
        raise IGScrapeError(
            f"Instagram blocked the request ({exc}); the session may be flagged"
        ) from exc
    except ClientError as exc:
        raise IGScrapeError(str(exc)) from exc
