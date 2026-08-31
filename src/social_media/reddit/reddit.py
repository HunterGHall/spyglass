"""Reddit: user karma, user / subreddit profiles, post stats, recent posts.

Everything for Reddit in one file. Uses the public `.json` endpoints, falling
back to OAuth (oauth.reddit.com) when `REDDIT_CLIENT_ID` is in tokens.conf -
needed from datacenter / VPN IPs, which Reddit 403s.

    python reddit.py karma   <user>
    python reddit.py profile <user | u/name | r/sub> [--json]
    python reddit.py post    <id | url> [--json]
    python reddit.py recent  <user | r/sub> [--comments] [--sort S] [--time T]
                                            [--limit N] [--json]

Register an app at https://www.reddit.com/prefs/apps ("script" or "installed").
Importable: get_karma, get_user_profile, get_subreddit, get_post_stats,
get_recent.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import random
import re
import sys
import threading
import time

import requests

_UA = "spyglass/0.1 (github scraper)"


# ========================================================================== #
# tokens.conf loader  +  paced session
# ========================================================================== #

def _load_tokens_conf() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        path = os.path.join(here, "tokens.conf")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key, val = key.strip(), val.strip().strip("\"'")
                    if key and val and key not in os.environ:
                        os.environ[key] = val
            return
        parent = os.path.dirname(here)
        if parent == here:
            return
        here = parent


_load_tokens_conf()


class PacedSession(requests.Session):
    _lock = threading.Lock()
    _next_at = 0.0

    def request(self, *args, **kwargs):
        lo = float(os.environ.get("PACE_MIN", 3))
        hi = float(os.environ.get("PACE_MAX", 7))
        with PacedSession._lock:
            now = time.monotonic()
            if now < PacedSession._next_at:
                time.sleep(PacedSession._next_at - now)
            PacedSession._next_at = time.monotonic() + random.uniform(lo, hi)
        return super().request(*args, **kwargs)


class RedditError(RuntimeError):
    """Any failure to retrieve data from Reddit."""


_session: PacedSession | None = None
_base = "https://www.reddit.com"


def session() -> PacedSession:
    global _session, _base
    if _session is not None:
        return _session
    s = PacedSession()
    s.headers.update({"User-Agent": _UA, "Accept": "application/json"})
    cid = os.environ.get("REDDIT_CLIENT_ID")
    if cid:
        secret = os.environ.get("REDDIT_CLIENT_SECRET", "")
        data = ({"grant_type": "client_credentials"} if secret else {
            "grant_type": "https://oauth.reddit.com/grants/installed_client",
            "device_id": "DO_NOT_TRACK_THIS_DEVICE",
        })
        resp = s.post("https://www.reddit.com/api/v1/access_token",
                      data=data, auth=(cid, secret), timeout=15)
        if resp.status_code == 401:
            raise RedditError("REDDIT_CLIENT_ID / _SECRET rejected (HTTP 401)")
        resp.raise_for_status()
        token = resp.json().get("access_token")
        if not token:
            raise RedditError(f"no access_token in response: {resp.text[:200]}")
        s.headers["Authorization"] = f"bearer {token}"
        _base = "https://oauth.reddit.com"
    _session = s
    return s


def logged_in() -> bool:
    return _base.startswith("https://oauth")


def api_get(path: str, **params):
    s = session()
    url = _base + path + ("" if logged_in() else ".json")
    params.setdefault("raw_json", 1)
    resp = s.get(url, params=params, timeout=20)
    if resp.status_code in (403, 429) and not logged_in():
        raise RedditError(
            f"Reddit blocked the request (HTTP {resp.status_code}). This IP needs "
            f"OAuth - set REDDIT_CLIENT_ID in tokens.conf (see src/reddit/tokens.md).")
    if resp.status_code == 404:
        raise RedditError("not found (HTTP 404)")
    if resp.status_code == 429:
        raise RedditError("rate limited by Reddit (HTTP 429); back off")
    resp.raise_for_status()
    return resp.json()


def clean_name(value: str, kind: str) -> str:
    v = value.strip().rstrip("/")
    if "reddit.com" in v:
        v = v.split("reddit.com", 1)[1]
    for pfx in ("/u/", "u/", "/user/", "user/", "/r/", "r/", "/"):
        if v.startswith(pfx):
            v = v[len(pfx):]
    v = v.split("/")[0].lstrip("@")
    if not v or not all(c.isalnum() or c in "_-" for c in v):
        raise ValueError(f"invalid {kind} name: {value!r}")
    return v


def _ts(v) -> dt.datetime | None:
    try:
        return dt.datetime.fromtimestamp(float(v), dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def _age(created: dt.datetime | None) -> int | None:
    return (dt.datetime.now(dt.timezone.utc) - created).days if created else None


# ========================================================================== #
# profiles
# ========================================================================== #

@dataclasses.dataclass
class UserProfile:
    kind: str
    name: str
    id: str
    created: dt.datetime | None
    age_days: int | None
    total_karma: int
    link_karma: int
    comment_karma: int
    awardee_karma: int
    awarder_karma: int
    is_gold: bool
    is_mod: bool
    is_employee: bool
    verified: bool
    has_verified_email: bool
    icon_url: str | None
    profile_title: str | None
    profile_description: str | None
    profile_subscribers: int | None

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["created"] = self.created.isoformat() if self.created else None
        return d


@dataclasses.dataclass
class Subreddit:
    kind: str
    name: str
    id: str
    title: str
    public_description: str
    created: dt.datetime | None
    age_days: int | None
    subscribers: int
    active_users: int | None
    over18: bool
    subreddit_type: str
    lang: str | None
    icon_url: str | None

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["created"] = self.created.isoformat() if self.created else None
        return d


def get_user_profile(name: str) -> UserProfile:
    name = clean_name(name, "user")
    d = api_get(f"/user/{name}/about")["data"]
    sub = d.get("subreddit") or {}
    created = _ts(d.get("created_utc"))
    return UserProfile(
        kind="user", name=d.get("name", name), id=d.get("id", ""),
        created=created, age_days=_age(created),
        total_karma=int(d.get("total_karma", 0)), link_karma=int(d.get("link_karma", 0)),
        comment_karma=int(d.get("comment_karma", 0)),
        awardee_karma=int(d.get("awardee_karma", 0)),
        awarder_karma=int(d.get("awarder_karma", 0)),
        is_gold=bool(d.get("is_gold")), is_mod=bool(d.get("is_mod")),
        is_employee=bool(d.get("is_employee")), verified=bool(d.get("verified")),
        has_verified_email=bool(d.get("has_verified_email")),
        icon_url=(d.get("icon_img") or "").split("?")[0] or None,
        profile_title=sub.get("title") or None,
        profile_description=sub.get("public_description") or None,
        profile_subscribers=sub.get("subscribers"),
    )


def get_subreddit(name: str) -> Subreddit:
    name = clean_name(name, "subreddit")
    d = api_get(f"/r/{name}/about")["data"]
    created = _ts(d.get("created_utc"))
    return Subreddit(
        kind="subreddit", name=d.get("display_name", name), id=d.get("id", ""),
        title=d.get("title", ""), public_description=d.get("public_description", ""),
        created=created, age_days=_age(created),
        subscribers=int(d.get("subscribers", 0)),
        active_users=d.get("active_user_count"), over18=bool(d.get("over18")),
        subreddit_type=d.get("subreddit_type", ""), lang=d.get("lang") or None,
        icon_url=(d.get("community_icon") or d.get("icon_img") or "").split("?")[0] or None,
    )


def get_profile(ref: str):
    if ref.strip().lower().startswith(("r/", "/r/")) or "/r/" in ref:
        return get_subreddit(ref)
    return get_user_profile(ref)


def get_karma(name: str) -> dict:
    p = get_user_profile(name)
    return {"name": p.name, "total": p.total_karma, "link": p.link_karma,
            "comment": p.comment_karma, "awardee": p.awardee_karma,
            "awarder": p.awarder_karma}


# ========================================================================== #
# post stats
# ========================================================================== #

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
    kind: str
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
            r"redd\.it/([a-z0-9]{4,10})", value)
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
    return d.get("post_hint", "").replace(":", " ") or "link"


def get_post_stats(post: str) -> PostStats:
    pid = _post_id(post)
    data = api_get(f"/comments/{pid}", limit=1)
    try:
        d = data[0]["data"]["children"][0]["data"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RedditError(f"unexpected response for post {pid}") from exc
    created = _ts(d.get("created_utc"))
    age_hours = (round((dt.datetime.now(dt.timezone.utc) - created).total_seconds() / 3600, 1)
                 if created else None)
    return PostStats(
        id=pid,
        url=d.get("url_overridden_by_dest") or d.get("url", ""),
        permalink="https://www.reddit.com" + d.get("permalink", ""),
        title=d.get("title", ""), author=d.get("author", "[deleted]"),
        subreddit=d.get("subreddit", ""), created=created, age_hours=age_hours,
        score=int(d.get("score", 0)), upvote_ratio=d.get("upvote_ratio"),
        ups=int(d.get("ups", 0)), num_comments=int(d.get("num_comments", 0)),
        num_crossposts=int(d.get("num_crossposts", 0)),
        total_awards=int(d.get("total_awards_received", 0)),
        gilded=int(d.get("gilded", 0)), kind=_kind(d), domain=d.get("domain", ""),
        over_18=bool(d.get("over_18")), spoiler=bool(d.get("spoiler")),
        locked=bool(d.get("locked")), stickied=bool(d.get("stickied")),
        is_original_content=bool(d.get("is_original_content")),
        edited=bool(d.get("edited")), selftext=d.get("selftext", "") or "",
    )


# ========================================================================== #
# recent posts
# ========================================================================== #

@dataclasses.dataclass
class Item:
    kind: str
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
    body: str

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["created"] = self.created.isoformat() if self.created else None
        return d


def _row(child: dict) -> Item:
    is_comment = child.get("kind") == "t1"
    d = child.get("data", {})
    return Item(
        kind="comment" if is_comment else "post", id=d.get("id", ""),
        permalink="https://www.reddit.com" + d.get("permalink", ""),
        title=d.get("link_title", "") if is_comment else d.get("title", ""),
        subreddit=d.get("subreddit", ""), author=d.get("author", ""),
        created=_ts(d.get("created_utc")), score=int(d.get("score", 0)),
        num_comments=None if is_comment else int(d.get("num_comments", 0)),
        upvote_ratio=d.get("upvote_ratio"), over_18=bool(d.get("over_18")),
        body=(d.get("body") if is_comment else d.get("selftext", "")) or "",
    )


def get_recent(ref: str, limit: int = 20, *, comments: bool = False,
               sort: str = "new", time: str = "all") -> list[Item]:
    is_sub = ref.strip().lower().startswith(("r/", "/r/")) or "/r/" in ref
    if is_sub:
        path = f"/r/{clean_name(ref, 'subreddit')}/{sort}"
        params = {"limit": min(limit, 100), "t": time}
    else:
        path = f"/user/{clean_name(ref, 'user')}/{'comments' if comments else 'submitted'}"
        params = {"limit": min(limit, 100), "sort": sort, "t": time}
    data = api_get(path, **params)
    children = data.get("data", {}).get("children", []) if isinstance(data, dict) else []
    return [_row(c) for c in children][:limit]


# ========================================================================== #
# CLI
# ========================================================================== #

def _print_user(p: UserProfile) -> None:
    print(f"u/{p.name}" + ("  [employee]" if p.is_employee else ""))
    if p.created:
        print(f"  created:       {p.created:%Y-%m-%d}  ({p.age_days} days ago)")
    print(f"  total karma:   {p.total_karma:,}")
    print(f"    link:        {p.link_karma:,}")
    print(f"    comment:     {p.comment_karma:,}")
    print(f"    awardee:     {p.awardee_karma:,}")
    flags = [n for n, v in (("gold", p.is_gold), ("mod", p.is_mod),
                            ("verified-email", p.has_verified_email)) if v]
    if flags:
        print(f"  flags:         {', '.join(flags)}")
    if p.profile_description:
        print(f"  profile:       {p.profile_description}")


def _print_sub(s: Subreddit) -> None:
    print(f"r/{s.name} — {s.title}" + ("  [NSFW]" if s.over18 else ""))
    if s.public_description:
        print(f"\n{s.public_description}\n")
    print(f"  subscribers:  {s.subscribers:,}")
    if s.active_users is not None:
        print(f"  online now:   {s.active_users:,}")
    if s.created:
        print(f"  created:      {s.created:%Y-%m-%d}  ({s.age_days} days ago)")
    print(f"  type:         {s.subreddit_type}")


def _print_post(p: PostStats) -> None:
    print(p.title)
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
    flags = [n for n, v in (("NSFW", p.over_18), ("spoiler", p.spoiler),
                            ("locked", p.locked), ("stickied", p.stickied),
                            ("OC", p.is_original_content), ("edited", p.edited)) if v]
    if flags:
        print(f"  flags:       {', '.join(flags)}")


def _cmd_karma(args) -> int:
    k = get_karma(args.user)
    print(f"u/{k['name']}: {k['total']:,} karma "
          f"({k['link']:,} post + {k['comment']:,} comment)")
    return 0


def _cmd_profile(args) -> int:
    p = get_profile(args.ref)
    if args.json:
        print(json.dumps(p.as_dict(), indent=2))
    elif isinstance(p, Subreddit):
        _print_sub(p)
    else:
        _print_user(p)
    return 0


def _cmd_post(args) -> int:
    p = get_post_stats(args.post)
    print(json.dumps(p.as_dict(), indent=2)) if args.json else _print_post(p)
    return 0


def _cmd_recent(args) -> int:
    items = get_recent(args.ref, args.limit, comments=args.comments,
                       sort=args.sort, time=args.time)
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="reddit", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("karma", help="user karma")
    p.add_argument("user")
    p.set_defaults(fn=_cmd_karma)

    p = sub.add_parser("profile", help="user or subreddit profile")
    p.add_argument("ref", help="username, u/name, or r/subreddit")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_profile)

    p = sub.add_parser("post", help="stats for one post")
    p.add_argument("post", help="post id, t3_ fullname, or permalink URL")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_post)

    p = sub.add_parser("recent", help="recent posts from a user or subreddit")
    p.add_argument("ref")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--comments", action="store_true", help="user comments not posts")
    p.add_argument("--sort", default="new")
    p.add_argument("--time", default="all", help="hour|day|week|month|year|all")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_recent)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (RedditError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
