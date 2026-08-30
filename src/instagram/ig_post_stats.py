"""Fetch public engagement stats for a single Instagram post / reel.

Likes, comments, video plays/views, caption, author, timestamp, place tag.

    python ig_post_stats.py https://www.instagram.com/p/SHORTCODE/
    python ig_post_stats.py SHORTCODE --json

Needs IG_SESSIONID in tokens.conf (see tokens.md).
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import sys

import ig_common as ig

_KIND = {1: "photo", 2: "video", 8: "carousel"}


@dataclasses.dataclass
class PostStats:
    shortcode: str
    url: str
    media_id: str
    kind: str
    product_type: str | None
    author: str
    author_full_name: str
    caption: str
    posted_at: dt.datetime | None
    location: str | None
    likes: int | None
    comments: int
    views: int | None
    video_duration: float | None

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["posted_at"] = self.posted_at.isoformat() if self.posted_at else None
        return d


def media_stats_from_model(m) -> PostStats:
    code = m.code or ""
    likes = None if getattr(m, "like_count", None) in (None, -1) else int(m.like_count)
    plays = getattr(m, "play_count", 0) or 0
    views = getattr(m, "view_count", 0) or 0
    view_total = plays or views or None
    loc = getattr(m, "location", None)
    return PostStats(
        shortcode=code,
        url=f"https://www.instagram.com/p/{code}/",
        media_id=str(m.pk),
        kind=_KIND.get(int(m.media_type), "post"),
        product_type=getattr(m, "product_type", None) or None,
        author=m.user.username if m.user else "",
        author_full_name=(m.user.full_name or "") if m.user else "",
        caption=m.caption_text or "",
        posted_at=m.taken_at,
        location=getattr(loc, "name", None) if loc else None,
        likes=likes,
        comments=int(getattr(m, "comment_count", 0) or 0),
        views=int(view_total) if view_total else None,
        video_duration=getattr(m, "video_duration", None) or None,
    )


def get_post_stats(post: str, cl=None) -> PostStats:
    cl = cl or ig.get_client()
    pk = ig.wrap_errors(cl.media_pk_from_url, post) if "/" in post else (
        ig.wrap_errors(cl.media_pk_from_code, post)
    )
    media = ig.wrap_errors(cl.media_info_v1, pk)
    return media_stats_from_model(media)


def _print_human(p: PostStats) -> None:
    when = p.posted_at.strftime("%Y-%m-%d %H:%M UTC") if p.posted_at else "unknown"
    kind = p.product_type or p.kind
    print(f"@{p.author} ({p.author_full_name}) · {kind} · {when}")
    if p.location:
        print(f"location: {p.location}")
    print(f"{p.url}\n")
    if p.caption:
        print(" ".join(p.caption.split())[:280] + "\n")
    print(f"  likes:    {p.likes:,}" if p.likes is not None else "  likes:    hidden")
    print(f"  comments: {p.comments:,}")
    if p.views is not None:
        print(f"  views:    {p.views:,}")
    if p.video_duration:
        print(f"  duration: {p.video_duration:.0f}s")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Get stats for one Instagram post.")
    ap.add_argument("post", help="shortcode or post/reel URL")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        stats = get_post_stats(args.post)
    except (ig.IGScrapeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(stats.as_dict(), indent=2))
    else:
        _print_human(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
