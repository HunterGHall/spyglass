"""Fetch a Reddit user's or subreddit's public profile.

    python reddit_profile.py spez
    python reddit_profile.py r/python --json
    python reddit_profile.py u/spez

A bare name or `u/name` is treated as a user; `r/name` as a subreddit.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import sys

import reddit_common as rc


@dataclasses.dataclass
class UserProfile:
    kind: str
    name: str
    id: str
    created: dt.datetime | None
    age_days: int | None
    total_karma: int
    link_karma: int
    comment_karma: int
    awardee_karma: int
    awarder_karma: int
    is_gold: bool
    is_mod: bool
    is_employee: bool
    verified: bool
    has_verified_email: bool
    icon_url: str | None
    profile_title: str | None
    profile_description: str | None
    profile_subscribers: int | None

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["created"] = self.created.isoformat() if self.created else None
        return d


@dataclasses.dataclass
class Subreddit:
    kind: str
    name: str
    id: str
    title: str
    public_description: str
    created: dt.datetime | None
    age_days: int | None
    subscribers: int
    active_users: int | None
    over18: bool
    subreddit_type: str
    lang: str | None
    icon_url: str | None

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["created"] = self.created.isoformat() if self.created else None
        return d


def _ts(created_utc) -> dt.datetime | None:
    try:
        return dt.datetime.fromtimestamp(float(created_utc), dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def _age(created: dt.datetime | None) -> int | None:
    return (dt.datetime.now(dt.timezone.utc) - created).days if created else None


def get_user_profile(name: str) -> UserProfile:
    name = rc.clean_name(name, "user")
    d = rc.api_get(f"/user/{name}/about")["data"]
    sub = d.get("subreddit") or {}
    created = _ts(d.get("created_utc"))
    return UserProfile(
        kind="user",
        name=d.get("name", name),
        id=d.get("id", ""),
        created=created,
        age_days=_age(created),
        total_karma=int(d.get("total_karma", 0)),
        link_karma=int(d.get("link_karma", 0)),
        comment_karma=int(d.get("comment_karma", 0)),
        awardee_karma=int(d.get("awardee_karma", 0)),
        awarder_karma=int(d.get("awarder_karma", 0)),
        is_gold=bool(d.get("is_gold")),
        is_mod=bool(d.get("is_mod")),
        is_employee=bool(d.get("is_employee")),
        verified=bool(d.get("verified")),
        has_verified_email=bool(d.get("has_verified_email")),
        icon_url=(d.get("icon_img") or "").split("?")[0] or None,
        profile_title=sub.get("title") or None,
        profile_description=sub.get("public_description") or None,
        profile_subscribers=sub.get("subscribers"),
    )


def get_subreddit(name: str) -> Subreddit:
    name = rc.clean_name(name, "subreddit")
    d = rc.api_get(f"/r/{name}/about")["data"]
    created = _ts(d.get("created_utc"))
    return Subreddit(
        kind="subreddit",
        name=d.get("display_name", name),
        id=d.get("id", ""),
        title=d.get("title", ""),
        public_description=d.get("public_description", ""),
        created=created,
        age_days=_age(created),
        subscribers=int(d.get("subscribers", 0)),
        active_users=d.get("active_user_count"),
        over18=bool(d.get("over18")),
        subreddit_type=d.get("subreddit_type", ""),
        lang=d.get("lang") or None,
        icon_url=(d.get("community_icon") or d.get("icon_img") or "").split("?")[0]
        or None,
    )


def get_profile(ref: str):
    if ref.strip().lower().startswith(("r/", "/r/")) or "/r/" in ref:
        return get_subreddit(ref)
    return get_user_profile(ref)


def _print_user(p: UserProfile) -> None:
    print(f"u/{p.name}" + ("  [employee]" if p.is_employee else ""))
    if p.created:
        print(f"  created:       {p.created:%Y-%m-%d}  ({p.age_days} days ago)")
    print(f"  total karma:   {p.total_karma:,}")
    print(f"    link:        {p.link_karma:,}")
    print(f"    comment:     {p.comment_karma:,}")
    print(f"    awardee:     {p.awardee_karma:,}")
    flags = [n for n, v in (
        ("gold", p.is_gold), ("mod", p.is_mod), ("verified-email",
         p.has_verified_email)) if v]
    if flags:
        print(f"  flags:         {', '.join(flags)}")
    if p.profile_description:
        print(f"  profile:       {p.profile_description}")


def _print_sub(s: Subreddit) -> None:
    print(f"r/{s.name} — {s.title}" + ("  [NSFW]" if s.over18 else ""))
    if s.public_description:
        print(f"\n{s.public_description}\n")
    print(f"  subscribers:  {s.subscribers:,}")
    if s.active_users is not None:
        print(f"  online now:   {s.active_users:,}")
    if s.created:
        print(f"  created:      {s.created:%Y-%m-%d}  ({s.age_days} days ago)")
    print(f"  type:         {s.subreddit_type}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Get a Reddit user or subreddit profile.")
    ap.add_argument("ref", help="username, u/name, or r/subreddit")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        profile = get_profile(args.ref)
    except (rc.RedditError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(profile.as_dict(), indent=2))
    elif isinstance(profile, Subreddit):
        _print_sub(profile)
    else:
        _print_user(profile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
