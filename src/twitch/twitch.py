"""Twitch: channel profiles, follower counts, live status, VOD & clip stats.

Everything for Twitch in one file. Uses the public GraphQL endpoint the web
player itself calls (gql.twitch.tv) with Twitch's well-known web Client-ID -
no developer app, no OAuth key.

    python twitch.py followers <login>
    python twitch.py profile   <login> [--json]
    python twitch.py stream    <login> [--json]
    python twitch.py video     <id|url> [--json]
    python twitch.py clip      <slug|url> [--json]
    python twitch.py recent    <login> [--clips] [--limit N] [--json]

Twitch pulled the public follower / following *lists* in 2022, so `followers`
is a count only - there is no per-account follower list to scrape. Unofficial,
undocumented, against Twitch's ToS; for anything reliable use the Helix API
(dev.twitch.tv).

Importable: get_profile, get_follower_count, get_stream, get_video_stats,
get_clip_stats, get_recent_videos, get_recent_clips.
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

GQL_URL = "https://gql.twitch.tv/gql"
# The Client-ID the logged-out twitch.tv web app ships with. Public, static.
WEB_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_LOGIN_RE = re.compile(r"[A-Za-z0-9_]{1,25}")
_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)


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


class TwitchError(RuntimeError):
    """Any failure to retrieve or parse data from Twitch."""


_session: PacedSession | None = None


def session() -> PacedSession:
    global _session
    if _session is not None:
        return _session
    s = PacedSession()
    s.headers.update({
        "User-Agent": _UA,
        "Client-ID": os.environ.get("TWITCH_CLIENT_ID") or WEB_CLIENT_ID,
        "Accept": "application/json",
        "Origin": "https://www.twitch.tv",
        "Referer": "https://www.twitch.tv/",
    })
    oauth = os.environ.get("TWITCH_OAUTH", "").replace("oauth:", "").strip()
    if oauth:
        s.headers["Authorization"] = f"OAuth {oauth}"
    _session = s
    return s


def gql(query: str, variables: dict | None = None):
    body = {"query": query, "variables": variables or {}}
    resp = session().post(GQL_URL, json=body, timeout=20)
    if resp.status_code == 429:
        raise TwitchError("rate limited by Twitch (HTTP 429); back off and retry")
    resp.raise_for_status()
    payload = resp.json()
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    data = payload.get("data") or {}
    errors = payload.get("errors") or []
    if errors and not any(data.values()):
        raise TwitchError(f"Twitch GraphQL error: {errors[0].get('message', 'unknown')}")
    return data


# ========================================================================== #
# helpers
# ========================================================================== #

def clean_login(value: str) -> str:
    value = value.strip().lstrip("@").rstrip("/")
    if "twitch.tv/" in value:
        value = value.split("twitch.tv/", 1)[1].split("/")[0].split("?")[0]
    value = value.lower()
    if not _LOGIN_RE.fullmatch(value):
        raise ValueError(f"invalid Twitch login: {value!r}")
    return value


def video_id_from(value: str) -> str:
    value = value.strip()
    if value.isdigit():
        return value
    m = re.search(r"videos/(\d+)", value) or re.search(r"\b(\d{8,12})\b", value)
    if not m:
        raise ValueError(f"could not find a Twitch video id in: {value!r}")
    return m.group(1)


def clip_slug_from(value: str) -> str:
    value = value.strip().rstrip("/")
    for marker in ("/clip/", "clips.twitch.tv/"):
        if marker in value:
            value = value.split(marker, 1)[1]
            break
    value = value.split("?")[0].split("/")[0]
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError(f"could not find a Twitch clip slug in: {value!r}")
    return value


def _parse_ts(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _hms(seconds: int | None) -> str:
    if not seconds:
        return "0:00"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ========================================================================== #
# channel profile
# ========================================================================== #

@dataclasses.dataclass
class Channel:
    id: str
    login: str
    display_name: str
    description: str
    created_at: str | None
    partner: bool
    affiliate: bool
    followers: int
    primary_color: str | None
    avatar_url: str | None
    offline_image_url: str | None
    is_live: bool
    title: str | None
    game: str | None
    last_broadcast_at: str | None

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


_PROFILE_Q = """
query($login: String!) {
  user(login: $login) {
    id login displayName description createdAt primaryColorHex
    profileImageURL(width: 300) offlineImageURL
    roles { isPartner isAffiliate }
    followers { totalCount }
    stream { id title game { name } }
    lastBroadcast { startedAt }
    broadcastSettings { title game { name } }
  }
}
"""


def get_profile(login: str) -> Channel:
    user = gql(_PROFILE_Q, {"login": clean_login(login)}).get("user")
    if not user:
        raise TwitchError(f"channel not found: {login}")
    roles = user.get("roles") or {}
    stream = user.get("stream") or None
    settings = user.get("broadcastSettings") or {}
    live_game = (stream or {}).get("game") or {}
    off_game = settings.get("game") or {}
    return Channel(
        id=user.get("id", ""),
        login=user.get("login", ""),
        display_name=user.get("displayName", ""),
        description=user.get("description") or "",
        created_at=user.get("createdAt"),
        partner=bool(roles.get("isPartner")),
        affiliate=bool(roles.get("isAffiliate")),
        followers=int((user.get("followers") or {}).get("totalCount", 0)),
        primary_color=(f"#{user['primaryColorHex']}" if user.get("primaryColorHex")
                       else None),
        avatar_url=user.get("profileImageURL"),
        offline_image_url=user.get("offlineImageURL") or None,
        is_live=stream is not None,
        title=(stream or {}).get("title") or settings.get("title") or None,
        game=live_game.get("name") or off_game.get("name") or None,
        last_broadcast_at=(user.get("lastBroadcast") or {}).get("startedAt"),
    )


def get_follower_count(login: str) -> int:
    data = gql("query($l:String!){user(login:$l){followers{totalCount}}}",
               {"l": clean_login(login)})
    user = data.get("user")
    if not user:
        raise TwitchError(f"channel not found: {login}")
    return int((user.get("followers") or {}).get("totalCount", 0))


# ========================================================================== #
# live stream
# ========================================================================== #

@dataclasses.dataclass
class Stream:
    login: str
    live: bool
    title: str | None
    game: str | None
    viewers: int | None
    started_at: str | None
    uptime_minutes: int | None
    stream_type: str | None

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def get_stream(login: str) -> Stream:
    login = clean_login(login)
    data = gql("""
    query($l: String!) {
      user(login: $l) {
        stream { title viewersCount createdAt type game { name } }
        broadcastSettings { title game { name } }
      }
    }""", {"l": login})
    user = data.get("user")
    if not user:
        raise TwitchError(f"channel not found: {login}")
    s = user.get("stream")
    if not s:
        settings = user.get("broadcastSettings") or {}
        return Stream(login, False, settings.get("title") or None,
                      (settings.get("game") or {}).get("name"),
                      None, None, None, None)
    started = _parse_ts(s.get("createdAt"))
    uptime = (int((dt.datetime.now(dt.timezone.utc) - started).total_seconds() // 60)
              if started else None)
    return Stream(
        login=login, live=True, title=s.get("title"),
        game=(s.get("game") or {}).get("name"),
        viewers=s.get("viewersCount"), started_at=s.get("createdAt"),
        uptime_minutes=uptime, stream_type=s.get("type"),
    )


# ========================================================================== #
# VOD / video stats
# ========================================================================== #

@dataclasses.dataclass
class Video:
    id: str
    url: str
    title: str
    game: str | None
    views: int
    duration_seconds: int
    duration: str
    published_at: str | None
    kind: str | None
    creator: str | None
    creator_name: str | None

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def _video_from(node: dict) -> Video:
    length = int(node.get("lengthSeconds") or 0)
    owner = node.get("owner") or {}
    return Video(
        id=node.get("id", ""),
        url=f"https://www.twitch.tv/videos/{node.get('id', '')}",
        title=node.get("title") or "",
        game=(node.get("game") or {}).get("name"),
        views=int(node.get("viewCount") or 0),
        duration_seconds=length, duration=_hms(length),
        published_at=node.get("publishedAt") or node.get("createdAt"),
        kind=node.get("broadcastType"),
        creator=owner.get("login"), creator_name=owner.get("displayName"),
    )


def get_video_stats(video: str) -> Video:
    vid = video_id_from(video)
    data = gql("""
    query($id: ID!) {
      video(id: $id) {
        id title viewCount lengthSeconds publishedAt createdAt broadcastType
        game { name } owner { login displayName }
      }
    }""", {"id": vid})
    node = data.get("video")
    if not node:
        raise TwitchError(f"video {vid} not found (deleted, sub-only, or expired)")
    return _video_from(node)


def get_recent_videos(login: str, limit: int = 10) -> list[Video]:
    data = gql("""
    query($l: String!, $n: Int!) {
      user(login: $l) {
        videos(first: $n, type: ARCHIVE, sort: TIME) {
          edges { node {
            id title viewCount lengthSeconds publishedAt createdAt broadcastType
            game { name } owner { login displayName }
          } }
        }
      }
    }""", {"l": clean_login(login), "n": max(1, min(limit, 100))})
    user = data.get("user")
    if not user:
        raise TwitchError(f"channel not found: {login}")
    edges = ((user.get("videos") or {}).get("edges")) or []
    return [_video_from(e["node"]) for e in edges if e.get("node")][:limit]


# ========================================================================== #
# clip stats
# ========================================================================== #

@dataclasses.dataclass
class Clip:
    slug: str
    url: str
    title: str
    game: str | None
    views: int
    duration_seconds: int
    created_at: str | None
    broadcaster: str | None
    broadcaster_name: str | None
    curator: str | None
    video_id: str | None

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def _clip_from(node: dict) -> Clip:
    b = node.get("broadcaster") or {}
    return Clip(
        slug=node.get("slug", ""),
        url=f"https://clips.twitch.tv/{node.get('slug', '')}",
        title=node.get("title") or "",
        game=(node.get("game") or {}).get("name"),
        views=int(node.get("viewCount") or 0),
        duration_seconds=int(node.get("durationSeconds") or 0),
        created_at=node.get("createdAt"),
        broadcaster=b.get("login"), broadcaster_name=b.get("displayName"),
        curator=(node.get("curator") or {}).get("login"),
        video_id=(node.get("video") or {}).get("id"),
    )


def get_clip_stats(clip: str) -> Clip:
    slug = clip_slug_from(clip)
    data = gql("""
    query($slug: ID!) {
      clip(slug: $slug) {
        slug title viewCount durationSeconds createdAt
        game { name } broadcaster { login displayName }
        curator { login } video { id }
      }
    }""", {"slug": slug})
    node = data.get("clip")
    if not node:
        raise TwitchError(f"clip {slug!r} not found")
    return _clip_from(node)


def get_recent_clips(login: str, limit: int = 10, *, period: str = "LAST_MONTH") -> list[Clip]:
    data = gql("""
    query($l: String!, $n: Int!, $p: ClipsPeriod!) {
      user(login: $l) {
        clips(first: $n, criteria: { period: $p, sort: VIEWS_DESC }) {
          edges { node {
            slug title viewCount durationSeconds createdAt
            game { name } broadcaster { login displayName }
            curator { login } video { id }
          } }
        }
      }
    }""", {"l": clean_login(login), "n": max(1, min(limit, 100)), "p": period})
    user = data.get("user")
    if not user:
        raise TwitchError(f"channel not found: {login}")
    edges = ((user.get("clips") or {}).get("edges")) or []
    return [_clip_from(e["node"]) for e in edges if e.get("node")][:limit]


# ========================================================================== #
# CLI
# ========================================================================== #

def _print_profile(c: Channel) -> None:
    kind = "partner" if c.partner else ("affiliate" if c.affiliate else "")
    print(f"{c.display_name} ({c.login})" + (f"  [{kind}]" if kind else ""))
    print(f"  https://www.twitch.tv/{c.login}")
    if c.description:
        print(f"\n{c.description}\n")
    print(f"  followers:  {c.followers:,}")
    if c.is_live:
        print(f"  status:     LIVE — {c.title or ''}")
    else:
        print("  status:     offline")
    if c.game:
        print(f"  game:       {c.game}")
    if c.created_at:
        print(f"  created:    {c.created_at[:10]}")
    if c.last_broadcast_at:
        print(f"  last live:  {c.last_broadcast_at[:10]}")
    if c.primary_color:
        print(f"  color:      {c.primary_color}")


def _print_stream(s: Stream) -> None:
    if not s.live:
        print(f"{s.login} is offline"
              + (f"  (last title: {s.title})" if s.title else ""))
        return
    up = f"{s.uptime_minutes // 60}h{s.uptime_minutes % 60:02d}m" if s.uptime_minutes else "?"
    print(f"{s.login} is LIVE")
    print(f"  title:    {s.title or ''}")
    print(f"  game:     {s.game or '?'}")
    print(f"  viewers:  {s.viewers:,}" if s.viewers is not None else "  viewers:  ?")
    print(f"  uptime:   {up}")


def _print_video(v: Video) -> None:
    who = v.creator_name or v.creator or "?"
    print(f"{v.title}\n  by {who} · {v.kind or 'video'} · {v.url}")
    if v.published_at:
        print(f"  published: {v.published_at[:10]}")
    print(f"  views:     {v.views:,}")
    print(f"  length:    {v.duration}")
    if v.game:
        print(f"  game:      {v.game}")


def _print_clip(c: Clip) -> None:
    print(f"{c.title}\n  {c.url}")
    print(f"  broadcaster: {c.broadcaster_name or c.broadcaster or '?'}")
    if c.curator:
        print(f"  clipped by:  {c.curator}")
    if c.created_at:
        print(f"  created:     {c.created_at[:10]}")
    print(f"  views:       {c.views:,}")
    print(f"  length:      {_hms(c.duration_seconds)}")
    if c.game:
        print(f"  game:        {c.game}")


def _cmd_followers(args) -> int:
    print(f"{clean_login(args.login)}: {get_follower_count(args.login):,} followers")
    return 0


def _cmd_profile(args) -> int:
    c = get_profile(args.login)
    print(json.dumps(c.as_dict(), indent=2)) if args.json else _print_profile(c)
    return 0


def _cmd_stream(args) -> int:
    s = get_stream(args.login)
    print(json.dumps(s.as_dict(), indent=2)) if args.json else _print_stream(s)
    return 0


def _cmd_video(args) -> int:
    v = get_video_stats(args.video)
    print(json.dumps(v.as_dict(), indent=2)) if args.json else _print_video(v)
    return 0


def _cmd_clip(args) -> int:
    c = get_clip_stats(args.clip)
    print(json.dumps(c.as_dict(), indent=2)) if args.json else _print_clip(c)
    return 0


def _cmd_recent(args) -> int:
    if args.clips:
        rows = get_recent_clips(args.login, args.limit)
        if args.json:
            print(json.dumps([c.as_dict() for c in rows], indent=2))
            return 0
        for c in rows:
            print(f"[{(c.created_at or '?')[:10]}] {c.views:,} views · {_hms(c.duration_seconds)}"
                  f" · {c.title}")
            print(f"    {c.url}")
        return 0
    rows = get_recent_videos(args.login, args.limit)
    if args.json:
        print(json.dumps([v.as_dict() for v in rows], indent=2))
        return 0
    for v in rows:
        print(f"[{(v.published_at or '?')[:10]}] {v.views:,} views · {v.duration} · {v.title}")
        print(f"    {v.url}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="twitch", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("followers", help="follower count (Twitch hides the list)")
    p.add_argument("login")
    p.set_defaults(fn=_cmd_followers)

    p = sub.add_parser("profile", help="channel profile + live status")
    p.add_argument("login")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_profile)

    p = sub.add_parser("stream", help="live status: title, game, viewers, uptime")
    p.add_argument("login")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_stream)

    p = sub.add_parser("video", help="stats for one VOD")
    p.add_argument("video", help="video id or twitch.tv/videos/... URL")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_video)

    p = sub.add_parser("clip", help="stats for one clip")
    p.add_argument("clip", help="clip slug or URL")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_clip)

    p = sub.add_parser("recent", help="recent VODs, or top clips with --clips")
    p.add_argument("login")
    p.add_argument("--clips", action="store_true", help="top clips (last 30 days) instead")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_recent)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (TwitchError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
