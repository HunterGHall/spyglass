"""List a user's recent X (Twitter) posts with per-post engagement stats.

No developer API key. See x_common.py for the caveats.

    python x_recent_posts.py nasa
    python x_recent_posts.py nasa --limit 40 --json
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys

import x_common as xc
import datetime as dt

from x_post_stats import PostStats, _parse_ts, _views

_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
from x_profile import get_profile


def _extract_posts(payload: dict) -> list[dict]:
    """Pull the Tweet result objects out of a UserTweets timeline payload."""
    posts = []
    stack = [payload]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if cur.get("__typename") == "Tweet" and "legacy" in cur:
                posts.append(cur)
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return posts


def _to_stats(result: dict) -> PostStats:
    legacy = result["legacy"]
    tweet_id = result.get("rest_id", legacy.get("id_str", ""))
    user = xc.deep_find(result.get("core", {}), "result") or {}
    user_legacy = user.get("legacy") or {}
    screen_name = user_legacy.get("screen_name", "i")
    return PostStats(
        id=tweet_id,
        url=f"https://x.com/{screen_name}/status/{tweet_id}",
        author=screen_name,
        author_name=user_legacy.get("name", ""),
        text=legacy.get("full_text", ""),
        created_at=_parse_ts(legacy.get("created_at")),
        lang=legacy.get("lang"),
        views=_views(result),
        likes=int(legacy.get("favorite_count", 0)),
        reposts=int(legacy.get("retweet_count", 0)),
        quotes=int(legacy.get("quote_count", 0)),
        replies=int(legacy.get("reply_count", 0)),
        bookmarks=int(legacy.get("bookmark_count", 0)),
    )


def get_recent_posts(
    username: str, limit: int = 20, *, include_replies: bool = False, session=None
) -> list[PostStats]:
    username = xc.clean_username(username)
    session = session or xc.new_session()

    user_id = get_profile(username, session=session).id

    payload = xc.graphql(
        session,
        "UserTweets",
        {
            "userId": user_id,
            "count": min(max(limit, 1), 100),
            "includePromotedContent": False,
            "withQuickPromoteEligibilityTweetFields": True,
            "withVoice": True,
            "withV2Timeline": True,
        },
    )

    seen: set[str] = set()
    out: list[PostStats] = []
    for result in _extract_posts(payload):
        legacy = result["legacy"]
        tweet_id = result.get("rest_id", legacy.get("id_str", ""))
        if tweet_id in seen:
            continue
        seen.add(tweet_id)
        if not include_replies and legacy.get("in_reply_to_status_id_str"):
            continue
        out.append(_to_stats(result))

    out.sort(key=lambda p: p.created_at or _EPOCH, reverse=True)
    return out[:limit]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="List a user's recent posts + stats.")
    ap.add_argument("username")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--replies", action="store_true", help="include replies")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        posts = get_recent_posts(
            args.username, args.limit, include_replies=args.replies
        )
    except (xc.XScrapeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([p.as_dict() for p in posts], indent=2))
        return 0

    if not xc.have_login() and posts and posts[0].created_at:
        age_min = (
            dt.datetime.now(dt.timezone.utc) - posts[0].created_at
        ).total_seconds() / 60
        # Between ~15 min and 12 h of lag is the logged-out cache; more than
        # that usually just means the account posts infrequently.
        if 15 < age_min < 12 * 60:
            print(
                f"(logged-out timeline may lag; newest post here is "
                f"{int(age_min)} min old. Set X_AUTH_TOKEN / X_CT0 for "
                f"real-time — see x_common.py)\n"
            )

    for p in posts:
        when = p.created_at.strftime("%Y-%m-%d %H:%M") if p.created_at else "?"
        text = " ".join(p.text.split())
        if len(text) > 80:
            text = text[:77] + "..."
        views = f"{p.views:,}" if p.views is not None else "n/a"
        print(f"[{when}] {text}")
        print(
            f"    views {views} · likes {p.likes:,} · reposts {p.reposts:,} "
            f"· replies {p.replies:,} · {p.url}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
