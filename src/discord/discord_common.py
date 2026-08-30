"""Shared plumbing for the Discord lookups in this repo.

Discord has no open API for arbitrary data, but two endpoints need no auth:

    * GET /api/v10/invites/{code}?with_counts=true   - server name, member and
      online counts, description, features, verification level, from any invite
    * GET /api/guilds/{id}/widget.json               - live widget (online
      members, voice channels) IF the server has the widget enabled

`discord_user.py` needs a bot token (it calls GET /users/{id}); put it in
`tokens.conf` as `DISCORD_TOKEN`. Everything else works without one.

Token loader and 3-7 s pacing are inlined so this folder needs no siblings.
"""

from __future__ import annotations

import os
import random
import re
import threading
import time

import requests

API = "https://discord.com/api/v10"
_UA = "DiscordBot (https://github.com/, 0.1)"


# --------------------------------------------------------------------------- #
# tokens.conf loader
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
# paced session
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


class DiscordError(RuntimeError):
    """Any failure to retrieve data from Discord."""


_session: PacedSession | None = None


def session() -> PacedSession:
    global _session
    if _session is None:
        _session = PacedSession()
        _session.headers.update({"User-Agent": _UA, "Accept": "application/json"})
    return _session


def have_token() -> bool:
    return bool(os.environ.get("DISCORD_TOKEN"))


def _auth_header() -> dict:
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token:
        raise DiscordError(
            "this needs a bot token - set DISCORD_TOKEN in tokens.conf "
            "(see src/discord/tokens.md)"
        )
    if not token.lower().startswith("bot "):
        token = f"Bot {token}"
    return {"Authorization": token}


def api_get(path: str, *, auth: bool = False, **params) -> dict:
    s = session()
    headers = _auth_header() if auth else {}
    resp = s.get(API + path, params=params or None, headers=headers, timeout=20)

    if resp.status_code == 401:
        raise DiscordError("Discord rejected the token (HTTP 401)")
    if resp.status_code == 403:
        raise DiscordError("forbidden (HTTP 403) - the widget may be disabled")
    if resp.status_code == 404:
        raise DiscordError("not found (HTTP 404) - bad id/invite, or widget off")
    if resp.status_code == 429:
        retry = resp.json().get("retry_after", "?")
        raise DiscordError(f"rate limited by Discord (retry after {retry}s)")
    resp.raise_for_status()
    return resp.json()


def widget_json(guild_id: str) -> dict:
    """The guilds/{id}/widget.json endpoint lives outside /v10."""
    s = session()
    resp = s.get(
        f"https://discord.com/api/guilds/{guild_id}/widget.json", timeout=20
    )
    if resp.status_code == 403:
        raise DiscordError(f"widget is disabled for guild {guild_id}")
    if resp.status_code == 404:
        raise DiscordError(f"guild {guild_id} not found")
    resp.raise_for_status()
    return resp.json()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

_INVITE_RE = re.compile(
    r"(?:discord(?:\.gg|app\.com/invite|\.com/invite)/)([A-Za-z0-9-]+)"
)
_SNOWFLAKE_EPOCH = 1420070400000


def invite_code(value: str) -> str:
    value = value.strip()
    m = _INVITE_RE.search(value)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9-]{2,32}", value):
        return value
    raise ValueError(f"could not find an invite code in: {value!r}")


def snowflake_time(snowflake: str | int) -> str:
    try:
        ms = (int(snowflake) >> 22) + _SNOWFLAKE_EPOCH
        return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))
    except (TypeError, ValueError):
        return "?"


def cdn(kind: str, holder_id: str, hash_: str | None, ext: str = "png") -> str | None:
    if not hash_:
        return None
    if hash_.startswith("a_"):
        ext = "gif"
    return f"https://cdn.discordapp.com/{kind}/{holder_id}/{hash_}.{ext}?size=1024"


USER_FLAGS = {
    1 << 0: "Discord Staff",
    1 << 1: "Partnered Server Owner",
    1 << 2: "HypeSquad Events",
    1 << 3: "Bug Hunter Level 1",
    1 << 6: "HypeSquad Bravery",
    1 << 7: "HypeSquad Brilliance",
    1 << 8: "HypeSquad Balance",
    1 << 9: "Early Supporter",
    1 << 14: "Bug Hunter Level 2",
    1 << 16: "Verified Bot",
    1 << 17: "Early Verified Bot Developer",
    1 << 18: "Moderator Programs Alumni",
    1 << 22: "Active Developer",
}

VERIFICATION_LEVELS = ["none", "low", "medium", "high", "very high"]
NSFW_LEVELS = ["default", "explicit", "safe", "age restricted"]


def decode_flags(flags: int) -> list[str]:
    return [name for bit, name in USER_FLAGS.items() if flags & bit]
