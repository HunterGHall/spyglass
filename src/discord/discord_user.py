"""Look up a Discord user by ID (needs a bot token).

    python discord_user.py 80351110224678912 --json

Set DISCORD_TOKEN in tokens.conf (see tokens.md). Only public fields come back:
username, display name, avatar/banner, accent colour, badges, bot flag,
account creation date (from the ID). No mutual-server data.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys

import discord_common as dc


@dataclasses.dataclass
class User:
    id: str
    username: str
    global_name: str | None
    is_bot: bool
    is_system: bool
    created: str
    badges: list[str]
    accent_color: int | None
    avatar_url: str | None
    banner_url: str | None

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def get_user(user_id: str) -> User:
    user_id = user_id.strip()
    if not re.fullmatch(r"\d{15,25}", user_id):
        raise ValueError(f"not a Discord user id: {user_id!r}")

    d = dc.api_get(f"/users/{user_id}", auth=True)
    flags = d.get("public_flags", 0) or 0
    avatar = dc.cdn("avatars", user_id, d.get("avatar"))
    if not avatar:
        idx = (int(user_id) >> 22) % 6
        avatar = f"https://cdn.discordapp.com/embed/avatars/{idx}.png"

    return User(
        id=user_id,
        username=d.get("username", ""),
        global_name=d.get("global_name"),
        is_bot=bool(d.get("bot")),
        is_system=bool(d.get("system")),
        created=dc.snowflake_time(user_id),
        badges=dc.decode_flags(flags),
        accent_color=d.get("accent_color"),
        avatar_url=avatar,
        banner_url=dc.cdn("banners", user_id, d.get("banner")),
    )


def _print_human(u: User) -> None:
    name = u.global_name or u.username
    tag = "  [BOT]" if u.is_bot else ""
    print(f"{name}  (@{u.username}){tag}")
    print(f"  id:       {u.id}")
    print(f"  created:  {u.created}")
    if u.badges:
        print(f"  badges:   {', '.join(u.badges)}")
    if u.accent_color is not None:
        print(f"  accent:   #{u.accent_color:06x}")
    if u.banner_url:
        print(f"  banner:   {u.banner_url}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Look up a Discord user by ID.")
    ap.add_argument("user_id")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        user = get_user(args.user_id)
    except (dc.DiscordError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(user.as_dict(), indent=2))
    else:
        _print_human(user)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
