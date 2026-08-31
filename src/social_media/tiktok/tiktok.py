"""TikTok: profile metadata, follower / following counts, single-video stats.

Reads the JSON blob TikTok server-renders into its own profile and video pages
(`__UNIVERSAL_DATA_FOR_REHYDRATION__`) - no API key, no account.

    python tiktok.py count   <user>
    python tiktok.py profile <user> [--json]
    python tiktok.py post    <id|url> [--json]

TikTok cryptographically signs the requests that back follower / following
*lists* and the video feed, so those are out of reach without a browser-grade
signer - this file stops at what the page HTML already carries. Video pages
are additionally bot-gated from datacenter IPs; drop a real browser `Cookie`
header into tokens.conf as `TIKTOK_COOKIE` if `post` returns "not found".
Unofficial, undocumented, against TikTok's ToS.

Importable: get_profile, get_follower_count, get_following_count, get_post_stats.
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

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_USERNAME_RE = re.compile(r"[A-Za-z0-9._]{1,24}")
_BLOB_RE = re.compile(
    r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">(.+?)</script>',
    re.DOTALL,
)


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


class TikTokError(RuntimeError):
    """Any failure to retrieve or parse data from TikTok."""


_session: PacedSession | None = None


def session() -> PacedSession:
    global _session
    if _session is not None:
        return _session
    s = PacedSession()
    s.headers.update({
        "User-Agent": _UA,
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.tiktok.com/",
    })
    if os.environ.get("TIKTOK_COOKIE"):
        s.headers["Cookie"] = os.environ["TIKTOK_COOKIE"]
    _session = s
    return s


def clean_username(username: str) -> str:
    username = username.strip().lstrip("@").rstrip("/")
    if "tiktok.com/@" in username:
        username = username.split("tiktok.com/@", 1)[1].split("/")[0].split("?")[0]
    if not _USERNAME_RE.fullmatch(username):
        raise ValueError(f"invalid TikTok username: {username!r}")
    return username


def video_id_from(value: str) -> str:
    value = value.strip()
    if value.isdigit():
        return value
    m = re.search(r"/(?:video|photo)/(\d+)", value) or re.search(r"\b(\d{15,25})\b", value)
    if m:
        return m.group(1)
    if re.search(r"(vm\.|vt\.)?tiktok\.com/(t/)?[\w@.]+", value):
        # short link - follow the redirect to the canonical URL
        try:
            resp = session().get(value if value.startswith("http") else f"https://{value}",
                                  allow_redirects=True, timeout=20)
        except requests.RequestException as exc:
            raise TikTokError(f"could not resolve short link: {exc}") from exc
        m = re.search(r"/(?:video|photo)/(\d+)", resp.url)
        if m:
            return m.group(1)
    raise ValueError(f"could not find a TikTok video id in: {value!r}")


def _scope(url: str) -> dict:
    resp = session().get(url, params={"lang": "en"}, timeout=20)
    if resp.status_code == 429:
        raise TikTokError("rate limited by TikTok (HTTP 429); back off and retry")
    if "captcha" in resp.text.lower() and "__UNIVERSAL_DATA" not in resp.text:
        raise TikTokError("TikTok served a CAPTCHA wall; set TIKTOK_COOKIE or try later")
    resp.raise_for_status()
    m = _BLOB_RE.search(resp.text)
    if not m:
        raise TikTokError("page carried no rehydration blob (layout change or bot wall)")
    try:
        return json.loads(m.group(1)).get("__DEFAULT_SCOPE__", {})
    except json.JSONDecodeError as exc:
        raise TikTokError(f"could not parse the rehydration blob: {exc}") from exc


def _ts(value) -> dt.datetime | None:
    try:
        return dt.datetime.fromtimestamp(int(value), dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def _int(*values) -> int:
    for v in values:
        if v not in (None, ""):
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
    return 0


# ========================================================================== #
# profile
# ========================================================================== #

@dataclasses.dataclass
class Profile:
    id: str
    sec_uid: str
    username: str
    nickname: str
    signature: str
    bio_link: str | None
    verified: bool
    private: bool
    region: str | None
    created: dt.datetime | None
    followers: int
    following: int
    likes: int
    videos: int
    friends: int
    avatar_url: str | None

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["created"] = self.created.isoformat() if self.created else None
        return d


_STATUS = {
    10221: "user not found or banned",
    10222: "this account is private",
    10202: "account not found",
}


def _user_detail(username: str) -> tuple[dict, dict, dict]:
    scope = _scope(f"https://www.tiktok.com/@{clean_username(username)}")
    detail = scope.get("webapp.user-detail") or {}
    status = detail.get("statusCode")
    info = detail.get("userInfo") or {}
    if status not in (0, None) or not info.get("user"):
        raise TikTokError(_STATUS.get(status, f"TikTok returned status {status} for @{username}"))
    return info.get("user") or {}, info.get("stats") or {}, info.get("statsV2") or {}


def get_profile(username: str) -> Profile:
    user, stats, stats2 = _user_detail(username)
    link = (user.get("bioLink") or {}).get("link") or None
    return Profile(
        id=str(user.get("id", "")),
        sec_uid=user.get("secUid", ""),
        username=user.get("uniqueId", ""),
        nickname=user.get("nickname", "") or "",
        signature=user.get("signature", "") or "",
        bio_link=link,
        verified=bool(user.get("verified")),
        private=bool(user.get("privateAccount")),
        region=user.get("region") or None,
        created=_ts(user.get("createTime")),
        followers=_int(stats2.get("followerCount"), stats.get("followerCount")),
        following=_int(stats2.get("followingCount"), stats.get("followingCount")),
        likes=_int(stats2.get("heartCount"), stats.get("heartCount"), stats.get("heart")),
        videos=_int(stats2.get("videoCount"), stats.get("videoCount")),
        friends=_int(stats2.get("friendCount"), stats.get("friendCount")),
        avatar_url=user.get("avatarLarger") or user.get("avatarMedium") or None,
    )


def get_follower_count(username: str) -> int:
    _, stats, stats2 = _user_detail(username)
    return _int(stats2.get("followerCount"), stats.get("followerCount"))


def get_following_count(username: str) -> int:
    _, stats, stats2 = _user_detail(username)
    return _int(stats2.get("followingCount"), stats.get("followingCount"))


# ========================================================================== #
# single-video stats
# ========================================================================== #

@dataclasses.dataclass
class PostStats:
    id: str
    url: str
    author: str
    author_name: str
    description: str
    created_at: dt.datetime | None
    duration: int | None
    music: str | None
    views: int
    likes: int
    comments: int
    shares: int
    saves: int
    hashtags: list[str]

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["created_at"] = self.created_at.isoformat() if self.created_at else None
        return d


def get_post_stats(post: str) -> PostStats:
    vid = video_id_from(post)
    url = post if post.startswith("http") and "/video/" in post else \
        f"https://www.tiktok.com/@i/video/{vid}"
    scope = _scope(url)
    detail = scope.get("webapp.video-detail") or {}
    status = detail.get("statusCode")
    item = (detail.get("itemInfo") or {}).get("itemStruct") or {}
    if status not in (0, None) or not item:
        raise TikTokError(
            f"TikTok did not return video {vid} "
            f"(deleted / private, or this IP is bot-gated - set TIKTOK_COOKIE)")
    stats = item.get("statsV2") or item.get("stats") or {}
    author = item.get("author") or {}
    challenges = item.get("textExtra") or []
    tags = [c.get("hashtagName") for c in challenges if c.get("hashtagName")]
    return PostStats(
        id=str(item.get("id", vid)),
        url=f"https://www.tiktok.com/@{author.get('uniqueId', 'i')}/video/{item.get('id', vid)}",
        author=author.get("uniqueId", ""),
        author_name=author.get("nickname", "") or "",
        description=item.get("desc", "") or "",
        created_at=_ts(item.get("createTime")),
        duration=(item.get("video") or {}).get("duration"),
        music=(item.get("music") or {}).get("title") or None,
        views=_int(stats.get("playCount")),
        likes=_int(stats.get("diggCount")),
        comments=_int(stats.get("commentCount")),
        shares=_int(stats.get("shareCount")),
        saves=_int(stats.get("collectCount")),
        hashtags=tags,
    )


# ========================================================================== #
# CLI
# ========================================================================== #

def _print_profile(p: Profile) -> None:
    marks = [m for m, v in (("verified", p.verified), ("private", p.private)) if v]
    print(f"@{p.username} — {p.nickname}" + (f"  [{', '.join(marks)}]" if marks else ""))
    if p.signature:
        print(f"\n{p.signature}\n")
    if p.bio_link:
        print(f"  link:      {p.bio_link}")
    if p.region:
        print(f"  region:    {p.region}")
    if p.created:
        print(f"  created:   {p.created:%Y-%m-%d}")
    print(f"  followers: {p.followers:,}")
    print(f"  following: {p.following:,}")
    print(f"  likes:     {p.likes:,}")
    print(f"  videos:    {p.videos:,}")


def _print_post(p: PostStats) -> None:
    when = p.created_at.strftime("%Y-%m-%d %H:%M UTC") if p.created_at else "unknown"
    print(f"@{p.author} ({p.author_name}) · {when}")
    print(f"{p.url}\n")
    if p.description:
        print(" ".join(p.description.split())[:280] + "\n")
    print(f"  views:    {p.views:,}")
    print(f"  likes:    {p.likes:,}")
    print(f"  comments: {p.comments:,}")
    print(f"  shares:   {p.shares:,}")
    print(f"  saves:    {p.saves:,}")
    if p.duration:
        print(f"  duration: {p.duration}s")
    if p.music:
        print(f"  music:    {p.music}")


def _cmd_count(args) -> int:
    print(f"@{clean_username(args.user)}: {get_follower_count(args.user):,} followers")
    return 0


def _cmd_profile(args) -> int:
    p = get_profile(args.user)
    print(json.dumps(p.as_dict(), indent=2)) if args.json else _print_profile(p)
    return 0


def _cmd_post(args) -> int:
    p = get_post_stats(args.post)
    print(json.dumps(p.as_dict(), indent=2)) if args.json else _print_post(p)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tiktok", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("count", help="follower count")
    p.add_argument("user")
    p.set_defaults(fn=_cmd_count)

    p = sub.add_parser("profile", help="profile + follower / following / like counts")
    p.add_argument("user")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_profile)

    p = sub.add_parser("post", help="stats for one video (IP-gated on datacenters)")
    p.add_argument("post", help="video id or URL")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_post)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (TikTokError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
