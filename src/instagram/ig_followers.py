"""List the followers (or the following) of an Instagram account.

    python ig_followers.py natgeo --limit 100
    python ig_followers.py natgeo --following --limit 50 --json

Needs IG_SESSIONID in tokens.conf (see tokens.md).

Reality check: Instagram rate-limits this hard. A few hundred is usually fine;
pulling every follower of a big account will get the session throttled or
temporarily blocked long before it finishes. Private accounts only work if the
logged-in account follows them. `--limit 0` means "no cap" - use with care.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys

import ig_common as ig
from ig_profile import fetch_user


@dataclasses.dataclass
class Follower:
    pk: str
    username: str
    full_name: str
    is_private: bool
    is_verified: bool
    profile_pic_url: str | None

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def _to_follower(u) -> Follower:
    return Follower(
        pk=str(u.pk),
        username=u.username,
        full_name=u.full_name or "",
        is_private=bool(u.is_private),
        is_verified=bool(u.is_verified),
        profile_pic_url=str(u.profile_pic_url) if u.profile_pic_url else None,
    )


def get_followers(
    username: str, limit: int = 100, *, following: bool = False, cl=None
) -> list[Follower]:
    cl = cl or ig.get_client()
    user = fetch_user(username, cl)

    # For a private account this only works if the logged-in account follows
    # them; otherwise instagrapi raises, which wrap_errors turns into a clear
    # IGScrapeError.
    method = cl.user_following if following else cl.user_followers
    result = ig.wrap_errors(method, str(user.pk), amount=max(limit, 0))
    users = list(result.values())
    if limit:
        users = users[:limit]
    return [_to_follower(u) for u in users]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="List an Instagram account's followers or following."
    )
    ap.add_argument("username")
    ap.add_argument("--limit", type=int, default=100, help="0 = no cap (risky)")
    ap.add_argument(
        "--following", action="store_true", help="list who they follow instead"
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        people = get_followers(
            args.username, args.limit, following=args.following
        )
    except (ig.IGScrapeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([p.as_dict() for p in people], indent=2))
        return 0

    label = "following" if args.following else "followers"
    print(f"@{args.username.lstrip('@')} — {len(people)} {label} shown\n")
    for p in people:
        marks = "".join(
            m for m, v in (("✓", p.is_verified), ("🔒", p.is_private)) if v
        )
        name = f"  ({p.full_name})" if p.full_name else ""
        print(f"  @{p.username}{name} {marks}".rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
