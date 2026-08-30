"""List a YouTube channel's recent uploads with stats.

    python yt_recent_videos.py @MrBeast
    python yt_recent_videos.py @MrBeast --limit 20 --deep --json

By default this reads the channel's Videos tab in one request, so view counts
are YouTube's rounded figures ("76M views") and there are no like/comment
counts. `--deep` fetches the full watch page for each video (exact views,
likes, comments) - slow, since every request is paced 3-7 s apart.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys

import yt_common as yt
from yt_video_stats import get_video_stats


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
    title = yt.text_of(meta.get("title"))

    parts = []
    for row in yt.deep_find(meta, "metadataRows") or []:
        for part in row.get("metadataParts", []):
            txt = yt.text_of(part.get("text"))
            if txt:
                parts.append(txt)
    views = next((yt.parse_count(p) for p in parts if "view" in p.lower()), None)
    published = next(
        (p for p in parts if "ago" in p.lower() or "premier" in p.lower()), None
    )

    duration = None
    for d in yt.iter_dicts(lvm.get("contentImage", {})):
        badge = d.get("thumbnailBadgeViewModel")
        if badge and re.fullmatch(r"[\d:]+", badge.get("text", "")):
            duration = badge["text"]
            break

    return RecentVideo(
        id=vid,
        url=f"https://www.youtube.com/watch?v={vid}",
        title=title,
        published_text=published,
        duration=duration,
        views=views,
        views_exact=False,
    )


def _legacy_video_renderer(vr: dict) -> RecentVideo:
    vid = vr.get("videoId", "")
    return RecentVideo(
        id=vid,
        url=f"https://www.youtube.com/watch?v={vid}",
        title=yt.text_of(vr.get("title")),
        published_text=yt.text_of(vr.get("publishedTimeText")) or None,
        duration=yt.text_of(vr.get("lengthText")) or None,
        views=yt.parse_count(yt.text_of(vr.get("viewCountText"))),
        views_exact=False,
    )


def get_recent_videos(
    ident: str, limit: int = 15, *, deep: bool = False, session=None
) -> list[RecentVideo]:
    session = session or yt.new_session()
    ident = yt.channel_ident(ident)

    if ident.startswith("@"):
        url = f"https://www.youtube.com/{ident}/videos"
    elif ident.startswith("http"):
        url = ident.rstrip("/") + "/videos"
    elif re.fullmatch(yt._CHANNEL_ID_RE, ident):
        url = f"https://www.youtube.com/channel/{ident}/videos"
    else:
        url = f"https://www.youtube.com/{ident.lstrip('/')}/videos"

    data = yt.page_data(session, url)

    videos: list[RecentVideo] = []
    seen: set[str] = set()
    for d in yt.iter_dicts(data):
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
                s = get_video_stats(rv.id, session=session)
            except yt.YTScrapeError:
                continue
            rv.views = s.views if s.views is not None else rv.views
            rv.views_exact = s.views is not None
            rv.likes = s.likes
            rv.comments = s.comments

    return videos


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="List a channel's recent uploads.")
    ap.add_argument("channel", help="@handle, channel URL, or UC... id")
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument(
        "--deep", action="store_true", help="fetch exact stats per video (slow)"
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        videos = get_recent_videos(args.channel, args.limit, deep=args.deep)
    except (yt.YTScrapeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([v.as_dict() for v in videos], indent=2))
        return 0

    for v in videos:
        tilde = "" if v.views_exact else "~"
        views = f"{tilde}{v.views:,}" if v.views is not None else "n/a"
        line = f"[{v.published_text or '?'}] {v.title[:70]}"
        stats = f"    {views} views"
        if v.likes is not None:
            stats += f" · {v.likes:,} likes"
        if v.comments is not None:
            stats += f" · {v.comments:,} comments"
        stats += f" · {v.duration or '?'} · {v.url}"
        print(line)
        print(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
