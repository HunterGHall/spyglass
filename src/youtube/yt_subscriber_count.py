"""Get a YouTube channel's subscriber count (rounded, as YouTube reports it).

    python yt_subscriber_count.py @MrBeast
"""

from __future__ import annotations

import sys

import yt_common as yt
from yt_channel import get_channel


def get_subscriber_count(ident: str) -> tuple[int | None, str | None]:
    c = get_channel(ident)
    return c.subscribers, c.subscribers_text


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <@handle|url|UC...>", file=sys.stderr)
        return 2
    try:
        count, text = get_subscriber_count(argv[1])
    except (yt.YTScrapeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if count is None:
        print("subscriber count is hidden")
        return 0
    print(f"{argv[1]}: {count:,} subscribers" + (f" ({text})" if text else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
