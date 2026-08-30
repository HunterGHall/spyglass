"""Discord: server lookup, member counts, user profiles, live widget.

Everything for Discord in one file. Server / member / widget lookups use the
public invite and widget endpoints (no token). `user` needs a token in
tokens.conf: `DISCORD_TOKEN` (bot) for the basics, plus `DISCORD_USER_TOKEN`
for a user's bio, pronouns and connected accounts. See src/discord/tokens.md.

    python discord.py server  <invite url|code> [--json]
    python discord.py members <invite url|code>
    python discord.py user    <user id> [--json]
    python discord.py widget  <server id | invite> [--json]

Importable: get_server, get_member_count, get_user, get_widget.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import random
import re
import sys
import threading
import time

import requests

API = "https://discord.com/api/v10"
_UA = "DiscordBot (https://github.com/, 0.1)"


# ========================================================================== #
# tokens.conf loader  +  paced session
# ========================================================================== #

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


# ========================================================================== #
# auth
# ========================================================================== #

def have_user_token() -> bool:
    tok = os.environ.get("DISCORD_TOKEN", "").strip()
    return bool(os.environ.get("DISCORD_USER_TOKEN") or tok.lower().startswith("mfa."))


def _auth_header(require_user: bool = False) -> dict:
    user_tok = os.environ.get("DISCORD_USER_TOKEN", "").strip()
    bot_tok = os.environ.get("DISCORD_TOKEN", "").strip()
    if not user_tok and bot_tok.lower().startswith("mfa."):
        user_tok, bot_tok = bot_tok, ""

    if require_user:
        if not user_tok:
            raise DiscordError(
                "this needs a Discord USER token - set DISCORD_USER_TOKEN in "
                "tokens.conf (a bot token can't read profiles; see src/discord/tokens.md)")
        return {"Authorization": user_tok}
    if bot_tok:
        return {"Authorization": bot_tok if bot_tok.lower().startswith("bot ")
                else f"Bot {bot_tok}"}
    if user_tok:
        return {"Authorization": user_tok}
    raise DiscordError("this needs a token - set DISCORD_TOKEN (bot) or "
                       "DISCORD_USER_TOKEN in tokens.conf (see src/discord/tokens.md)")


def api_get(path: str, *, auth: bool | str = False, **params) -> dict:
    headers = _auth_header(require_user=(auth == "user")) if auth else {}
    resp = session().get(API + path, params=params or None, headers=headers, timeout=20)
    if resp.status_code == 401:
        raise DiscordError("Discord rejected the token (HTTP 401)")
    if resp.status_code == 403:
        raise DiscordError("forbidden (HTTP 403)")
    if resp.status_code == 404:
        raise DiscordError("not found (HTTP 404) - bad id/invite, or widget off")
    if resp.status_code == 429:
        raise DiscordError(f"rate limited by Discord (retry after "
                           f"{resp.json().get('retry_after', '?')}s)")
    resp.raise_for_status()
    return resp.json()


def widget_json(guild_id: str) -> dict:
    resp = session().get(f"https://discord.com/api/guilds/{guild_id}/widget.json", timeout=20)
    if resp.status_code == 403:
        raise DiscordError(f"widget is disabled for guild {guild_id}")
    if resp.status_code == 404:
        raise DiscordError(f"guild {guild_id} not found")
    resp.raise_for_status()
    return resp.json()


# ========================================================================== #
# helpers
# ========================================================================== #

_INVITE_RE = re.compile(r"(?:discord(?:\.gg|app\.com/invite|\.com/invite)/)([A-Za-z0-9-]+)")
_SNOWFLAKE_EPOCH = 1420070400000

VERIFICATION_LEVELS = ["none", "low", "medium", "high", "very high"]
NSFW_LEVELS = ["default", "explicit", "safe", "age restricted"]
USER_FLAGS = {
    1 << 0: "Discord Staff", 1 << 1: "Partnered Server Owner",
    1 << 2: "HypeSquad Events", 1 << 3: "Bug Hunter Level 1",
    1 << 6: "HypeSquad Bravery", 1 << 7: "HypeSquad Brilliance",
    1 << 8: "HypeSquad Balance", 1 << 9: "Early Supporter",
    1 << 14: "Bug Hunter Level 2", 1 << 16: "Verified Bot",
    1 << 17: "Early Verified Bot Developer", 1 << 18: "Moderator Programs Alumni",
    1 << 22: "Active Developer",
}
_CONN = {
    "battlenet": "Battle.net", "ebay": "eBay", "epicgames": "Epic Games",
    "facebook": "Facebook", "github": "GitHub", "instagram": "Instagram",
    "leagueoflegends": "League of Legends", "paypal": "PayPal",
    "playstation": "PlayStation", "reddit": "Reddit", "riotgames": "Riot Games",
    "roblox": "Roblox", "spotify": "Spotify", "skype": "Skype", "steam": "Steam",
    "tiktok": "TikTok", "twitch": "Twitch", "twitter": "X / Twitter",
    "xbox": "Xbox", "youtube": "YouTube", "domain": "Domain",
    "bungie": "Bungie.net", "crunchyroll": "Crunchyroll",
}
_PREMIUM = {0: None, 1: "Nitro Classic", 2: "Nitro", 3: "Nitro Basic"}


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
        return time.strftime("%Y-%m-%d",
                             time.gmtime(((int(snowflake) >> 22) + _SNOWFLAKE_EPOCH) / 1000))
    except (TypeError, ValueError):
        return "?"


def cdn(kind: str, holder_id: str, hash_: str | None, ext: str = "png") -> str | None:
    if not hash_:
        return None
    if hash_.startswith("a_"):
        ext = "gif"
    return f"https://cdn.discordapp.com/{kind}/{holder_id}/{hash_}.{ext}?size=1024"


def _decode_flags(flags: int) -> list[str]:
    return [name for bit, name in USER_FLAGS.items() if flags & bit]


# ========================================================================== #
# server
# ========================================================================== #

@dataclasses.dataclass
class Server:
    id: str
    name: str
    description: str | None
    created: str
    members: int | None
    online: int | None
    verification_level: str | None
    nsfw_level: str | None
    premium_tier: int | None
    premium_subscribers: int | None
    vanity_url: str | None
    features: list[str]
    icon_url: str | None
    banner_url: str | None
    splash_url: str | None
    invite_channel: str | None
    invite_url: str

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def get_server(invite: str) -> Server:
    code = invite_code(invite)
    inv = api_get(f"/invites/{code}", with_counts="true", with_expiration="true")
    g = inv.get("guild") or {}
    gid = g.get("id", "")
    online = inv.get("approximate_presence_count")
    if online is None and gid:
        try:
            online = widget_json(gid).get("presence_count")
        except DiscordError:
            pass
    vtier, ntier = g.get("verification_level"), g.get("nsfw_level")
    ch = inv.get("channel") or {}
    return Server(
        id=gid, name=g.get("name", ""), description=g.get("description"),
        created=snowflake_time(gid), members=inv.get("approximate_member_count"),
        online=online,
        verification_level=(VERIFICATION_LEVELS[vtier] if isinstance(vtier, int)
                            and vtier < len(VERIFICATION_LEVELS) else None),
        nsfw_level=(NSFW_LEVELS[ntier] if isinstance(ntier, int)
                    and ntier < len(NSFW_LEVELS) else None),
        premium_tier=g.get("premium_tier"),
        premium_subscribers=g.get("premium_subscription_count"),
        vanity_url=(f"https://discord.gg/{g['vanity_url_code']}"
                    if g.get("vanity_url_code") else None),
        features=g.get("features", []),
        icon_url=cdn("icons", gid, g.get("icon")),
        banner_url=cdn("banners", gid, g.get("banner")),
        splash_url=cdn("splashes", gid, g.get("splash")),
        invite_channel=("#" + ch["name"]) if ch.get("name") else None,
        invite_url=f"https://discord.gg/{code}",
    )


def get_member_count(invite: str) -> tuple[str, int | None, int | None]:
    s = get_server(invite)
    return s.name, s.members, s.online


# ========================================================================== #
# user
# ========================================================================== #

@dataclasses.dataclass
class Connection:
    type: str
    label: str
    name: str
    verified: bool


@dataclasses.dataclass
class User:
    id: str
    username: str
    global_name: str | None
    legacy_tag: str | None
    is_bot: bool
    is_system: bool
    created: str
    badges: list[str]
    accent_color: str | None
    banner_color: str | None
    avatar_url: str | None
    banner_url: str | None
    avatar_decoration_url: str | None
    server_tag: str | None
    bio: str | None
    pronouns: str | None
    premium: str | None
    connections: list[Connection]
    profile_badges: list[str]
    profile_error: str | None = None

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def _avatar(uid: str, d: dict) -> str:
    a = cdn("avatars", uid, d.get("avatar"))
    if a:
        return a
    disc = int(d.get("discriminator") or 0)
    idx = disc % 5 if disc else (int(uid) >> 22) % 6
    return f"https://cdn.discordapp.com/embed/avatars/{idx}.png"


def _decoration(d: dict) -> str | None:
    dec = (d.get("avatar_decoration_data") or {}).get("asset")
    return (f"https://cdn.discordapp.com/avatar-decoration-presets/{dec}.png?size=1024"
            if dec else None)


def get_user(user_id: str) -> User:
    user_id = user_id.strip()
    if not re.fullmatch(r"\d{15,25}", user_id):
        raise ValueError(f"not a Discord user id: {user_id!r}")

    d = api_get(f"/users/{user_id}", auth=True)

    profile: dict = {}
    profile_error: str | None = None
    if have_user_token():
        try:
            profile = api_get(f"/users/{user_id}/profile", auth="user",
                              with_mutual_guilds="false")
        except DiscordError as exc:
            profile_error = str(exc)
    else:
        profile_error = "no DISCORD_USER_TOKEN set"

    up = profile.get("user_profile") or {}
    up_user = profile.get("user") or {}
    conns = [Connection(c.get("type", ""), _CONN.get(c.get("type", ""), c.get("type", "").title()),
                        c.get("name", ""), bool(c.get("verified")))
             for c in profile.get("connected_accounts") or []]
    flags = d.get("public_flags", 0) or 0
    accent = d.get("accent_color")
    prem = profile.get("premium_type")
    tag = (d.get("primary_guild") or d.get("clan") or {}).get("tag")

    return User(
        id=user_id, username=d.get("username", ""), global_name=d.get("global_name"),
        legacy_tag=(f"{d['username']}#{d['discriminator']}"
                    if d.get("discriminator") not in (None, "0")
                    else profile.get("legacy_username")),
        is_bot=bool(d.get("bot")), is_system=bool(d.get("system")),
        created=snowflake_time(user_id), badges=_decode_flags(flags),
        accent_color=f"#{accent:06x}" if isinstance(accent, int) else None,
        banner_color=d.get("banner_color") if isinstance(d.get("banner_color"), str) else None,
        avatar_url=_avatar(user_id, d),
        banner_url=cdn("banners", user_id, d.get("banner") or up_user.get("banner")),
        avatar_decoration_url=_decoration(d), server_tag=tag,
        bio=(up.get("bio") or up_user.get("bio") or "").strip() or None,
        pronouns=up.get("pronouns") or None,
        premium=_PREMIUM.get(prem) if prem is not None else None,
        connections=conns,
        profile_badges=[b.get("description", "") for b in profile.get("badges") or []],
        profile_error=None if profile else profile_error,
    )


# ========================================================================== #
# widget
# ========================================================================== #

@dataclasses.dataclass
class Widget:
    id: str
    name: str
    instant_invite: str | None
    online: int
    members: list[dict]
    voice_channels: list[dict]

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def get_widget(server: str) -> Widget:
    server = server.strip()
    if server.isdigit():
        gid = server
    else:
        inv = api_get(f"/invites/{invite_code(server)}")
        gid = (inv.get("guild") or {}).get("id", "")
        if not gid:
            raise DiscordError("could not resolve that invite to a server id")
    w = widget_json(gid)
    chan_names = {c["id"]: c["name"] for c in w.get("channels", [])}
    voice: dict[str, list[str]] = {}
    members = []
    for m in w.get("members", []):
        members.append({"name": m.get("username", ""), "status": m.get("status", ""),
                        "game": (m.get("game") or {}).get("name")})
        if m.get("channel_id"):
            voice.setdefault(m["channel_id"], []).append(m.get("username", ""))
    return Widget(
        id=w.get("id", gid), name=w.get("name", ""),
        instant_invite=w.get("instant_invite"),
        online=w.get("presence_count", len(members)), members=members,
        voice_channels=[{"name": chan_names.get(cid, cid), "members": names}
                        for cid, names in voice.items()],
    )


# ========================================================================== #
# CLI
# ========================================================================== #

def _print_server(s: Server) -> None:
    print(f"{s.name}   ({s.id})")
    if s.description:
        print(f"\n{s.description}\n")
    if s.members is not None:
        print(f"  members:       {s.members:,}")
    if s.online is not None:
        print(f"  online now:    {s.online:,}")
    print(f"  created:       {s.created}")
    if s.verification_level:
        print(f"  verification:  {s.verification_level}")
    if s.premium_tier is not None:
        subs = f" ({s.premium_subscribers} boosts)" if s.premium_subscribers else ""
        print(f"  boost tier:    {s.premium_tier}{subs}")
    if s.vanity_url:
        print(f"  vanity url:    {s.vanity_url}")
    if s.nsfw_level and s.nsfw_level != "default":
        print(f"  nsfw level:    {s.nsfw_level}")
    if s.features:
        print(f"  features:      {', '.join(sorted(s.features))}")


def _print_user(u: User) -> None:
    name = u.global_name or u.username
    print(f"{name}  (@{u.username})" + ("  [BOT]" if u.is_bot else ""))
    print(f"  id:          {u.id}")
    print(f"  created:     {u.created}")
    if u.server_tag:
        print(f"  server tag:  {u.server_tag}")
    badges = u.badges + [b for b in u.profile_badges if b not in u.badges]
    if badges:
        print(f"  badges:      {', '.join(badges)}")
    if u.premium:
        print(f"  nitro:       {u.premium}")
    if u.pronouns:
        print(f"  pronouns:    {u.pronouns}")
    if u.bio:
        print(f"\n  {u.bio.strip()}\n")
    if u.connections:
        print("  connections:")
        for c in u.connections:
            print(f"    {c.label}: {c.name}" + (" ✓" if c.verified else ""))
    if u.accent_color:
        print(f"  accent:      {u.accent_color}")
    print(f"  avatar:      {u.avatar_url}")
    if u.avatar_decoration_url:
        print(f"  decoration:  {u.avatar_decoration_url}")
    if u.banner_url:
        print(f"  banner:      {u.banner_url}")
    if u.profile_error and not u.is_bot:
        if u.profile_error == "no DISCORD_USER_TOKEN set":
            print("\n  (bio / connections need DISCORD_USER_TOKEN - see tokens.md)")
        else:
            print(f"\n  (couldn't load profile: {u.profile_error} - the user token "
                  f"is likely wrong or expired; see tokens.md)")


def _print_widget(w: Widget) -> None:
    print(f"{w.name}  ({w.id})")
    print(f"  online now: {w.online:,}")
    if w.instant_invite:
        print(f"  invite:     {w.instant_invite}")
    if w.voice_channels:
        print("\n  in voice:")
        for vc in w.voice_channels:
            print(f"    🔊 {vc['name']}: {', '.join(vc['members'])}")
    playing = [m for m in w.members if m["game"]]
    if playing:
        print("\n  playing:")
        for m in playing[:15]:
            print(f"    {m['name']} — {m['game']}")
    sample = [m for m in w.members if not m["game"]][:15]
    if sample:
        print("\n  online (sample): " + ", ".join(m["name"] for m in sample))


def _cmd_server(args) -> int:
    s = get_server(args.invite)
    print(json.dumps(s.as_dict(), indent=2)) if args.json else _print_server(s)
    return 0


def _cmd_members(args) -> int:
    name, members, online = get_member_count(args.invite)
    if members is None:
        print(f"{name}: member count not available for this invite")
        return 0
    extra = f" ({online:,} online)" if online is not None else ""
    print(f"{name}: {members:,} members{extra}")
    return 0


def _cmd_user(args) -> int:
    u = get_user(args.user_id)
    print(json.dumps(u.as_dict(), indent=2)) if args.json else _print_user(u)
    return 0


def _cmd_widget(args) -> int:
    w = get_widget(args.server)
    print(json.dumps(w.as_dict(), indent=2)) if args.json else _print_widget(w)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="discord", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("server", help="server info from an invite")
    p.add_argument("invite")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_server)

    p = sub.add_parser("members", help="member count from an invite")
    p.add_argument("invite")
    p.set_defaults(fn=_cmd_members)

    p = sub.add_parser("user", help="user profile by id (needs a token)")
    p.add_argument("user_id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_user)

    p = sub.add_parser("widget", help="live widget (needs widget enabled)")
    p.add_argument("server", help="server id or invite")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_widget)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (DiscordError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
