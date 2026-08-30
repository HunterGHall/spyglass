"""List an Instagram user's recent posts with per-post engagement stats.

    python ig_recent_posts.py nasa
    python ig_recent_posts.py nasa --limit 40 --json

Needs IG_SESSIONID in tokens.conf (see tokens.md). The list view carries like
and comment counts; video play counts are only filled in with --deep (one
extra request per post, paced 3-7 s apart).
"""

from __future__ import annotations

import argparse
import json
import sys

import ig_common as ig
from ig_post_stats import PostStats, media_stats_from_model
from ig_profile import fetch_user


def get_recent_posts(
    username: str, limit: int = 12, *, deep: bool = False, cl=None
) -> list[PostStats]:
    cl = cl or ig.get_client()
    user = fetch_user(username, cl)
    medias = ig.wrap_errors(cl.user_medias_v1, user.pk, amount=max(limit, 1))

    out = [media_stats_from_model(m) for m in medias[:limit]]

    if deep:
        for post in out:
            try:
                full = ig.wrap_errors(cl.media_info_v1, post.media_id)
            except ig.IGScrapeError:
                continue
            fresh = media_stats_from_model(full)
            post.views = fresh.views
            post.likes = fresh.likes
            post.comments = fresh.comments
            post.video_duration = fresh.video_duration

    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="List a user's recent posts + stats.")
    ap.add_argument("username")
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--deep", action="store_true", help="also fetch video play counts")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        posts = get_recent_posts(args.username, args.limit, deep=args.deep)
    except (ig.IGScrapeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([p.as_dict() for p in posts], indent=2))
        return 0

    for p in posts:
        when = p.posted_at.strftime("%Y-%m-%d %H:%M") if p.posted_at else "?"
        text = " ".join(p.caption.split())
        if len(text) > 80:
            text = text[:77] + "..."
        likes = f"{p.likes:,}" if p.likes is not None else "hidden"
        views = f" · views {p.views:,}" if p.views is not None else ""
        print(f"[{when}] ({p.product_type or p.kind}) {text}")
        print(f"    likes {likes} · comments {p.comments:,}{views} · {p.url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
