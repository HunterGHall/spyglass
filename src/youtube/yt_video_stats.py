"""In-depth public stats for a single YouTube video.

Views, likes, comments, engagement rates, publish/live info, format details
(resolution, fps, HDR, codecs), captions, age/availability flags, and more.

    python yt_video_stats.py dQw4w9WgXcQ
    python yt_video_stats.py "https://youtu.be/dQw4w9WgXcQ" --json

Notes:
    * View and like counts are exact. Comment counts are YouTube's rounded
      figure ("2.4M"); `comments` is that value parsed, `comments_text` the
      original.
    * Subscriber counts are rounded by YouTube.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import re
import sys

import yt_common as yt


@dataclasses.dataclass
class Channel:
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
    channel: Channel
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

    like_rate: float | None          # likes / views
    comment_rate: float | None       # comments / views
    views_per_day: int | None

    is_live_now: bool
    is_upcoming: bool
    was_live: bool
    live_start: str | None
    live_end: str | None
    concurrent_viewers: int | None

    availability: str                # public | unlisted | private
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


def _parse_publish(value: str | None) -> dt.datetime | None:
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
    vcodecs, acodecs = set(), set()
    hdr = False
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
    res = None
    if max_h:
        res = {4320: "8K", 2160: "4K", 1440: "1440p"}.get(max_h, f"{max_h}p")
    return Format(
        max_resolution=res,
        max_fps=max(fpses) if fpses else None,
        hdr=hdr,
        video_codecs=sorted(vcodecs),
        audio_codecs=sorted(acodecs),
    )


def _captions(player: dict) -> Captions:
    tracks = (
        (player.get("captions") or {})
        .get("playerCaptionsTracklistRenderer", {})
        .get("captionTracks")
        or []
    )
    langs, has_auto = [], False
    for t in tracks:
        code = t.get("languageCode")
        if t.get("kind") == "asr":
            has_auto = True
        elif code and code not in langs:
            langs.append(code)
    return Captions(count=len(tracks), languages=langs, has_auto=has_auto)


def _likes(data: dict) -> int | None:
    for d in yt.iter_dicts(data):
        text = d.get("accessibilityText") or ""
        m = re.search(r"along with ([\d,]+) other", text) or re.match(
            r"([\d,]+) likes?$", text
        )
        if m:
            return int(m.group(1).replace(",", ""))
    return None


def _comments_text(data: dict) -> str | None:
    for d in yt.iter_dicts(data):
        h = d.get("engagementPanelTitleHeaderRenderer")
        if h and yt.text_of(h.get("title")) == "Comments":
            return yt.text_of(h.get("contextualInfo")) or None
    return None


def _owner(data: dict) -> tuple[str, str, str | None]:
    for d in yt.iter_dicts(data):
        o = d.get("videoOwnerRenderer")
        if o:
            cid = yt.deep_find(o.get("navigationEndpoint", {}), "browseId") or ""
            return (
                yt.text_of(o.get("title")),
                cid,
                yt.text_of(o.get("subscriberCountText")) or None,
            )
    return "", "", None


def _primary_info(data: dict) -> dict:
    for d in yt.iter_dicts(data):
        if "videoPrimaryInfoRenderer" in d:
            return d["videoPrimaryInfoRenderer"]
    return {}


def _age_restricted(player: dict) -> bool:
    if yt.deep_find(player, "ytRating") == "ytAgeRestricted":
        return True
    status = player.get("playabilityStatus") or {}
    reason = f"{status.get('status','')} {status.get('reason','')}".lower()
    return "age" in reason and "confirm" in reason


def get_video_stats(video: str, session=None) -> VideoStats:
    session = session or yt.new_session()
    vid = yt.video_id_from(video)
    player, data = yt.watch_data(session, vid)

    details = player.get("videoDetails") or {}
    micro = (player.get("microformat") or {}).get("playerMicroformatRenderer") or {}
    if not details and not micro:
        status = (player.get("playabilityStatus") or {}).get("reason", "unavailable")
        raise yt.YTScrapeError(f"video {vid}: {status}")

    primary = _primary_info(data)
    owner_name, owner_id, owner_subs = _owner(data)

    published = _parse_publish(micro.get("publishDate") or micro.get("uploadDate"))
    age_days = (
        (dt.datetime.now(dt.timezone.utc) - published).days
        if published and published.tzinfo
        else None
    )

    views = yt.first_int(details.get("viewCount")) or yt.first_int(
        micro.get("viewCount")
    )
    likes = _likes(data)
    comments_text = _comments_text(data)
    comments = yt.parse_count(comments_text)

    view_render = yt.deep_find(primary, "videoViewCountRenderer") or {}
    concurrent = None
    if view_render.get("isLive"):
        concurrent = yt.first_int(yt.text_of(view_render.get("viewCount")))

    live = micro.get("liveBroadcastDetails") or {}
    countries = micro.get("availableCountries") or []

    availability = "public"
    if details.get("isPrivate"):
        availability = "private"
    elif micro.get("isUnlisted"):
        availability = "unlisted"

    duration = int(details.get("lengthSeconds") or micro.get("lengthSeconds") or 0)

    thumbs = yt.deep_find(micro.get("thumbnail", {}), "thumbnails") or details.get(
        "thumbnail", {}
    ).get("thumbnails", [])
    thumb_url = (
        f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg"
        if thumbs
        else None
    )

    return VideoStats(
        id=vid,
        url=f"https://www.youtube.com/watch?v={vid}",
        title=details.get("title") or yt.text_of(primary.get("title")),
        description=details.get("shortDescription", ""),
        channel=Channel(
            name=owner_name or details.get("author", ""),
            id=owner_id or details.get("channelId", ""),
            subscribers=yt.parse_count(owner_subs),
            subscribers_text=owner_subs,
        ),
        published=published,
        published_text=yt.text_of(primary.get("dateText")) or None,
        age_days=age_days,
        duration_seconds=duration,
        duration=yt.human_duration(duration),
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
        thumbnail_url=thumb_url,
    )


def _pct(x: float | None) -> str:
    return f"{x * 100:.2f}%" if x is not None else "n/a"


def _print_human(v: VideoStats) -> None:
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
    res = f.max_resolution or "?"
    extras = []
    if f.max_fps:
        extras.append(f"{f.max_fps}fps")
    if f.hdr:
        extras.append("HDR")
    print(f"  max quality:  {res}" + (f" {' '.join(extras)}" if extras else ""))
    if f.video_codecs:
        print(f"  video codecs: {', '.join(f.video_codecs)}")
    cap = v.captions
    if cap.count:
        auto = " +auto" if cap.has_auto else ""
        langs = ", ".join(cap.languages[:8]) or "auto only"
        print(f"  captions:     {cap.count} tracks ({langs}{auto})")
    flags = [f"availability={v.availability}"]
    if v.age_restricted:
        flags.append("age-restricted")
    if v.family_safe is False:
        flags.append("not-family-safe")
    if v.is_live_now:
        flags.append("LIVE NOW")
    if v.was_live:
        flags.append("was-live")
    if v.is_upcoming:
        flags.append("upcoming")
    if v.concurrent_viewers:
        flags.append(f"{v.concurrent_viewers:,} watching")
    print(f"  flags:        {', '.join(flags)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="In-depth stats for one YouTube video.")
    ap.add_argument("video", help="video id or URL")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        stats = get_video_stats(args.video)
    except (yt.YTScrapeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(stats.as_dict(), indent=2))
    else:
        _print_human(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
