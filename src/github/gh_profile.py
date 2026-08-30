"""Fetch a GitHub user's or organization's public profile.

    python gh_profile.py torvalds
    python gh_profile.py github --json
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import sys

import gh_common as gh


@dataclasses.dataclass
class Account:
    kind: str            # User | Organization
    login: str
    id: int
    name: str | None
    company: str | None
    location: str | None
    bio: str | None
    blog: str | None
    email: str | None
    twitter: str | None
    hireable: bool | None
    followers: int
    following: int
    public_repos: int
    public_gists: int
    created_at: dt.datetime | None
    updated_at: dt.datetime | None
    avatar_url: str | None
    html_url: str

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        for k in ("created_at", "updated_at"):
            d[k] = getattr(self, k).isoformat() if getattr(self, k) else None
        return d


def _ts(value) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def get_profile(login: str) -> Account:
    login = login.strip().lstrip("@").rstrip("/").split("/")[-1]
    d = gh.api(f"/users/{login}")
    return Account(
        kind=d.get("type", "User"),
        login=d["login"],
        id=d["id"],
        name=d.get("name"),
        company=d.get("company"),
        location=d.get("location"),
        bio=d.get("bio"),
        blog=(d.get("blog") or None),
        email=d.get("email"),
        twitter=d.get("twitter_username"),
        hireable=d.get("hireable"),
        followers=d.get("followers", 0),
        following=d.get("following", 0),
        public_repos=d.get("public_repos", 0),
        public_gists=d.get("public_gists", 0),
        created_at=_ts(d.get("created_at")),
        updated_at=_ts(d.get("updated_at")),
        avatar_url=d.get("avatar_url"),
        html_url=d.get("html_url", f"https://github.com/{login}"),
    )


def _print_human(a: Account) -> None:
    print(f"{a.login}" + (f" — {a.name}" if a.name else "") + f"  [{a.kind}]")
    print(f"  {a.html_url}")
    if a.bio:
        print(f"\n{a.bio}\n")
    for label, val in (
        ("company", a.company),
        ("location", a.location),
        ("blog", a.blog),
        ("email", a.email),
        ("twitter", f"@{a.twitter}" if a.twitter else None),
    ):
        if val:
            print(f"  {label}:       {val}")
    print(f"  followers:   {a.followers:,}")
    print(f"  following:   {a.following:,}")
    print(f"  public repos:{a.public_repos:>6,}")
    print(f"  public gists:{a.public_gists:>6,}")
    if a.created_at:
        age = (dt.datetime.now(dt.timezone.utc) - a.created_at).days // 365
        print(f"  joined:      {a.created_at:%Y-%m-%d}  (~{age} yr ago)")
    if a.hireable:
        print("  hireable:    yes")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Get a GitHub user/org profile.")
    ap.add_argument("login")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        profile = get_profile(args.login)
    except (gh.GitHubError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(profile.as_dict(), indent=2))
    else:
        _print_human(profile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
