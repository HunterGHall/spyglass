"""Fetch public stats for a single Reddit post (submission).

Score, upvote ratio, comment count, awards, crossposts, plus the flags
(locked, stickied, NSFW, spoiler, OC) and content type.

    python reddit_post_stats.py https://www.reddit.com/r/python/comments/abc123/title/
    python reddit_post_stats.py abc123 --json
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import re
import sys

import reddit_common as rc


@dataclasses.dataclass
class PostStats:
    id: str
    url: str
    permalink: str
    title: str
    author: str
    subreddit: str
    created: dt.datetime | None
    age_hours: float | None
    score: int
    upvote_ratio: float | None
    ups: int
    num_comments: int
    num_crossposts: int
    total_awards: int
    gilded: int
    kind: str            # self | link | image | video | gallery
    domain: str
    over_18: bool
    spoiler: bool
    locked: bool
    stickied: bool
    is_original_content: bool
    edited: bool
    selftext: str

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["created"] = self.created.isoformat() if self.created else None
        return d


def _post_id(value: str) -> str:
    value = value.strip()
    if "reddit.com" in value or "redd.it" in value:
        m = re.search(r"comments/([a-z0-9]{4,10})", value) or re.search(
            r"redd\.it/([a-z0-9]{4,10})", value
        )
        if m:
            return m.group(1)
    if re.fullmatch(r"t3_[a-z0-9]+", value):
        return value[3:]
    if re.fullmatch(r"[a-z0-9]{4,10}", value):
        return value
    raise ValueError(f"could not find a post id in: {value!r}")


def _kind(d: dict) -> str:
    if d.get("is_self"):
        return "self"
    if d.get("is_gallery"):
        return "gallery"
    if d.get("is_video"):
        return "video"
    hint = d.get("post_hint", "")
    if hint:
        return hint.replace(":", " ")
    return "link"


def get_post_stats(post: str) -> PostStats:
    pid = _post_id(post)
    data = rc.api_get(f"/comments/{pid}", limit=1)
    try:
        d = data[0]["data"]["children"][0]["data"]
    except (KeyError, IndexError, TypeError) as exc:
        raise rc.RedditError(f"unexpected response for post {pid}") from exc

    created = None
    try:
        created = dt.datetime.fromtimestamp(
            float(d["created_utc"]), dt.timezone.utc
        )
    except (KeyError, TypeError, ValueError):
        pass
    age_hours = (
        round((dt.datetime.now(dt.timezone.utc) - created).total_seconds() / 3600, 1)
        if created
        else None
    )

    return PostStats(
        id=pid,
        url=d.get("url_overridden_by_dest") or d.get("url", ""),
        permalink="https://www.reddit.com" + d.get("permalink", ""),
        title=d.get("title", ""),
        author=d.get("author", "[deleted]"),
        subreddit=d.get("subreddit", ""),
        created=created,
        age_hours=age_hours,
        score=int(d.get("score", 0)),
        upvote_ratio=d.get("upvote_ratio"),
        ups=int(d.get("ups", 0)),
        num_comments=int(d.get("num_comments", 0)),
        num_crossposts=int(d.get("num_crossposts", 0)),
        total_awards=int(d.get("total_awards_received", 0)),
        gilded=int(d.get("gilded", 0)),
        kind=_kind(d),
        domain=d.get("domain", ""),
        over_18=bool(d.get("over_18")),
        spoiler=bool(d.get("spoiler")),
        locked=bool(d.get("locked")),
        stickied=bool(d.get("stickied")),
        is_original_content=bool(d.get("is_original_content")),
        edited=bool(d.get("edited")),
        selftext=d.get("selftext", "") or "",
    )


def _print_human(p: PostStats) -> None:
    print(f"{p.title}")
    print(f"by u/{p.author} in r/{p.subreddit}")
    when = p.created.strftime("%Y-%m-%d %H:%M UTC") if p.created else "?"
    print(f"{when}" + (f"  ({p.age_hours}h ago)" if p.age_hours else ""))
    print(f"{p.permalink}\n")
    ratio = f"{p.upvote_ratio * 100:.0f}% upvoted" if p.upvote_ratio else ""
    print(f"  score:       {p.score:,}   {ratio}")
    print(f"  comments:    {p.num_comments:,}")
    if p.num_crossposts:
        print(f"  crossposts:  {p.num_crossposts:,}")
    if p.total_awards:
        print(f"  awards:      {p.total_awards:,}")
    print(f"  type:        {p.kind} ({p.domain})")
    flags = [n for n, v in (
        ("NSFW", p.over_18), ("spoiler", p.spoiler), ("locked", p.locked),
        ("stickied", p.stickied), ("OC", p.is_original_content),
        ("edited", p.edited)) if v]
    if flags:
        print(f"  flags:       {', '.join(flags)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Get stats for one Reddit post.")
    ap.add_argument("post", help="post id, t3_ fullname, or permalink URL")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        stats = get_post_stats(args.post)
    except (rc.RedditError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(stats.as_dict(), indent=2))
    else:
        _print_human(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
