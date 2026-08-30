"""YouTube: channel profiles, subscriber counts, video stats, recent uploads.

Everything for YouTube in one file. Scrapes the JSON blobs YouTube embeds in
its HTML (`ytInitialData`, `ytInitialPlayerResponse`) plus the InnerTube API
its own frontend uses - no Data API key.

    python youtube.py subs     @MrBeast
    python youtube.py channel  @MrBeast [--json]
    python youtube.py video    dQw4w9WgXcQ [--json]
    python youtube.py recent   @MrBeast [--limit N] [--deep] [--json]

`YT_COOKIE` in tokens.conf (a raw Cookie header from a logged-in browser) is
only needed when YouTube bot-gates watch pages from your IP - it affects
`video` and `recent --deep`. Unofficial, undocumented, against YouTube's ToS;
for anything reliable use the Data API v3.

Importable: get_channel, get_subscriber_count, get_video_stats,
get_recent_videos.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import os
import random
import re
import sys
import threading
import time
import urllib.parse

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
    """requests.Session that sleeps PACE_MIN..PACE_MAX s between requests."""

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
# constants / errors
# ========================================================================== #

INNERTUBE_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
INNERTUBE_CLIENT = {"clientName": "WEB", "clientVersion": "2.20240726.00.00"}
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_VIDEO_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
_CHANNEL_ID_RE = re.compile(r"UC[A-Za-z0-9_-]{22}")


class YTError(RuntimeError):
    """Any failure to retrieve or parse data from YouTube."""


# ========================================================================== #
# session / auth
# ========================================================================== #

def _cookie_dict() -> dict[str, str]:
    out = {}
    for part in os.environ.get("YT_COOKIE", "").split(";"):
        if "=" in part:
            k, _, v = part.strip().partition("=")
            out[k] = v
    return out


def have_login() -> bool:
    return bool(os.environ.get("YT_COOKIE"))


def _sapisid_hash(origin: str = "https://www.youtube.com") -> str | None:
    c = _cookie_dict()
    sapisid = c.get("SAPISID") or c.get("__Secure-3PAPISID") or c.get("__Secure-1PAPISID")
    if not sapisid:
        return None
    ts = int(time.time())
    digest = hashlib.sha1(f"{ts} {sapisid} {origin}".encode()).hexdigest()
    return f"SAPISIDHASH {ts}_{digest}"


_session: PacedSession | None = None


def session() -> PacedSession:
    global _session
    if _session is not None:
        return _session
    s = PacedSession()
    s.headers.update(
        {
            "User-Agent": _UA,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/json",
        }
    )
    s.cookies.set("SOCS", "CAI", domain=".youtube.com")
    s.cookies.set("CONSENT", "YES+1", domain=".youtube.com")
    s.cookies.set("PREF", "hl=en&gl=US", domain=".youtube.com")
    raw = os.environ.get("YT_COOKIE")
    if raw:
        s.headers["Cookie"] = raw
        auth = _sapisid_hash()
        if auth:
            s.headers["Authorization"] = auth
            s.headers["X-Origin"] = "https://www.youtube.com"
            s.headers["X-Goog-AuthUser"] = "0"
    _session = s
    return s


# ========================================================================== #
# id / url parsing
# ========================================================================== #

def video_id_from(value: str) -> str:
    value = value.strip()
    if re.fullmatch(_VIDEO_ID_RE, value):
        return value
    m = re.search(r"(?:v=|/shorts/|/embed/|/live/|youtu\.be/)([A-Za-z0-9_-]{11})", value)
    if m:
        return m.group(1)
    raise ValueError(f"could not find a video id in: {value!r}")


def channel_ident(value: str) -> str:
    value = value.strip().rstrip("/")
    if re.fullmatch(_CHANNEL_ID_RE, value):
        return value
    m = _CHANNEL_ID_RE.search(value)
    if m:
        return m.group(0)
    if "/@" in value:
        return "@" + value.split("/@", 1)[1].split("/")[0]
    if value.startswith("@"):
        return value.split("/")[0]
    for tag in ("/c/", "/user/"):
        if tag in value:
            return value
    if value.startswith("http"):
        return value
    return "@" + value


def _channel_url(ident: str, tab: str = "about") -> str:
    ident = channel_ident(ident)
    if ident.startswith("@"):
        return f"https://www.youtube.com/{ident}/{tab}"
    if ident.startswith("http"):
        return ident.rstrip("/") + f"/{tab}"
    if re.fullmatch(_CHANNEL_ID_RE, ident):
        return f"https://www.youtube.com/channel/{ident}/{tab}"
    return f"https://www.youtube.com/{ident.lstrip('/')}/{tab}"


# ========================================================================== #
# fetching
# ========================================================================== #

def _match_object(html: str, brace: int) -> str | None:
    depth, in_str, esc = 0, False, False
    for i in range(brace, len(html)):
        ch = html[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return html[brace : i + 1]
    return None


def _json_var(html: str, name: str) -> dict | None:
    pattern = re.compile(r"(?:var\s+)?" + re.escape(name) + r"\s*=\s*\{")
    for m in pattern.finditer(html):
        frag = _match_object(html, m.end() - 1)
        if not frag:
            continue
        try:
            return json.loads(frag)
        except json.JSONDecodeError:
            continue
    return None


def _get_html(url: str) -> str:
    resp = session().get(url, params={"bpctr": "9999999999", "hl": "en"}, timeout=20)
    if resp.status_code == 429:
        raise YTError("rate limited by YouTube (HTTP 429); back off")
    resp.raise_for_status()
    return resp.text


def _bot_gated(text: str) -> bool:
    t = (text or "").lower()
    return any(s in t for s in ("confirm you", "not a bot", "login_required",
                                "sign in to confirm"))


def watch_data(video_id: str) -> tuple[dict, dict]:
    html = _get_html(f"https://www.youtube.com/watch?v={video_id}")
    player = _json_var(html, "ytInitialPlayerResponse") or {}
    data = _json_var(html, "ytInitialData") or {}
    status = player.get("playabilityStatus") or {}
    reason = f"{status.get('status', '')} {status.get('reason', '')}".strip()
    if _bot_gated(reason) or (not player and _bot_gated(html[:20000])):
        extra = "" if have_login() else (
            " - set YT_COOKIE in tokens.conf (YouTube bot-gates datacenter/VPN IPs)"
        )
        raise YTError(f"YouTube demanded sign-in for {video_id}{extra}")
    if not player and not data:
        raise YTError("could not parse watch page (layout changed?)")
    return player, data


def page_data(url: str) -> dict:
    data = _json_var(_get_html(url), "ytInitialData")
    if not data:
        raise YTError(f"could not parse ytInitialData from {url}")
    return data


# ========================================================================== #
# JSON helpers
# ========================================================================== #

def iter_dicts(obj):
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def deep_find(obj, *keys):
    for cur in iter_dicts(obj):
        for key in keys:
            if key in cur:
                return cur[key]
    return None


def text_of(node) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if "simpleText" in node:
            return node["simpleText"]
        if "content" in node:
            return node["content"]
        if "runs" in node:
            return "".join(r.get("text", "") for r in node["runs"])
        if "accessibility" in node:
            return node["accessibility"].get("accessibilityData", {}).get("label", "")
    return ""


_MULT = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def parse_count(text: str | None) -> int | None:
    if text is None:
        return None
    text = str(text).strip()
    if not text:
        return None
    if re.match(r"(?i)^(no|none)\b", text):
        return 0
    m = re.search(r"([\d,]+(?:\.\d+)?)\s*([KMBT])?", text, re.I)
    if not m:
        return None
    return int(round(float(m.group(1).replace(",", "")) * _MULT.get((m.group(2) or "").upper(), 1)))


def first_int(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"(\d[\d,]*)", text)
    return int(m.group(1).replace(",", "")) if m else None


def human_duration(seconds: int | None) -> str:
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
    title: str
    handle: str | None
    description: str
    subscribers: int | None
    subscribers_text: str | None
    videos: int | None
    views: int | None
    joined: dt.date | None
    country: str | None
    keywords: str | None
    tags: list[str]
    links: list[dict]
    is_verified: bool
    is_verified_artist: bool
    is_family_safe: bool | None
    available_countries: int
    worldwide: bool
    music_artist_name: str | None
    canonical_url: str | None
    vanity_url: str | None
    rss_url: str | None
    avatar_url: str | None
    banner_url: str | None
    tv_banner_url: str | None
    mobile_banner_url: str | None

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["joined"] = self.joined.isoformat() if self.joined else None
        return d


def _parse_joined(text: str) -> dt.date | None:
    m = re.search(r"([A-Z][a-z]{2,8})\s+(\d{1,2}),\s+(\d{4})", text or "")
    if not m:
        return None
    for fmt in ("%b %d %Y", "%B %d %Y"):
        try:
            return dt.datetime.strptime(" ".join(m.groups()), fmt).date()
        except ValueError:
            continue
    return None


def _unwrap_redirect(url: str) -> str:
    if "youtube.com/redirect" in url:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("q")
        if q:
            return q[0]
    return url


def _links(about: dict) -> list[dict]:
    out = []
    for link in about.get("links") or []:
        vm = link.get("channelExternalLinkViewModel") or link
        title = text_of(vm.get("title"))
        display = text_of(vm.get("link"))
        target = deep_find(vm.get("link", {}), "url") or display
        if title or display:
            out.append({"title": title, "url": _unwrap_redirect(target)})
    return out


def _biggest(sources: list[dict] | None) -> str | None:
    if not sources:
        return None
    best = max((s for s in sources if s.get("url")),
               key=lambda s: (s.get("width") or 0) * (s.get("height") or 0), default=None)
    return best.get("url") if best else None


def _hi_res_avatar(url: str | None) -> str | None:
    return re.sub(r"=s\d+", "=s800", url) if url and "=s" in url else url


def _badges(data: dict) -> tuple[bool, bool]:
    verified = artist = False
    for d in iter_dicts(data):
        style = str(d.get("style") or d.get("badgeStyle") or "")
        if "VERIFIED_ARTIST" in style:
            artist = True
        elif "VERIFIED" in style:
            verified = True
        icon = d.get("icon")
        if isinstance(icon, dict) and icon.get("iconType") == "CHECK_CIRCLE_THICK":
            verified = True
    return verified, artist


def _banners(header: dict) -> tuple[str | None, str | None, str | None]:
    web = tv = mobile = None
    for d in iter_dicts(header):
        if "imageBannerViewModel" in d:
            web = web or _biggest(deep_find(d["imageBannerViewModel"], "sources"))
        for key, which in (("tvBanner", "tv"), ("mobileBanner", "mobile")):
            if key in d and isinstance(d[key], dict):
                url = _biggest(deep_find(d[key], "sources") or deep_find(d[key], "thumbnails"))
                if which == "tv":
                    tv = tv or url
                else:
                    mobile = mobile or url
    return web, tv, mobile


def _handle_from_url(url: str | None) -> str | None:
    if url and "/@" in url:
        return "@" + url.split("/@", 1)[1].strip("/")
    return None


def get_channel(ident: str) -> Channel:
    data = page_data(_channel_url(ident, "about"))
    about = deep_find(data, "aboutChannelViewModel") or {}
    meta = (data.get("metadata") or {}).get("channelMetadataRenderer") or {}
    micro = (data.get("microformat") or {}).get("microformatDataRenderer") or {}

    subs_text = text_of(about.get("subscriberCountText")) or None
    verified, artist = _badges(data)
    web_banner, tv_banner, mobile_banner = _banners(data.get("header", {}))
    countries = meta.get("availableCountryCodes") or micro.get("availableCountries") or []
    avatar = _hi_res_avatar((deep_find(meta, "thumbnails") or [{}])[-1].get("url"))
    cid = about.get("channelId") or meta.get("externalId") or ""

    return Channel(
        id=cid,
        title=text_of(about.get("title")) or meta.get("title") or "",
        handle=(text_of(about.get("channelHandleText")) or None)
        or _handle_from_url(meta.get("vanityChannelUrl")),
        description=about.get("description") or meta.get("description") or "",
        subscribers=parse_count(subs_text),
        subscribers_text=subs_text,
        videos=first_int(text_of(about.get("videoCountText"))),
        views=first_int(text_of(about.get("viewCountText"))),
        joined=_parse_joined(text_of(about.get("joinedDateText"))),
        country=text_of(about.get("country")) or None,
        keywords=meta.get("keywords") or None,
        tags=micro.get("tags") or [],
        links=_links(about),
        is_verified=verified or artist,
        is_verified_artist=artist,
        is_family_safe=meta.get("isFamilySafe"),
        available_countries=len(countries),
        worldwide=len(countries) >= 200,
        music_artist_name=meta.get("musicArtistName") or None,
        canonical_url=about.get("canonicalChannelUrl")
        or (f"https://www.youtube.com/channel/{cid}" if cid else None),
        vanity_url=(meta.get("vanityChannelUrl") or "").replace("http://", "https://") or None,
        rss_url=meta.get("rssUrl"),
        avatar_url=avatar,
        banner_url=web_banner,
        tv_banner_url=tv_banner,
        mobile_banner_url=mobile_banner,
    )


def get_subscriber_count(ident: str) -> tuple[int | None, str | None]:
    c = get_channel(ident)
    return c.subscribers, c.subscribers_text


# ========================================================================== #
# video stats
# ========================================================================== #

@dataclasses.dataclass
class VideoChannel:
    name: str
    id: str
    subscribers: int | None
    subscribers_text: str | None


@dataclasses.dataclass
class Format:
    max_resolution: str | None
    max_fps: int | None
    hdr: bool
    video_codecs: list[str]
    audio_codecs: list[str]


@dataclasses.dataclass
class Captions:
    count: int
    languages: list[str]
    has_auto: bool


@dataclasses.dataclass
class VideoStats:
    id: str
    url: str
    title: str
    description: str
    channel: VideoChannel
    published: dt.datetime | None
    published_text: str | None
    age_days: int | None
    duration_seconds: int
    duration: str
    category: str | None
    keywords: list[str]
    views: int | None
    likes: int | None
    comments: int | None
    comments_text: str | None
    like_rate: float | None
    comment_rate: float | None
    views_per_day: int | None
    is_live_now: bool
    is_upcoming: bool
    was_live: bool
    live_start: str | None
    live_end: str | None
    concurrent_viewers: int | None
    availability: str
    age_restricted: bool
    family_safe: bool | None
    allow_ratings: bool | None
    available_countries: int | None
    fmt: Format
    captions: Captions
    default_audio_language: str | None
    thumbnail_url: str | None

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["published"] = self.published.isoformat() if self.published else None
        return d


def _parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def _format_details(player: dict) -> Format:
    formats = (player.get("streamingData") or {}).get("adaptiveFormats") or []
    formats += (player.get("streamingData") or {}).get("formats") or []
    heights = [f["height"] for f in formats if f.get("height")]
    fpses = [f["fps"] for f in formats if f.get("fps")]
    vcodecs, acodecs, hdr = set(), set(), False
    for f in formats:
        mime = f.get("mimeType", "")
        m = re.search(r'codecs="([^"]+)"', mime)
        codec = m.group(1).split(".")[0] if m else None
        if codec:
            (vcodecs if mime.startswith("video/") else acodecs).add(codec)
        if "HDR" in (f.get("qualityLabel") or ""):
            hdr = True
        if (f.get("colorInfo") or {}).get("primaries", "").endswith("BT2020"):
            hdr = True
    max_h = max(heights) if heights else None
    res = {4320: "8K", 2160: "4K", 1440: "1440p"}.get(max_h, f"{max_h}p") if max_h else None
    return Format(res, max(fpses) if fpses else None, hdr, sorted(vcodecs), sorted(acodecs))


def _captions(player: dict) -> Captions:
    tracks = ((player.get("captions") or {})
              .get("playerCaptionsTracklistRenderer", {}).get("captionTracks") or [])
    langs, has_auto = [], False
    for t in tracks:
        if t.get("kind") == "asr":
            has_auto = True
        elif t.get("languageCode") and t["languageCode"] not in langs:
            langs.append(t["languageCode"])
    return Captions(len(tracks), langs, has_auto)


def _likes(data: dict) -> int | None:
    for d in iter_dicts(data):
        text = d.get("accessibilityText") or ""
        m = re.search(r"along with ([\d,]+) other", text) or re.match(r"([\d,]+) likes?$", text)
        if m:
            return int(m.group(1).replace(",", ""))
    return None


def _comments_text(data: dict) -> str | None:
    for d in iter_dicts(data):
        h = d.get("engagementPanelTitleHeaderRenderer")
        if h and text_of(h.get("title")) == "Comments":
            return text_of(h.get("contextualInfo")) or None
    return None


def _owner(data: dict) -> tuple[str, str, str | None]:
    for d in iter_dicts(data):
        o = d.get("videoOwnerRenderer")
        if o:
            cid = deep_find(o.get("navigationEndpoint", {}), "browseId") or ""
            return text_of(o.get("title")), cid, text_of(o.get("subscriberCountText")) or None
    return "", "", None


def _primary_info(data: dict) -> dict:
    for d in iter_dicts(data):
        if "videoPrimaryInfoRenderer" in d:
            return d["videoPrimaryInfoRenderer"]
    return {}


def _age_restricted(player: dict) -> bool:
    if deep_find(player, "ytRating") == "ytAgeRestricted":
        return True
    status = player.get("playabilityStatus") or {}
    reason = f"{status.get('status', '')} {status.get('reason', '')}".lower()
    return "age" in reason and "confirm" in reason


def get_video_stats(video: str) -> VideoStats:
    vid = video_id_from(video)
    player, data = watch_data(vid)
    details = player.get("videoDetails") or {}
    micro = (player.get("microformat") or {}).get("playerMicroformatRenderer") or {}
    if not details and not micro:
        reason = (player.get("playabilityStatus") or {}).get("reason", "unavailable")
        raise YTError(f"video {vid}: {reason}")

    primary = _primary_info(data)
    owner_name, owner_id, owner_subs = _owner(data)
    published = _parse_iso(micro.get("publishDate") or micro.get("uploadDate"))
    age_days = ((dt.datetime.now(dt.timezone.utc) - published).days
               if published and published.tzinfo else None)
    views = first_int(details.get("viewCount")) or first_int(micro.get("viewCount"))
    likes = _likes(data)
    comments_text = _comments_text(data)
    comments = parse_count(comments_text)
    view_render = deep_find(primary, "videoViewCountRenderer") or {}
    concurrent = first_int(text_of(view_render.get("viewCount"))) if view_render.get("isLive") else None
    live = micro.get("liveBroadcastDetails") or {}
    countries = micro.get("availableCountries") or []
    availability = "private" if details.get("isPrivate") else (
        "unlisted" if micro.get("isUnlisted") else "public")
    duration = int(details.get("lengthSeconds") or micro.get("lengthSeconds") or 0)
    thumbs = deep_find(micro.get("thumbnail", {}), "thumbnails") or \
        details.get("thumbnail", {}).get("thumbnails", [])

    return VideoStats(
        id=vid,
        url=f"https://www.youtube.com/watch?v={vid}",
        title=details.get("title") or text_of(primary.get("title")),
        description=details.get("shortDescription", ""),
        channel=VideoChannel(owner_name or details.get("author", ""),
                             owner_id or details.get("channelId", ""),
                             parse_count(owner_subs), owner_subs),
        published=published,
        published_text=text_of(primary.get("dateText")) or None,
        age_days=age_days,
        duration_seconds=duration,
        duration=human_duration(duration),
        category=micro.get("category"),
        keywords=details.get("keywords") or [],
        views=views,
        likes=likes,
        comments=comments,
        comments_text=comments_text,
        like_rate=round(likes / views, 5) if likes and views else None,
        comment_rate=round(comments / views, 5) if comments and views else None,
        views_per_day=int(views / age_days) if views and age_days else None,
        is_live_now=bool(details.get("isLive") or live.get("isLiveNow")),
        is_upcoming=bool(details.get("isUpcoming")),
        was_live=bool(details.get("isLiveContent")) and not details.get("isLive"),
        live_start=live.get("startTimestamp"),
        live_end=live.get("endTimestamp"),
        concurrent_viewers=concurrent,
        availability=availability,
        age_restricted=_age_restricted(player),
        family_safe=micro.get("isFamilySafe"),
        allow_ratings=details.get("allowRatings"),
        available_countries=len(countries) or None,
        fmt=_format_details(player),
        captions=_captions(player),
        default_audio_language=details.get("defaultAudioLanguage")
        or micro.get("defaultAudioLanguage"),
        thumbnail_url=f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg" if thumbs else None,
    )


# ========================================================================== #
# recent uploads
# ========================================================================== #

@dataclasses.dataclass
class RecentVideo:
    id: str
    url: str
    title: str
    published_text: str | None
    duration: str | None
    views: int | None
    views_exact: bool
    likes: int | None = None
    comments: int | None = None

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def _lockup_to_video(lvm: dict) -> RecentVideo | None:
    vid = lvm.get("contentId")
    if not vid or lvm.get("contentType") not in (None, "LOCKUP_CONTENT_TYPE_VIDEO"):
        return None
    meta = lvm.get("metadata", {}).get("lockupMetadataViewModel", {})
    parts = []
    for row in deep_find(meta, "metadataRows") or []:
        for part in row.get("metadataParts", []):
            txt = text_of(part.get("text"))
            if txt:
                parts.append(txt)
    duration = None
    for d in iter_dicts(lvm.get("contentImage", {})):
        badge = d.get("thumbnailBadgeViewModel")
        if badge and re.fullmatch(r"[\d:]+", badge.get("text", "")):
            duration = badge["text"]
            break
    return RecentVideo(
        id=vid,
        url=f"https://www.youtube.com/watch?v={vid}",
        title=text_of(meta.get("title")),
        published_text=next((p for p in parts if "ago" in p.lower() or "premier" in p.lower()), None),
        duration=duration,
        views=next((parse_count(p) for p in parts if "view" in p.lower()), None),
        views_exact=False,
    )


def _legacy_video_renderer(vr: dict) -> RecentVideo:
    vid = vr.get("videoId", "")
    return RecentVideo(
        id=vid,
        url=f"https://www.youtube.com/watch?v={vid}",
        title=text_of(vr.get("title")),
        published_text=text_of(vr.get("publishedTimeText")) or None,
        duration=text_of(vr.get("lengthText")) or None,
        views=parse_count(text_of(vr.get("viewCountText"))),
        views_exact=False,
    )


def get_recent_videos(ident: str, limit: int = 15, *, deep: bool = False) -> list[RecentVideo]:
    data = page_data(_channel_url(ident, "videos"))
    videos: list[RecentVideo] = []
    seen: set[str] = set()
    for d in iter_dicts(data):
        if "lockupViewModel" in d:
            rv = _lockup_to_video(d["lockupViewModel"])
        elif "videoRenderer" in d:
            rv = _legacy_video_renderer(d["videoRenderer"])
        else:
            continue
        if rv and rv.id and rv.id not in seen:
            seen.add(rv.id)
            videos.append(rv)
        if len(videos) >= limit:
            break

    if deep:
        for rv in videos:
            try:
                s = get_video_stats(rv.id)
            except YTError:
                continue
            rv.views = s.views if s.views is not None else rv.views
            rv.views_exact = s.views is not None
            rv.likes, rv.comments = s.likes, s.comments
    return videos


# ========================================================================== #
# CLI
# ========================================================================== #

def _pct(x: float | None) -> str:
    return f"{x * 100:.2f}%" if x is not None else "n/a"


def _print_channel(c: Channel) -> None:
    mark = "  [verified artist]" if c.is_verified_artist else (
        "  [verified]" if c.is_verified else "")
    print(f"{c.title}{mark}" + (f"  ({c.handle})" if c.handle else ""))
    print(f"  {c.id}")
    if c.music_artist_name:
        print(f"  music artist:  {c.music_artist_name}")
    if c.description:
        print(f"\n{c.description.strip()[:500]}\n")
    subs = c.subscribers_text or (f"{c.subscribers:,}" if c.subscribers is not None else "n/a")
    print(f"  subscribers:   {subs}")
    print(f"  videos:        {c.videos:,}" if c.videos is not None else "  videos:        n/a")
    print(f"  total views:   {c.views:,}" if c.views is not None else "  total views:   n/a")
    if c.joined:
        print(f"  joined:        {c.joined:%B %d, %Y}")
    if c.country:
        print(f"  country:       {c.country}")
    print(f"  availability:  {'worldwide' if c.worldwide else f'{c.available_countries} countries'}")
    if c.is_family_safe is not None:
        print(f"  family safe:   {'yes' if c.is_family_safe else 'no'}")
    if c.avatar_url:
        print(f"  avatar:        {c.avatar_url}")
    if c.banner_url:
        print(f"  banner:        {c.banner_url}")
    if c.rss_url:
        print(f"  uploads RSS:   {c.rss_url}")
    for link in c.links:
        print(f"  link:          {link['title']}: {link['url']}")
    if c.tags:
        print(f"  tags:          {', '.join(c.tags[:12])}")


def _print_video(v: VideoStats) -> None:
    print(v.title)
    print(f"{v.url}\n")
    print(f"  channel:      {v.channel.name}  ({v.channel.subscribers_text or '?'})")
    when = v.published.strftime("%Y-%m-%d") if v.published else v.published_text
    print(f"  published:    {when}" + (f"  ({v.age_days} days ago)" if v.age_days else ""))
    print(f"  duration:     {v.duration}")
    if v.category:
        print(f"  category:     {v.category}")
    print()
    print(f"  views:        {v.views:,}" if v.views is not None else "  views:        n/a")
    print(f"  likes:        {v.likes:,}" if v.likes is not None else "  likes:        hidden")
    cm = v.comments_text or (f"{v.comments:,}" if v.comments is not None else "n/a")
    print(f"  comments:     {cm}")
    print(f"  like rate:    {_pct(v.like_rate)}  (likes/views)")
    print(f"  comment rate: {_pct(v.comment_rate)}")
    if v.views_per_day:
        print(f"  views/day:    {v.views_per_day:,}")
    print()
    f = v.fmt
    extras = ([f"{f.max_fps}fps"] if f.max_fps else []) + (["HDR"] if f.hdr else [])
    print(f"  max quality:  {f.max_resolution or '?'}" + (f" {' '.join(extras)}" if extras else ""))
    if f.video_codecs:
        print(f"  video codecs: {', '.join(f.video_codecs)}")
    if v.captions.count:
        auto = " +auto" if v.captions.has_auto else ""
        print(f"  captions:     {v.captions.count} tracks "
              f"({', '.join(v.captions.languages[:8]) or 'auto only'}{auto})")
    flags = [f"availability={v.availability}"]
    for cond, label in ((v.age_restricted, "age-restricted"),
                        (v.family_safe is False, "not-family-safe"),
                        (v.is_live_now, "LIVE NOW"), (v.was_live, "was-live"),
                        (v.is_upcoming, "upcoming")):
        if cond:
            flags.append(label)
    if v.concurrent_viewers:
        flags.append(f"{v.concurrent_viewers:,} watching")
    print(f"  flags:        {', '.join(flags)}")


def _cmd_subs(args) -> int:
    count, text = get_subscriber_count(args.channel)
    if count is None:
        print("subscriber count is hidden")
    else:
        print(f"{args.channel}: {count:,} subscribers" + (f" ({text})" if text else ""))
    return 0


def _cmd_channel(args) -> int:
    c = get_channel(args.channel)
    print(json.dumps(c.as_dict(), indent=2)) if args.json else _print_channel(c)
    return 0


def _cmd_video(args) -> int:
    v = get_video_stats(args.video)
    print(json.dumps(v.as_dict(), indent=2)) if args.json else _print_video(v)
    return 0


def _cmd_recent(args) -> int:
    vids = get_recent_videos(args.channel, args.limit, deep=args.deep)
    if args.json:
        print(json.dumps([v.as_dict() for v in vids], indent=2))
        return 0
    for v in vids:
        tilde = "" if v.views_exact else "~"
        views = f"{tilde}{v.views:,}" if v.views is not None else "n/a"
        line = f"    {views} views"
        if v.likes is not None:
            line += f" · {v.likes:,} likes"
        if v.comments is not None:
            line += f" · {v.comments:,} comments"
        print(f"[{v.published_text or '?'}] {v.title[:70]}")
        print(f"{line} · {v.duration or '?'} · {v.url}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="youtube", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("subs", help="subscriber count")
    p.add_argument("channel")
    p.set_defaults(fn=_cmd_subs)

    p = sub.add_parser("channel", help="channel profile + metadata")
    p.add_argument("channel")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_channel)

    p = sub.add_parser("video", help="in-depth video stats")
    p.add_argument("video")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_video)

    p = sub.add_parser("recent", help="recent uploads")
    p.add_argument("channel")
    p.add_argument("--limit", type=int, default=15)
    p.add_argument("--deep", action="store_true", help="exact per-video stats (slow)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_recent)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (YTError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
