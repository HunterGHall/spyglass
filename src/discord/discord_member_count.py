"""Get a Discord server's member count from an invite.

    python discord_member_count.py https://discord.gg/python
"""

from __future__ import annotations

import sys

import discord_common as dc
from discord_server import get_server


def get_member_count(invite: str) -> tuple[str, int | None, int | None]:
    s = get_server(invite)
    return s.name, s.members, s.online


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <invite url or code>", file=sys.stderr)
        return 2
    try:
        name, members, online = get_member_count(argv[1])
    except (dc.DiscordError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if members is None:
        print(f"{name}: member count not available for this invite")
        return 0
    extra = f" ({online:,} online)" if online is not None else ""
    print(f"{name}: {members:,} members{extra}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
