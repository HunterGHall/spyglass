"""Get a Reddit user's karma (the closest thing Reddit has to a follower count).

    python reddit_karma.py spez
"""

from __future__ import annotations

import sys

import reddit_common as rc
from reddit_profile import get_user_profile


def get_karma(name: str) -> dict:
    p = get_user_profile(name)
    return {
        "name": p.name,
        "total": p.total_karma,
        "link": p.link_karma,
        "comment": p.comment_karma,
        "awardee": p.awardee_karma,
        "awarder": p.awarder_karma,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <username>", file=sys.stderr)
        return 2
    try:
        k = get_karma(argv[1])
    except (rc.RedditError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"u/{k['name']}: {k['total']:,} karma "
        f"({k['link']:,} post + {k['comment']:,} comment)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
