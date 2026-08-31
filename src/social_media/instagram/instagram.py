"""Instagram: profiles, follower counts, follower lists, post stats, recent posts.

Everything for Instagram in one file, on top of
[instagrapi](https://github.com/subzeroid/instagrapi), which speaks the private
mobile API. Needs a real `sessionid` cookie in tokens.conf as `IG_SESSIONID`
(see src/instagram/tokens.md). Use a throwaway account - against Instagram's ToS.

    python instagram.py count     <user>
    python instagram.py profile   <user> [--json]
    python instagram.py followers <user> [--following] [--limit N] [--json]
    python instagram.py post      <shortcode|url> [--json]
    python instagram.py recent    <user> [--limit N] [--deep] [--json]

Importable: get_profile, get_follower_count, get_followers, get_following,
get_post_stats, get_recent_posts.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import re
import sys

from instagrapi import Client
from instagrapi.exceptions import (
    ClientError,
    ClientForbiddenError,
    LoginRequired,
    MediaNotFound,
    PleaseWaitFewMinutes,
    PrivateAccount,
    UserNotFound,
)

_USERNAME_RE = re.compile(r"[A-Za-z0-9._]{1,30}")
_KIND = {1: "photo", 2: "video", 8: "carousel"}


# ========================================================================== #
# tokens.conf loader
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

# instagrapi sleeps a random delay in this range between requests.
PACE_RANGE = [float(os.environ.get("PACE_MIN", 3)), float(os.environ.get("PACE_MAX", 7))]


class IGError(RuntimeError):
    """Any failure to retrieve data from Instagram."""


def clean_username(username: str) -> str:
    username = username.strip().lstrip("@").strip().rstrip("/")
    if "/" in username:
        username = username.rstrip("/").split("/")[-1]
    if not _USERNAME_RE.fullmatch(username):
        raise ValueError(f"invalid Instagram username: {username!r}")
    return username


_client: Client | None = None


def client() -> Client:
    global _client
    if _client is not None:
        return _client
    sessionid = os.environ.get("IG_SESSIONID")
    if not sessionid:
        raise IGError("no IG_SESSIONID - add it to tokens.conf (see src/instagram/tokens.md)")
    cl = Client()
    cl.delay_range = PACE_RANGE
    try:
        cl.login_by_sessionid(sessionid)
    except (ClientError, LoginRequired) as exc:
        raise IGError(f"Instagram rejected IG_SESSIONID ({exc}); log in again and refresh it") from exc
    _client = cl
    return cl


def _wrap(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (UserNotFound, MediaNotFound) as exc:
        raise IGError(str(exc) or "not found") from exc
    except PrivateAccount as exc:
        raise IGError(f"private account: {exc}") from exc
    except PleaseWaitFewMinutes as exc:
        raise IGError(f"Instagram rate limit: {exc}") from exc
    except (ClientForbiddenError, LoginRequired) as exc:
        raise IGError(f"Instagram blocked the request ({exc}); the session may be flagged") from exc
    except ClientError as exc:
        raise IGError(str(exc)) from exc


# ========================================================================== #
# profile
# ========================================================================== #

@dataclasses.dataclass
class Profile:
    id: str
    username: str
    full_name: str
    bio: str
    external_url: str | None
    category: str | None
    address: str | None
    is_private: bool
    is_verified: bool
    is_business: bool
    followers: int
    following: int
    posts: int
    profile_pic_url: str | None

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def _fetch_user(username: str):
    return _wrap(client().user_info_by_username_v1, clean_username(username))


def _address(user) -> str | None:
    parts = [getattr(user, "address_street", "") or "", getattr(user, "city_name", "") or "",
             getattr(user, "zip", "") or ""]
    return ", ".join(p for p in parts if p) or None


def get_profile(username: str) -> Profile:
    u = _fetch_user(username)
    return Profile(
        id=str(u.pk), username=u.username, full_name=u.full_name or "",
        bio=u.biography or "",
        external_url=str(u.external_url) if u.external_url else None,
        category=getattr(u, "category_name", None) or getattr(u, "category", None) or None,
        address=_address(u), is_private=bool(u.is_private), is_verified=bool(u.is_verified),
        is_business=bool(getattr(u, "is_business", False)),
        followers=int(u.follower_count), following=int(u.following_count),
        posts=int(u.media_count),
        profile_pic_url=str(getattr(u, "profile_pic_url_hd", None) or u.profile_pic_url),
    )


def get_follower_count(username: str) -> int:
    return int(_fetch_user(username).follower_count)


# ========================================================================== #
# follower / following list
# ========================================================================== #

@dataclasses.dataclass
class Follower:
    pk: str
    username: str
    full_name: str
    is_private: bool
    is_verified: bool
    profile_pic_url: str | None

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def get_followers(username: str, limit: int = 100, *, following: bool = False) -> list[Follower]:
    cl = client()
    user = _fetch_user(username)
    method = cl.user_following if following else cl.user_followers
    result = _wrap(method, str(user.pk), amount=max(limit, 0))
    users = list(result.values())
    if limit:
        users = users[:limit]
    return [Follower(str(u.pk), u.username, u.full_name or "", bool(u.is_private),
                     bool(u.is_verified),
                     str(u.profile_pic_url) if u.profile_pic_url else None) for u in users]


def get_following(username: str, limit: int = 100) -> list[Follower]:
    """List the accounts `username` follows."""
    return get_followers(username, limit, following=True)


# ========================================================================== #
# post stats
# ========================================================================== #

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


def _media_stats(m) -> PostStats:
    code = m.code or ""
    likes = None if getattr(m, "like_count", None) in (None, -1) else int(m.like_count)
    view_total = (getattr(m, "play_count", 0) or 0) or (getattr(m, "view_count", 0) or 0) or None
    loc = getattr(m, "location", None)
    return PostStats(
        shortcode=code, url=f"https://www.instagram.com/p/{code}/", media_id=str(m.pk),
        kind=_KIND.get(int(m.media_type), "post"),
        product_type=getattr(m, "product_type", None) or None,
        author=m.user.username if m.user else "",
        author_full_name=(m.user.full_name or "") if m.user else "",
        caption=m.caption_text or "", posted_at=m.taken_at,
        location=getattr(loc, "name", None) if loc else None,
        likes=likes, comments=int(getattr(m, "comment_count", 0) or 0),
        views=int(view_total) if view_total else None,
        video_duration=getattr(m, "video_duration", None) or None,
    )


def get_post_stats(post: str) -> PostStats:
    cl = client()
    pk = _wrap(cl.media_pk_from_url, post) if "/" in post else _wrap(cl.media_pk_from_code, post)
    return _media_stats(_wrap(cl.media_info_v1, pk))


# ========================================================================== #
# recent posts
# ========================================================================== #

def get_recent_posts(username: str, limit: int = 12, *, deep: bool = False) -> list[PostStats]:
    cl = client()
    user = _fetch_user(username)
    medias = _wrap(cl.user_medias_v1, user.pk, amount=max(limit, 1))
    out = [_media_stats(m) for m in medias[:limit]]
    if deep:
        for post in out:
            try:
                fresh = _media_stats(_wrap(cl.media_info_v1, post.media_id))
            except IGError:
                continue
            post.views, post.likes = fresh.views, fresh.likes
            post.comments, post.video_duration = fresh.comments, fresh.video_duration
    return out


# ========================================================================== #
# CLI
# ========================================================================== #

def _print_profile(p: Profile) -> None:
    marks = [m for m, v in (("verified", p.is_verified), ("private", p.is_private),
                            ("business", p.is_business)) if v]
    print(f"@{p.username} - {p.full_name}" + (f"  [{', '.join(marks)}]" if marks else ""))
    if p.bio:
        print(f"\n{p.bio}\n")
    if p.category:
        print(f"  category:  {p.category}")
    if p.address:
        print(f"  address:   {p.address}")
    if p.external_url:
        print(f"  link:      {p.external_url}")
    print(f"  followers: {p.followers:,}")
    print(f"  following: {p.following:,}")
    print(f"  posts:     {p.posts:,}")


def _print_post(p: PostStats) -> None:
    when = p.posted_at.strftime("%Y-%m-%d %H:%M UTC") if p.posted_at else "unknown"
    print(f"@{p.author} ({p.author_full_name}) · {p.product_type or p.kind} · {when}")
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


def _cmd_count(args) -> int:
    print(f"@{args.user.lstrip('@')}: {get_follower_count(args.user):,} followers")
    return 0


def _cmd_profile(args) -> int:
    p = get_profile(args.user)
    print(json.dumps(p.as_dict(), indent=2)) if args.json else _print_profile(p)
    return 0


def _cmd_followers(args) -> int:
    people = get_followers(args.user, args.limit, following=args.following)
    if args.json:
        print(json.dumps([p.as_dict() for p in people], indent=2))
        return 0
    label = "following" if args.following else "followers"
    print(f"@{args.user.lstrip('@')} — {len(people)} {label} shown\n")
    for p in people:
        marks = "".join(m for m, v in (("✓", p.is_verified), ("🔒", p.is_private)) if v)
        name = f"  ({p.full_name})" if p.full_name else ""
        print(f"  @{p.username}{name} {marks}".rstrip())
    return 0


def _cmd_post(args) -> int:
    p = get_post_stats(args.post)
    print(json.dumps(p.as_dict(), indent=2)) if args.json else _print_post(p)
    return 0


def _cmd_recent(args) -> int:
    posts = get_recent_posts(args.user, args.limit, deep=args.deep)
    if args.json:
        print(json.dumps([p.as_dict() for p in posts], indent=2))
        return 0
    for p in posts:
        when = p.posted_at.strftime("%Y-%m-%d %H:%M") if p.posted_at else "?"
        text = " ".join(p.caption.split())
        text = text[:77] + "..." if len(text) > 80 else text
        likes = f"{p.likes:,}" if p.likes is not None else "hidden"
        views = f" · views {p.views:,}" if p.views is not None else ""
        print(f"[{when}] ({p.product_type or p.kind}) {text}")
        print(f"    likes {likes} · comments {p.comments:,}{views} · {p.url}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="instagram", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("count", help="follower count")
    p.add_argument("user")
    p.set_defaults(fn=_cmd_count)

    p = sub.add_parser("profile", help="user profile")
    p.add_argument("user")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_profile)

    p = sub.add_parser("followers", help="list followers (Instagram throttles this)")
    p.add_argument("user")
    p.add_argument("--following", action="store_true", help="list who they follow")
    p.add_argument("--limit", type=int, default=100, help="0 = no cap (risky)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_followers)

    p = sub.add_parser("post", help="stats for one post / reel")
    p.add_argument("post", help="shortcode or post/reel URL")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_post)

    p = sub.add_parser("recent", help="recent posts + stats")
    p.add_argument("user")
    p.add_argument("--limit", type=int, default=12)
    p.add_argument("--deep", action="store_true", help="also fetch video play counts")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_recent)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (IGError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
