"""Fetch an X (Twitter) user's public profile: bio, location, join date, counts.

No developer API key. See x_common.py for the caveats.

    python x_profile.py nasa
    python x_profile.py @jack --json
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import sys

import x_common as xc

_TWITTER_TS = "%a %b %d %H:%M:%S %z %Y"  # e.g. "Wed Dec 19 20:20:32 +0000 2007"


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
    verified_type: str | None  # "Business" / "Government" / None
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


def _parse_joined(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.strptime(value, _TWITTER_TS)
    except ValueError:
        return None


def _first_url(legacy: dict) -> str | None:
    urls = legacy.get("entities", {}).get("url", {}).get("urls") or []
    if urls:
        return urls[0].get("expanded_url") or urls[0].get("url")
    return legacy.get("url")


def get_profile(username: str, session=None) -> Profile:
    username = xc.clean_username(username)
    session = session or xc.new_session()

    payload = xc.graphql(
        session,
        "UserByScreenName",
        {"screen_name": username, "withSafetyModeUserFields": True},
    )
    result = xc.deep_find(payload, "result") or {}
    if result.get("__typename") == "UserUnavailable":
        raise xc.XScrapeError(f"@{username} is unavailable (suspended or protected)")

    legacy = result.get("legacy")
    if not legacy:
        raise xc.XScrapeError(f"@{username} not found")

    verified_type = legacy.get("verified_type") or (
        "Blue" if result.get("is_blue_verified") else None
    )

    return Profile(
        id=result.get("rest_id", ""),
        username=legacy.get("screen_name", username),
        name=legacy.get("name", ""),
        bio=legacy.get("description", ""),
        location=(legacy.get("location") or None),
        website=_first_url(legacy),
        joined=_parse_joined(legacy.get("created_at")),
        verified=bool(legacy.get("verified") or result.get("is_blue_verified")),
        verified_type=verified_type,
        protected=bool(legacy.get("protected")),
        followers=int(legacy.get("followers_count", 0)),
        following=int(legacy.get("friends_count", 0)),
        posts=int(legacy.get("statuses_count", 0)),
        likes=int(legacy.get("favourites_count", 0)),
        listed=int(legacy.get("listed_count", 0)),
        media=int(legacy.get("media_count", 0)),
        profile_image_url=(
            legacy.get("profile_image_url_https", "").replace("_normal", "") or None
        ),
        banner_image_url=legacy.get("profile_banner_url"),
    )


def _print_human(p: Profile) -> None:
    joined = p.joined.strftime("%B %Y") if p.joined else "unknown"
    badge = f"  [{p.verified_type} verified]" if p.verified_type else ""
    print(f"@{p.username} — {p.name}{badge}")
    if p.bio:
        print(f"\n{p.bio}\n")
    if p.location:
        print(f"  location:  {p.location}")
    if p.website:
        print(f"  website:   {p.website}")
    print(f"  joined:    {joined}")
    print(f"  followers: {p.followers:,}")
    print(f"  following: {p.following:,}")
    print(f"  posts:     {p.posts:,}")
    print(f"  likes:     {p.likes:,}")
    print(f"  listed:    {p.listed:,}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Get an X user's public profile.")
    ap.add_argument("username")
    ap.add_argument("--json", action="store_true", help="print raw JSON")
    args = ap.parse_args(argv)

    try:
        profile = get_profile(args.username)
    except (xc.XScrapeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(profile.as_dict(), indent=2))
    else:
        _print_human(profile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
