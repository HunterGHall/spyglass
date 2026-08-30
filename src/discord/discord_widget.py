"""Snapshot a Discord server's public widget: who's online and in voice.

This is the closest thing Discord offers to "recent activity" without being in
the server - it only works if the server has the widget enabled, and it lists
at most 100 online members.

    python discord_widget.py 267624335836053506
    python discord_widget.py https://discord.gg/python --json
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys

import discord_common as dc


@dataclasses.dataclass
class Widget:
    id: str
    name: str
    instant_invite: str | None
    online: int
    members: list[dict]          # {name, status, game}
    voice_channels: list[dict]   # {name, members: [names]}

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def get_widget(server: str) -> Widget:
    server = server.strip()
    if server.isdigit():
        gid = server
    else:
        # resolve an invite to its guild id
        code = dc.invite_code(server)
        inv = dc.api_get(f"/invites/{code}")
        gid = (inv.get("guild") or {}).get("id", "")
        if not gid:
            raise dc.DiscordError("could not resolve that invite to a server id")

    w = dc.widget_json(gid)

    chan_names = {c["id"]: c["name"] for c in w.get("channels", [])}
    voice: dict[str, list[str]] = {}
    members = []
    for m in w.get("members", []):
        game = (m.get("game") or {}).get("name")
        members.append(
            {"name": m.get("username", ""), "status": m.get("status", ""), "game": game}
        )
        cid = m.get("channel_id")
        if cid:
            voice.setdefault(cid, []).append(m.get("username", ""))

    return Widget(
        id=w.get("id", gid),
        name=w.get("name", ""),
        instant_invite=w.get("instant_invite"),
        online=w.get("presence_count", len(members)),
        members=members,
        voice_channels=[
            {"name": chan_names.get(cid, cid), "members": names}
            for cid, names in voice.items()
        ],
    )


def _print_human(w: Widget) -> None:
    print(f"{w.name}  ({w.id})")
    print(f"  online now: {w.online:,}")
    if w.instant_invite:
        print(f"  invite:     {w.instant_invite}")
    if w.voice_channels:
        print("\n  in voice:")
        for vc in w.voice_channels:
            print(f"    🔊 {vc['name']}: {', '.join(vc['members'])}")
    shown = [m for m in w.members if not m["game"]][:15]
    playing = [m for m in w.members if m["game"]]
    if playing:
        print("\n  playing:")
        for m in playing[:15]:
            print(f"    {m['name']} — {m['game']}")
    if shown:
        print("\n  online (sample): " + ", ".join(m["name"] for m in shown))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read a Discord server's public widget.")
    ap.add_argument("server", help="server id, or an invite URL/code")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        widget = get_widget(args.server)
    except (dc.DiscordError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(widget.as_dict(), indent=2))
    else:
        _print_human(widget)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
