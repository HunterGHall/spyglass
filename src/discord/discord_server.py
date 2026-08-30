"""Look up a Discord server from an invite (or invite code).

Member count, online count, description, boost level, features, verification
level, vanity URL, icon/banner/splash - whatever the invite and the public
widget expose.

    python discord_server.py https://discord.gg/python
    python discord_server.py python --json
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys

import discord_common as dc


@dataclasses.dataclass
class Server:
    id: str
    name: str
    description: str | None
    created: str
    members: int | None
    online: int | None
    verification_level: str | None
    nsfw_level: str | None
    premium_tier: int | None
    premium_subscribers: int | None
    vanity_url: str | None
    features: list[str]
    icon_url: str | None
    banner_url: str | None
    splash_url: str | None
    invite_channel: str | None
    invite_url: str

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def get_server(invite: str) -> Server:
    code = dc.invite_code(invite)
    inv = dc.api_get(
        f"/invites/{code}", with_counts="true", with_expiration="true"
    )
    g = inv.get("guild") or {}
    gid = g.get("id", "")

    online = inv.get("approximate_presence_count")
    premium_subs = g.get("premium_subscription_count")
    # The widget can fill in a live online count if the invite didn't.
    if online is None and gid:
        try:
            online = dc.widget_json(gid).get("presence_count")
        except dc.DiscordError:
            pass

    vtier = g.get("verification_level")
    ntier = g.get("nsfw_level")
    ch = inv.get("channel") or {}

    return Server(
        id=gid,
        name=g.get("name", ""),
        description=g.get("description"),
        created=dc.snowflake_time(gid),
        members=inv.get("approximate_member_count"),
        online=online,
        verification_level=(
            dc.VERIFICATION_LEVELS[vtier] if isinstance(vtier, int)
            and vtier < len(dc.VERIFICATION_LEVELS) else None
        ),
        nsfw_level=(
            dc.NSFW_LEVELS[ntier] if isinstance(ntier, int)
            and ntier < len(dc.NSFW_LEVELS) else None
        ),
        premium_tier=g.get("premium_tier"),
        premium_subscribers=premium_subs,
        vanity_url=(
            f"https://discord.gg/{g['vanity_url_code']}"
            if g.get("vanity_url_code") else None
        ),
        features=g.get("features", []),
        icon_url=dc.cdn("icons", gid, g.get("icon")),
        banner_url=dc.cdn("banners", gid, g.get("banner")),
        splash_url=dc.cdn("splashes", gid, g.get("splash")),
        invite_channel=("#" + ch["name"]) if ch.get("name") else None,
        invite_url=f"https://discord.gg/{code}",
    )


def _print_human(s: Server) -> None:
    print(f"{s.name}   ({s.id})")
    if s.description:
        print(f"\n{s.description}\n")
    if s.members is not None:
        print(f"  members:       {s.members:,}")
    if s.online is not None:
        print(f"  online now:    {s.online:,}")
    print(f"  created:       {s.created}")
    if s.verification_level:
        print(f"  verification:  {s.verification_level}")
    if s.premium_tier is not None:
        subs = f" ({s.premium_subscribers} boosts)" if s.premium_subscribers else ""
        print(f"  boost tier:    {s.premium_tier}{subs}")
    if s.vanity_url:
        print(f"  vanity url:    {s.vanity_url}")
    if s.nsfw_level and s.nsfw_level != "default":
        print(f"  nsfw level:    {s.nsfw_level}")
    if s.features:
        print(f"  features:      {', '.join(sorted(s.features))}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Look up a Discord server by invite.")
    ap.add_argument("invite", help="invite URL or code")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        server = get_server(args.invite)
    except (dc.DiscordError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(server.as_dict(), indent=2))
    else:
        _print_human(server)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
