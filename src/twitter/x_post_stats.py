"""Fetch public engagement stats for a single X (Twitter) post.

Views, likes, reposts, replies, quotes, bookmarks, plus the text and author.
No developer API key. See x_common.py for the caveats.

    python x_post_stats.py 20
    python x_post_stats.py https://x.com/NASA/status/1234567890 --json

Note: the view count is only returned for posts where X exposes it (most
reasonably recent posts). Older posts often come back with views = None.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import sys

import x_common as xc

_TWITTER_TS = "%a %b %d %H:%M:%S %z %Y"


@dataclasses.dataclass
class PostStats:
    id: str
    url: str
    author: str
    author_name: str
    text: str
    created_at: dt.datetime | None
    lang: str | None
    views: int | None
    likes: int
    reposts: int
    quotes: int
    replies: int
    bookmarks: int

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["created_at"] = self.created_at.isoformat() if self.created_at else None
        return d


def _parse_ts(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.strptime(value, _TWITTER_TS)
    except ValueError:
        return None


def _views(result: dict) -> int | None:
    views = result.get("views") or {}
    count = views.get("count")
    return int(count) if count is not None else None


def get_post_stats(post: str, session=None) -> PostStats:
    tweet_id = xc.tweet_id_from(post)
    session = session or xc.new_session()

    payload = xc.graphql(
        session,
        "TweetResultByRestId",
        {
            "tweetId": tweet_id,
            "withCommunity": False,
            "includePromotedContent": False,
            "withVoice": False,
        },
    )

    result = xc.deep_find(payload, "tweetResult") or {}
    result = result.get("result") or {}
    typename = result.get("__typename")
    if typename == "TweetUnavailable":
        raise xc.XScrapeError(f"post {tweet_id} is unavailable (deleted or private)")
    if typename == "TweetTombstone":
        text = xc.deep_find(result, "text") or "tombstoned"
        raise xc.XScrapeError(f"post {tweet_id} not viewable: {text}")

    # Visibility-limited posts wrap the real tweet one level down.
    if typename == "TweetWithVisibilityResults":
        result = result.get("tweet") or result

    legacy = result.get("legacy")
    if not legacy:
        raise xc.XScrapeError(f"unexpected payload shape for post {tweet_id}")

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


def _print_human(p: PostStats) -> None:
    when = p.created_at.strftime("%Y-%m-%d %H:%M UTC") if p.created_at else "unknown"
    print(f"@{p.author} ({p.author_name}) · {when}")
    print(f"{p.url}\n")
    print(p.text + "\n")
    print(f"  views:     {p.views:,}" if p.views is not None else "  views:     n/a")
    print(f"  likes:     {p.likes:,}")
    print(f"  reposts:   {p.reposts:,}")
    print(f"  quotes:    {p.quotes:,}")
    print(f"  replies:   {p.replies:,}")
    print(f"  bookmarks: {p.bookmarks:,}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Get engagement stats for an X post.")
    ap.add_argument("post", help="post id or status URL")
    ap.add_argument("--json", action="store_true", help="print raw JSON")
    args = ap.parse_args(argv)

    try:
        stats = get_post_stats(args.post)
    except (xc.XScrapeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(stats.as_dict(), indent=2))
    else:
        _print_human(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
