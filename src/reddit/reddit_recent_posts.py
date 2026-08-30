"""List recent posts from a Reddit user or subreddit, with stats.

    python reddit_recent_posts.py spez
    python reddit_recent_posts.py u/spez --comments
    python reddit_recent_posts.py r/python --sort top --time week --limit 25 --json
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import sys

import reddit_common as rc


@dataclasses.dataclass
class Item:
    kind: str            # post | comment
    id: str
    permalink: str
    title: str
    subreddit: str
    author: str
    created: dt.datetime | None
    score: int
    num_comments: int | None
    upvote_ratio: float | None
    over_18: bool
    body: str            # selftext for posts, text for comments

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["created"] = self.created.isoformat() if self.created else None
        return d


def _ts(v) -> dt.datetime | None:
    try:
        return dt.datetime.fromtimestamp(float(v), dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def _row(child: dict) -> Item:
    k = child.get("kind")
    d = child.get("data", {})
    is_comment = k == "t1"
    return Item(
        kind="comment" if is_comment else "post",
        id=d.get("id", ""),
        permalink="https://www.reddit.com" + d.get("permalink", ""),
        title=d.get("link_title", "") if is_comment else d.get("title", ""),
        subreddit=d.get("subreddit", ""),
        author=d.get("author", ""),
        created=_ts(d.get("created_utc")),
        score=int(d.get("score", 0)),
        num_comments=None if is_comment else int(d.get("num_comments", 0)),
        upvote_ratio=d.get("upvote_ratio"),
        over_18=bool(d.get("over_18")),
        body=(d.get("body") if is_comment else d.get("selftext", "")) or "",
    )


def get_recent(
    ref: str,
    limit: int = 20,
    *,
    comments: bool = False,
    sort: str = "new",
    time: str = "all",
) -> list[Item]:
    ref = ref.strip()
    is_sub = ref.lower().startswith(("r/", "/r/")) or "/r/" in ref

    if is_sub:
        name = rc.clean_name(ref, "subreddit")
        path = f"/r/{name}/{sort}"
        params = {"limit": min(limit, 100), "t": time}
    else:
        name = rc.clean_name(ref, "user")
        path = f"/user/{name}/{'comments' if comments else 'submitted'}"
        params = {"limit": min(limit, 100), "sort": sort, "t": time}

    data = rc.api_get(path, **params)
    children = data.get("data", {}).get("children", []) if isinstance(data, dict) else []
    return [_row(c) for c in children][:limit]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Recent posts from a user or subreddit.")
    ap.add_argument("ref", help="username, u/name, or r/subreddit")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--comments", action="store_true", help="user comments not posts")
    ap.add_argument("--sort", default="new",
                    help="new|hot|top|controversial (subreddit) / new|top|hot (user)")
    ap.add_argument("--time", default="all", help="hour|day|week|month|year|all")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        items = get_recent(
            args.ref, args.limit, comments=args.comments,
            sort=args.sort, time=args.time,
        )
    except (rc.RedditError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([i.as_dict() for i in items], indent=2))
        return 0

    for i in items:
        when = i.created.strftime("%Y-%m-%d") if i.created else "?"
        text = " ".join((i.title or i.body).split())[:80]
        extra = f" · {i.num_comments:,} comments" if i.num_comments is not None else ""
        print(f"[{when}] r/{i.subreddit} · {i.score:,} pts{extra}")
        print(f"    {text}")
        print(f"    {i.permalink}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
