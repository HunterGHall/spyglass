"""Get a GitHub user's follower count.

    python gh_follower_count.py torvalds
"""

from __future__ import annotations

import sys

import gh_common as gh
from gh_profile import get_profile


def get_follower_count(login: str) -> int:
    return get_profile(login).followers


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <login>", file=sys.stderr)
        return 2
    try:
        count = get_follower_count(argv[1])
    except (gh.GitHubError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{argv[1].lstrip('@')}: {count:,} followers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
