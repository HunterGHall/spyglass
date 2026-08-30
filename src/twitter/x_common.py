"""Shared plumbing for the unofficial X (Twitter) scrapers in this repo.

Nothing here needs a developer API key. It leans on two things the logged-out
x.com web client itself uses:

    * syndication.twitter.com / cdn.syndication.twimg.com - the embed backend
    * a "guest token" activated with the public web bearer token, which unlocks
      a subset of the GraphQL API

All of it is unofficial, undocumented, rate limited by IP, changes without
notice, and is against X's Terms of Service. Use the official API for anything
that matters.
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import threading
import time

import requests


# --------------------------------------------------------------------------- #
# tokens.conf loader (KEY = value lines; env vars win)
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# client-side rate limiting: sleep a random PACE_MIN..PACE_MAX s between requests
# --------------------------------------------------------------------------- #

class PacedSession(requests.Session):
    """requests.Session that paces every request (shared across instances)."""

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

# Public bearer token shipped in the x.com web bundle. Not a secret.
WEB_BEARER = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
    "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

USERNAME_RE = re.compile(r"[A-Za-z0-9_]{1,15}")
_TWEET_ID_RE = re.compile(r"(\d{5,25})")

# GraphQL query ids from the logged-out x.com web app. These rotate every few
# weeks; when a call starts returning {"message": "Query not found"} grab a
# fresh id from the browser network tab (filter: /graphql/).
GQL_QUERY_IDS = {
    "UserByScreenName": "sLVLhk0bGj3MVFEKTdax1w",
    "TweetResultByRestId": "5GOHgZe-8U2j5sVHQzEm9A",
    "UserTweets": "E3opETHurmVJflFsUBVuUQ",
}

# The web app sends a big pile of feature flags with every GraphQL call and
# rejects the request if any it expects is missing. This superset satisfies all
# three queries we use.
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


class XScrapeError(RuntimeError):
    """Any failure to retrieve or parse data from X."""


def clean_username(username: str) -> str:
    username = username.strip().lstrip("@").strip()
    if not USERNAME_RE.fullmatch(username):
        raise ValueError(f"invalid X username: {username!r}")
    return username


def tweet_id_from(value: str) -> str:
    """Accept a raw id or any x.com/twitter.com status URL and return the id."""
    value = value.strip()
    if value.isdigit():
        return value
    match = re.search(r"status(?:es)?/(\d+)", value) or _TWEET_ID_RE.search(value)
    if not match:
        raise ValueError(f"could not find a tweet id in: {value!r}")
    return match.group(1)


def new_session() -> requests.Session:
    session = PacedSession()
    session.headers.update({"User-Agent": _UA, "Accept": "*/*"})
    apply_login(session)
    return session


def apply_login(session: requests.Session) -> bool:
    """Use real login cookies if X_AUTH_TOKEN / X_CT0 are in the environment.

    The logged-out endpoints serve a timeline that lags real time by an hour or
    more for busy accounts. Passing your own session cookies removes that lag.
    In a browser logged into x.com, copy the `auth_token` and `ct0` cookies:

        export X_AUTH_TOKEN=xxxxxxxx
        export X_CT0=yyyyyyyy

    Returns True if login headers were applied. This is your own account acting
    through an unofficial client, so X may flag or lock it — use a throwaway.
    """
    auth_token = os.environ.get("X_AUTH_TOKEN")
    ct0 = os.environ.get("X_CT0")
    if not (auth_token and ct0):
        return False

    session.headers["authorization"] = f"Bearer {WEB_BEARER}"
    session.headers["x-csrf-token"] = ct0
    session.headers["x-twitter-auth-type"] = "OAuth2Session"
    session.headers["x-twitter-active-user"] = "yes"
    session.cookies.set("auth_token", auth_token, domain=".x.com")
    session.cookies.set("ct0", ct0, domain=".x.com")
    session.cookies.set("auth_token", auth_token, domain=".twitter.com")
    session.cookies.set("ct0", ct0, domain=".twitter.com")
    session.headers.pop("x-guest-token", None)
    return True


def is_logged_in(session: requests.Session) -> bool:
    return "x-twitter-auth-type" in session.headers


def have_login() -> bool:
    """True if login cookies are configured in the environment."""
    return bool(os.environ.get("X_AUTH_TOKEN") and os.environ.get("X_CT0"))


_GUEST_TOKEN: tuple[str, float] | None = None


def guest_token(session: requests.Session, *, ttl: float = 900.0) -> str:
    """Activate (and cache) a guest token, and set the auth headers on `session`.

    No-ops when the session is already authenticated via apply_login().
    """
    global _GUEST_TOKEN

    if is_logged_in(session):
        return ""

    session.headers["authorization"] = f"Bearer {WEB_BEARER}"
    now = time.time()
    if _GUEST_TOKEN and now - _GUEST_TOKEN[1] < ttl:
        token = _GUEST_TOKEN[0]
    else:
        resp = session.post(
            "https://api.twitter.com/1.1/guest/activate.json", timeout=15
        )
        resp.raise_for_status()
        token = resp.json()["guest_token"]
        _GUEST_TOKEN = (token, now)

    session.headers["x-guest-token"] = token
    return token


def graphql(
    session: requests.Session,
    query_name: str,
    variables: dict,
    *,
    features: dict | None = None,
) -> dict:
    """Call a logged-out GraphQL query by name and return the parsed JSON.

    Activates a guest token on `session` if needed. Raises XScrapeError on the
    common failure modes (stale query id, rate limit, GraphQL-level errors).
    """
    try:
        query_id = GQL_QUERY_IDS[query_name]
    except KeyError:
        raise XScrapeError(f"unknown GraphQL query: {query_name}") from None

    guest_token(session)
    resp = session.get(
        f"https://api.twitter.com/graphql/{query_id}/{query_name}",
        params={
            "variables": json.dumps(variables, separators=(",", ":")),
            "features": json.dumps(
                features or GQL_FEATURES, separators=(",", ":")
            ),
        },
        timeout=15,
    )
    if resp.status_code == 404:
        raise XScrapeError(
            f"GraphQL query id for {query_name} is stale "
            f"(got 404). Update GQL_QUERY_IDS in x_common.py."
        )
    if resp.status_code == 429:
        raise XScrapeError("rate limited by X (HTTP 429); back off and retry")
    resp.raise_for_status()

    payload = resp.json()
    if payload.get("errors") and not payload.get("data"):
        raise XScrapeError(f"GraphQL error: {payload['errors'][0].get('message')}")
    return payload


def next_data(html: str) -> dict:
    """Pull the __NEXT_DATA__ JSON blob out of a syndication HTML page."""
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>',
        html,
        re.DOTALL,
    )
    if not match:
        raise XScrapeError("__NEXT_DATA__ not found (syndication layout changed?)")
    return json.loads(match.group(1))


def deep_find(obj, *keys):
    """Depth-first search a decoded JSON structure for the first of `keys`.

    Returns the value, or None if no key matches anywhere in the tree.
    """
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


def syndication_token(tweet_id: str) -> str:
    """Reproduce the client-side `token` param cdn.syndication.twimg.com wants.

    Mirrors the x.com web code:
        ((id / 1e15) * PI).toString(36).replace(/(0+|\\.)/g, "")
    """
    value = (int(tweet_id) / 1e15) * math.pi
    return _js_number_to_base36(value).replace("0", "").replace(".", "")


def _js_number_to_base36(num: float) -> str:
    """Match JavaScript's Number.prototype.toString(36) for our purposes."""
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if num < 0:
        return "-" + _js_number_to_base36(-num)

    int_part = int(num)
    frac = num - int_part

    if int_part == 0:
        out = "0"
    else:
        chars = []
        n = int_part
        while n:
            chars.append(digits[n % 36])
            n //= 36
        out = "".join(reversed(chars))

    if frac:
        out += "."
        # 12 fractional digits is plenty once zeros are stripped by the caller.
        for _ in range(12):
            frac *= 36
            d = int(frac)
            out += digits[d]
            frac -= d
    return out
