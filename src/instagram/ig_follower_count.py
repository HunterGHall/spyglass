"""Get an Instagram user's follower count.

    python ig_follower_count.py nasa

Needs IG_SESSIONID in tokens.conf (see tokens.md).
"""

from __future__ import annotations

import sys

import ig_common as ig
from ig_profile import fetch_user


def get_follower_count(username: str) -> int:
    return int(fetch_user(username).follower_count)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <username>", file=sys.stderr)
        return 2
    try:
        count = get_follower_count(argv[1])
    except (ig.IGScrapeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"@{argv[1].lstrip('@')}: {count:,} followers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
