"""Fetch a YouTube channel's public profile and lifetime stats.

    python yt_channel.py @MrBeast
    python yt_channel.py "https://www.youtube.com/channel/UC..." --json

Subscriber counts are rounded by YouTube itself ("515M subscribers"); the
lifetime view count and video count are exact.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import re
import sys
import urllib.parse

import yt_common as yt


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
    links: list[dict]
    is_verified: bool
    is_family_safe: bool | None
    avatar_url: str | None
    banner_url: str | None

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
    """youtube.com/redirect?...&q=<real url>  ->  <real url>."""
    if "youtube.com/redirect" in url:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("q")
        if q:
            return q[0]
    return url


def _links(about: dict) -> list[dict]:
    out = []
    for link in about.get("links") or []:
        vm = link.get("channelExternalLinkViewModel") or link
        title = yt.text_of(vm.get("title"))
        display = yt.text_of(vm.get("link"))
        target = yt.deep_find(vm.get("link", {}), "url") or display
        if title or display:
            out.append({"title": title, "url": _unwrap_redirect(target)})
    return out


def _verified(data: dict) -> bool:
    for d in yt.iter_dicts(data):
        style = d.get("badgeStyle") or d.get("style")
        if style and "VERIFIED" in str(style):
            return True
        if d.get("iconType") in ("CHECK_CIRCLE_FILLED", "AUDIO_BADGE"):
            if d.get("iconType") == "CHECK_CIRCLE_FILLED":
                return True
    return False


def get_channel(ident: str, session=None) -> Channel:
    session = session or yt.new_session()
    ident = yt.channel_ident(ident)

    if ident.startswith("@"):
        url = f"https://www.youtube.com/{ident}/about"
    elif ident.startswith("http"):
        url = ident.rstrip("/") + "/about"
    elif re.fullmatch(yt._CHANNEL_ID_RE, ident):
        url = f"https://www.youtube.com/channel/{ident}/about"
    else:
        url = f"https://www.youtube.com/{ident.lstrip('/')}/about"

    data = yt.page_data(session, url)

    about = yt.deep_find(data, "aboutChannelViewModel") or {}
    meta = (data.get("metadata") or {}).get("channelMetadataRenderer") or {}

    subs_text = yt.text_of(about.get("subscriberCountText")) or None
    joined = _parse_joined(yt.text_of(about.get("joinedDateText")))

    avatar = None
    thumbs = yt.deep_find(meta, "thumbnails") or []
    if thumbs:
        avatar = thumbs[-1].get("url")

    banner = None
    for d in yt.iter_dicts(data.get("header", {})):
        if "banner" in d and isinstance(d["banner"], dict):
            imgs = yt.deep_find(d["banner"], "sources") or yt.deep_find(
                d["banner"], "thumbnails"
            )
            if imgs:
                banner = imgs[-1].get("url")
                break

    return Channel(
        id=about.get("channelId") or meta.get("externalId") or "",
        title=yt.text_of(about.get("title")) or meta.get("title") or "",
        handle=(yt.text_of(about.get("channelHandleText")) or None)
        or _handle_from_url(meta.get("vanityChannelUrl")),
        description=about.get("description") or meta.get("description") or "",
        subscribers=yt.parse_count(subs_text),
        subscribers_text=subs_text,
        videos=yt.first_int(yt.text_of(about.get("videoCountText"))),
        views=yt.first_int(yt.text_of(about.get("viewCountText"))),
        joined=joined,
        country=yt.text_of(about.get("country")) or None,
        keywords=meta.get("keywords") or None,
        links=_links(about),
        is_verified=_verified(data),
        is_family_safe=meta.get("isFamilySafe"),
        avatar_url=avatar,
        banner_url=banner,
    )


def _handle_from_url(url: str | None) -> str | None:
    if url and "/@" in url:
        return "@" + url.split("/@", 1)[1].strip("/")
    return None


def _print_human(c: Channel) -> None:
    mark = "  [verified]" if c.is_verified else ""
    print(f"{c.title}{mark}" + (f"  ({c.handle})" if c.handle else ""))
    print(f"  {c.id}")
    if c.description:
        print(f"\n{c.description.strip()[:500]}\n")
    subs = c.subscribers_text or (
        f"{c.subscribers:,}" if c.subscribers is not None else "n/a"
    )
    print(f"  subscribers: {subs}")
    print(f"  videos:      {c.videos:,}" if c.videos is not None else "  videos:      n/a")
    print(f"  total views: {c.views:,}" if c.views is not None else "  total views: n/a")
    if c.joined:
        print(f"  joined:      {c.joined:%B %d, %Y}")
    if c.country:
        print(f"  country:     {c.country}")
    for link in c.links:
        print(f"  link:        {link['title']}: {link['url']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Get a YouTube channel's profile.")
    ap.add_argument("channel", help="@handle, channel URL, or UC... id")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        channel = get_channel(args.channel)
    except (yt.YTScrapeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(channel.as_dict(), indent=2))
    else:
        _print_human(channel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
