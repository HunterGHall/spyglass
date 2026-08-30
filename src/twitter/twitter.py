"""X / Twitter: profiles, follower counts + lists, post stats, recent posts.

Everything for X in one file. Uses the logged-out x.com web client's own
endpoints - a "guest token" for GraphQL, plus the syndication embed backend.
No developer API key.

    python twitter.py followers <user> [--following] [--limit N] [--json]
    python twitter.py profile   <user> [--json]
    python twitter.py post      <id|url> [--json]
    python twitter.py recent    <user> [--limit N] [--replies] [--json]

Logged out, the recent-posts timeline lags real time. Put your own
`X_AUTH_TOKEN` and `X_CT0` cookies in tokens.conf for live data (throwaway
account - X may lock it). Unofficial, against X's ToS; query IDs rotate.

`followers` with no flags prints just the count and works logged out; add
`--limit` (or `--following`) to list the accounts, which X only serves to a
logged-in session.

Importable: get_profile, get_follower_count, get_followers, get_following,
get_post_stats, get_recent_posts.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import math
import os
import random
import re
import sys
import threading
import time

import requests


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


# ========================================================================== #
# constants
# ========================================================================== #

WEB_BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
    "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_USERNAME_RE = re.compile(r"[A-Za-z0-9_]{1,15}")
_TWEET_ID_RE = re.compile(r"(\d{5,25})")
_TS = "%a %b %d %H:%M:%S %z %Y"
_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)

# GraphQL query ids from the logged-out web app; rotate every few weeks. When a
# call 404s with {"message": "Query not found"} grab a fresh id from the browser
# network tab (filter /graphql/).
GQL_QUERY_IDS = {
    "UserByScreenName": "sLVLhk0bGj3MVFEKTdax1w",
    "TweetResultByRestId": "5GOHgZe-8U2j5sVHQzEm9A",
    "UserTweets": "E3opETHurmVJflFsUBVuUQ",
}
GQL_FEATURES = {
    "hidden_profile_likes_enabled": True,
    "hidden_profile_subscriptions_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "subscriptions_verification_info_is_identity_verified_enabled": True,
    "subscriptions_verification_info_verified_since_enabled": True,
    "highlights_tweets_tab_ui_enabled": True,
    "responsive_web_twitter_article_notes_tab_enabled": True,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "articles_preview_enabled": True,
    "tweetypie_unmention_optimization_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "creator_subscriptions_quote_tweet_preview_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "rweb_video_timestamps_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
    "rweb_tipjar_consumption_enabled": True,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "rweb_video_screen_enabled": False,
    "payments_enabled": False,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_show_grok_translated_post": False,
    "responsive_web_grok_analysis_button_from_backend": True,
    "responsive_web_grok_image_annotation_enabled": True,
    "responsive_web_grok_community_note_auto_translation_is_enabled": False,
    "responsive_web_jetfuel_frame": True,
    "responsive_web_grok_share_attachment_enabled": True,
    "premium_content_api_read_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": True,
}


class XError(RuntimeError):
    """Any failure to retrieve or parse data from X."""


# ========================================================================== #
# session / auth
# ========================================================================== #

def clean_username(username: str) -> str:
    username = username.strip().lstrip("@").strip()
    if not _USERNAME_RE.fullmatch(username):
        raise ValueError(f"invalid X username: {username!r}")
    return username


def tweet_id_from(value: str) -> str:
    value = value.strip()
    if value.isdigit():
        return value
    m = re.search(r"status(?:es)?/(\d+)", value) or _TWEET_ID_RE.search(value)
    if not m:
        raise ValueError(f"could not find a tweet id in: {value!r}")
    return m.group(1)


def have_login() -> bool:
    return bool(os.environ.get("X_AUTH_TOKEN") and os.environ.get("X_CT0"))


_session: PacedSession | None = None


def session() -> PacedSession:
    global _session
    if _session is not None:
        return _session
    s = PacedSession()
    s.headers.update({"User-Agent": _UA, "Accept": "*/*"})
    auth_token, ct0 = os.environ.get("X_AUTH_TOKEN"), os.environ.get("X_CT0")
    if auth_token and ct0:
        s.headers.update({
            "authorization": f"Bearer {WEB_BEARER}",
            "x-csrf-token": ct0,
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-active-user": "yes",
        })
        for domain in (".x.com", ".twitter.com"):
            s.cookies.set("auth_token", auth_token, domain=domain)
            s.cookies.set("ct0", ct0, domain=domain)
    _session = s
    return s


def _is_logged_in() -> bool:
    return "x-twitter-auth-type" in session().headers


_GUEST_TOKEN: tuple[str, float] | None = None


def _guest_token(ttl: float = 900.0) -> str:
    global _GUEST_TOKEN
    s = session()
    if _is_logged_in():
        return ""
    s.headers["authorization"] = f"Bearer {WEB_BEARER}"
    now = time.time()
    if _GUEST_TOKEN and now - _GUEST_TOKEN[1] < ttl:
        token = _GUEST_TOKEN[0]
    else:
        resp = s.post("https://api.twitter.com/1.1/guest/activate.json", timeout=15)
        resp.raise_for_status()
        token = resp.json()["guest_token"]
        _GUEST_TOKEN = (token, now)
    s.headers["x-guest-token"] = token
    return token


def graphql(query_name: str, variables: dict) -> dict:
    try:
        qid = GQL_QUERY_IDS[query_name]
    except KeyError:
        raise XError(f"unknown GraphQL query: {query_name}") from None
    _guest_token()
    resp = session().get(
        f"https://api.twitter.com/graphql/{qid}/{query_name}",
        params={
            "variables": json.dumps(variables, separators=(",", ":")),
            "features": json.dumps(GQL_FEATURES, separators=(",", ":")),
        },
        timeout=15,
    )
    if resp.status_code == 404:
        raise XError(f"GraphQL query id for {query_name} is stale (404); "
                     f"update GQL_QUERY_IDS in twitter.py")
    if resp.status_code == 429:
        raise XError("rate limited by X (HTTP 429); back off and retry")
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("errors") and not payload.get("data"):
        raise XError(f"GraphQL error: {payload['errors'][0].get('message')}")
    return payload


# ========================================================================== #
# helpers
# ========================================================================== #

def deep_find(obj, *keys):
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for key in keys:
                if key in cur:
                    return cur[key]
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


def _next_data(html: str) -> dict:
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>',
        html, re.DOTALL,
    )
    if not m:
        raise XError("__NEXT_DATA__ not found (syndication layout changed?)")
    return json.loads(m.group(1))


def _parse_ts(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.strptime(value, _TS)
    except ValueError:
        return None


# ========================================================================== #
# profile
# ========================================================================== #

@dataclasses.dataclass
class Profile:
    id: str
    username: str
    name: str
    bio: str
    location: str | None
    website: str | None
    joined: dt.datetime | None
    verified: bool
    verified_type: str | None
    protected: bool
    followers: int
    following: int
    posts: int
    likes: int
    listed: int
    media: int
    profile_image_url: str | None
    banner_image_url: str | None

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["joined"] = self.joined.isoformat() if self.joined else None
        return d


def _first_url(legacy: dict) -> str | None:
    urls = legacy.get("entities", {}).get("url", {}).get("urls") or []
    if urls:
        return urls[0].get("expanded_url") or urls[0].get("url")
    return legacy.get("url")


def get_profile(username: str) -> Profile:
    username = clean_username(username)
    payload = graphql("UserByScreenName",
                      {"screen_name": username, "withSafetyModeUserFields": True})
    result = deep_find(payload, "result") or {}
    if result.get("__typename") == "UserUnavailable":
        raise XError(f"@{username} is unavailable (suspended or protected)")
    legacy = result.get("legacy")
    if not legacy:
        raise XError(f"@{username} not found")

    verified_type = legacy.get("verified_type") or (
        "Blue" if result.get("is_blue_verified") else None)
    return Profile(
        id=result.get("rest_id", ""),
        username=legacy.get("screen_name", username),
        name=legacy.get("name", ""),
        bio=legacy.get("description", ""),
        location=legacy.get("location") or None,
        website=_first_url(legacy),
        joined=_parse_ts(legacy.get("created_at")),
        verified=bool(legacy.get("verified") or result.get("is_blue_verified")),
        verified_type=verified_type,
        protected=bool(legacy.get("protected")),
        followers=int(legacy.get("followers_count", 0)),
        following=int(legacy.get("friends_count", 0)),
        posts=int(legacy.get("statuses_count", 0)),
        likes=int(legacy.get("favourites_count", 0)),
        listed=int(legacy.get("listed_count", 0)),
        media=int(legacy.get("media_count", 0)),
        profile_image_url=legacy.get("profile_image_url_https", "").replace("_normal", "") or None,
        banner_image_url=legacy.get("profile_banner_url"),
    )


def get_follower_count(username: str) -> int:
    try:
        return get_profile(username).followers
    except XError:
        pass
    # syndication fallback
    username = clean_username(username)
    resp = session().get(
        f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{username}",
        params={"showReplies": "false"}, timeout=15,
    )
    resp.raise_for_status()
    count = deep_find(_next_data(resp.text), "followers_count")
    if count is None:
        raise XError("could not get follower count (guest + syndication both failed)")
    return int(count)


# ========================================================================== #
# follower / following list
# ========================================================================== #

@dataclasses.dataclass
class Follower:
    id: str
    username: str
    name: str
    bio: str
    verified: bool
    protected: bool
    followers: int
    following: int

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def _follower_from(u: dict) -> Follower:
    return Follower(
        id=u.get("id_str", ""),
        username=u.get("screen_name", ""),
        name=u.get("name", "") or "",
        bio=u.get("description", "") or "",
        verified=bool(u.get("verified") or u.get("is_blue_verified")
                      or u.get("ext_is_blue_verified")),
        protected=bool(u.get("protected")),
        followers=int(u.get("followers_count", 0)),
        following=int(u.get("friends_count", 0)),
    )


def get_followers(username: str, limit: int = 100, *,
                  following: bool = False) -> list[Follower]:
    """List the accounts that follow `username` (or, with following=True, the
    ones it follows).

    Uses X's v1.1 followers/friends `list.json` endpoints - the GraphQL social
    graph is gated behind a browser-only transaction header, but v1.1 still
    honours the plain web-session cookies. Needs X_AUTH_TOKEN / X_CT0.
    """
    if not have_login():
        raise XError("listing followers needs a logged-in session; set "
                     "X_AUTH_TOKEN / X_CT0 in tokens.conf (see tokens.md)")
    handle = clean_username(username)
    endpoint = "friends" if following else "followers"
    out: list[Follower] = []
    cursor = "-1"

    while cursor and cursor != "0" and (not limit or len(out) < limit):
        page = min(200, limit - len(out)) if limit else 200
        resp = session().get(
            f"https://api.twitter.com/1.1/{endpoint}/list.json",
            params={
                "screen_name": handle, "count": max(page, 1), "cursor": cursor,
                "skip_status": "true", "include_user_entities": "false",
            },
            timeout=20,
        )
        if resp.status_code in (401, 403):
            try:
                msg = deep_find(resp.json(), "message") or ""
            except ValueError:
                msg = ""
            raise XError(f"X refused the request (HTTP {resp.status_code})"
                         + (f": {msg}" if msg else "; refresh X_AUTH_TOKEN / X_CT0"))
        if resp.status_code == 404:
            raise XError(f"@{handle} not found")
        if resp.status_code == 429:
            raise XError("rate limited by X (HTTP 429); back off and retry")
        resp.raise_for_status()
        data = resp.json()
        users = data.get("users") or []
        out.extend(_follower_from(u) for u in users)
        if not users:
            break
        cursor = data.get("next_cursor_str") or "0"

    return out[:limit] if limit else out


def get_following(username: str, limit: int = 100) -> list[Follower]:
    """List the accounts `username` follows."""
    return get_followers(username, limit, following=True)


# ========================================================================== #
# post stats
# ========================================================================== #

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


def _views(result: dict) -> int | None:
    count = (result.get("views") or {}).get("count")
    return int(count) if count is not None else None


def _stats_from_result(result: dict, tweet_id: str) -> PostStats:
    legacy = result["legacy"]
    user = deep_find(result.get("core", {}), "result") or {}
    ul = user.get("legacy") or {}
    screen_name = ul.get("screen_name", "i")
    return PostStats(
        id=tweet_id or result.get("rest_id", legacy.get("id_str", "")),
        url=f"https://x.com/{screen_name}/status/{tweet_id or result.get('rest_id', '')}",
        author=screen_name,
        author_name=ul.get("name", ""),
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


def get_post_stats(post: str) -> PostStats:
    tweet_id = tweet_id_from(post)
    payload = graphql("TweetResultByRestId", {
        "tweetId": tweet_id, "withCommunity": False,
        "includePromotedContent": False, "withVoice": False,
    })
    result = (deep_find(payload, "tweetResult") or {}).get("result") or {}
    typename = result.get("__typename")
    if typename == "TweetUnavailable":
        raise XError(f"post {tweet_id} is unavailable (deleted or private)")
    if typename == "TweetTombstone":
        raise XError(f"post {tweet_id} not viewable: {deep_find(result, 'text') or 'tombstoned'}")
    if typename == "TweetWithVisibilityResults":
        result = result.get("tweet") or result
    if not result.get("legacy"):
        raise XError(f"unexpected payload shape for post {tweet_id}")
    return _stats_from_result(result, tweet_id)


# ========================================================================== #
# recent posts
# ========================================================================== #

def _extract_posts(payload: dict) -> list[dict]:
    out, stack = [], [payload]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if cur.get("__typename") == "Tweet" and "legacy" in cur:
                out.append(cur)
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return out


def get_recent_posts(username: str, limit: int = 20, *,
                     include_replies: bool = False) -> list[PostStats]:
    user_id = get_profile(username).id
    payload = graphql("UserTweets", {
        "userId": user_id, "count": min(max(limit, 1), 100),
        "includePromotedContent": False,
        "withQuickPromoteEligibilityTweetFields": True,
        "withVoice": True, "withV2Timeline": True,
    })
    seen: set[str] = set()
    out: list[PostStats] = []
    for result in _extract_posts(payload):
        legacy = result["legacy"]
        tid = result.get("rest_id", legacy.get("id_str", ""))
        if tid in seen:
            continue
        seen.add(tid)
        if not include_replies and legacy.get("in_reply_to_status_id_str"):
            continue
        out.append(_stats_from_result(result, tid))
    out.sort(key=lambda p: p.created_at or _EPOCH, reverse=True)
    return out[:limit]


# ========================================================================== #
# CLI
# ========================================================================== #

def _print_profile(p: Profile) -> None:
    badge = f"  [{p.verified_type} verified]" if p.verified_type else ""
    print(f"@{p.username} — {p.name}{badge}")
    if p.bio:
        print(f"\n{p.bio}\n")
    if p.location:
        print(f"  location:  {p.location}")
    if p.website:
        print(f"  website:   {p.website}")
    print(f"  joined:    {p.joined.strftime('%B %Y') if p.joined else 'unknown'}")
    print(f"  followers: {p.followers:,}")
    print(f"  following: {p.following:,}")
    print(f"  posts:     {p.posts:,}")
    print(f"  likes:     {p.likes:,}")
    print(f"  listed:    {p.listed:,}")


def _print_post(p: PostStats) -> None:
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


def _cmd_followers(args) -> int:
    handle = clean_username(args.user)
    if not args.following and not args.limit:
        print(f"@{handle}: {get_follower_count(args.user):,} followers")
        return 0
    people = get_followers(args.user, args.limit or 100, following=args.following)
    if args.json:
        print(json.dumps([p.as_dict() for p in people], indent=2))
        return 0
    label = "following" if args.following else "followers"
    print(f"@{handle} — {len(people):,} {label} shown\n")
    for p in people:
        marks = "".join(m for m, v in (("✓", p.verified), ("🔒", p.protected)) if v)
        name = f"  ({p.name})" if p.name else ""
        print(f"  @{p.username}{name} {marks}".rstrip())
    return 0


def _cmd_profile(args) -> int:
    p = get_profile(args.user)
    print(json.dumps(p.as_dict(), indent=2)) if args.json else _print_profile(p)
    return 0


def _cmd_post(args) -> int:
    p = get_post_stats(args.post)
    print(json.dumps(p.as_dict(), indent=2)) if args.json else _print_post(p)
    return 0


def _cmd_recent(args) -> int:
    posts = get_recent_posts(args.user, args.limit, include_replies=args.replies)
    if args.json:
        print(json.dumps([p.as_dict() for p in posts], indent=2))
        return 0
    if not have_login() and posts and posts[0].created_at:
        age_min = (dt.datetime.now(dt.timezone.utc) - posts[0].created_at).total_seconds() / 60
        if 15 < age_min < 12 * 60:
            print(f"(logged-out timeline may lag; newest post here is {int(age_min)} min "
                  f"old. Set X_AUTH_TOKEN / X_CT0 for real-time.)\n")
    for p in posts:
        when = p.created_at.strftime("%Y-%m-%d %H:%M") if p.created_at else "?"
        text = " ".join(p.text.split())
        text = text[:77] + "..." if len(text) > 80 else text
        views = f"{p.views:,}" if p.views is not None else "n/a"
        print(f"[{when}] {text}")
        print(f"    views {views} · likes {p.likes:,} · reposts {p.reposts:,} "
              f"· replies {p.replies:,} · {p.url}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="twitter", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("followers", help="follower count, or the list with --limit")
    p.add_argument("user")
    p.add_argument("--following", action="store_true", help="list who they follow")
    p.add_argument("--limit", type=int, default=0,
                   help="list up to N accounts (needs login); 0 = just the count")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_followers)

    p = sub.add_parser("profile", help="user profile")
    p.add_argument("user")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_profile)

    p = sub.add_parser("post", help="engagement stats for one post")
    p.add_argument("post")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_post)

    p = sub.add_parser("recent", help="recent posts + stats")
    p.add_argument("user")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--replies", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_recent)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (XError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
