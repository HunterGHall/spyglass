"""Fetch an Instagram user's public profile: bio, link, category, counts.

    python ig_profile.py nasa
    python ig_profile.py @natgeo --json

Needs IG_SESSIONID in tokens.conf (see tokens.md).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys

import ig_common as ig


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


def fetch_user(username: str, cl=None):
    """Return the raw instagrapi User model."""
    cl = cl or ig.get_client()
    return ig.wrap_errors(cl.user_info_by_username_v1, ig.clean_username(username))


def _address(user) -> str | None:
    parts = [
        getattr(user, "address_street", "") or "",
        getattr(user, "city_name", "") or "",
        getattr(user, "zip", "") or "",
    ]
    joined = ", ".join(p for p in parts if p)
    return joined or None


def get_profile(username: str, cl=None) -> Profile:
    u = fetch_user(username, cl)
    return Profile(
        id=str(u.pk),
        username=u.username,
        full_name=u.full_name or "",
        bio=u.biography or "",
        external_url=str(u.external_url) if u.external_url else None,
        category=getattr(u, "category_name", None) or getattr(u, "category", None) or None,
        address=_address(u),
        is_private=bool(u.is_private),
        is_verified=bool(u.is_verified),
        is_business=bool(getattr(u, "is_business", False)),
        followers=int(u.follower_count),
        following=int(u.following_count),
        posts=int(u.media_count),
        profile_pic_url=str(getattr(u, "profile_pic_url_hd", None) or u.profile_pic_url),
    )


def _print_human(p: Profile) -> None:
    marks = []
    if p.is_verified:
        marks.append("verified")
    if p.is_private:
        marks.append("private")
    if p.is_business:
        marks.append("business")
    suffix = f"  [{', '.join(marks)}]" if marks else ""
    print(f"@{p.username} - {p.full_name}{suffix}")
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Get an Instagram user's profile.")
    ap.add_argument("username")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        profile = get_profile(args.username)
    except (ig.IGScrapeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(profile.as_dict(), indent=2))
    else:
        _print_human(profile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
