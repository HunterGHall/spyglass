# spyglass

One Python file per platform that pulls **public** profile and engagement data
from social sites — using the same private endpoints their own apps call, or a
real API where one exists. No paid keys.

```bash
pip install -r requirements.txt   # requests, plus instagrapi for Instagram
```

## Platforms

Each `src/<platform>/<platform>.py` is a self-contained multi-command CLI. Run
`python src/<platform>/<platform>.py <command> ...`; every command takes `--json`.

| File | Commands | Token setup |
| --- | --- | --- |
| [`src/twitter/twitter.py`](src/twitter/twitter.py) | `followers` (count, or list with `--limit`) · `profile` · `post` · `recent` | [tokens.md](src/twitter/tokens.md) |
| [`src/instagram/instagram.py`](src/instagram/instagram.py) | `count` · `profile` · `followers` · `post` · `recent` | [tokens.md](src/instagram/tokens.md) |
| [`src/youtube/youtube.py`](src/youtube/youtube.py) | `subs` · `channel` · `video` · `recent` | [tokens.md](src/youtube/tokens.md) (usually none) |
| [`src/reddit/reddit.py`](src/reddit/reddit.py) | `karma` · `profile` (user *or* `r/sub`) · `post` · `recent` | [tokens.md](src/reddit/tokens.md) |
| [`src/discord/discord.py`](src/discord/discord.py) | `server` · `members` · `user` · `widget` | [tokens.md](src/discord/tokens.md) (mostly none) |
| [`src/github/github.py`](src/github/github.py) | `followers` (count, or list with `--limit`) · `profile` · `repo` · `recent` · `commits` · `releases` · `activity` · `commit` | [tokens.md](src/github/tokens.md) (optional) |
| [`src/twitch/twitch.py`](src/twitch/twitch.py) | `followers` (count) · `profile` · `stream` · `video` · `clip` · `recent` | [tokens.md](src/twitch/tokens.md) (none) |
| [`src/tiktok/tiktok.py`](src/tiktok/tiktok.py) | `count` · `profile` · `post` | [tokens.md](src/tiktok/tokens.md) (optional) |

Examples:

```bash
python src/youtube/youtube.py video dQw4w9WgXcQ --json
python src/twitter/twitter.py recent nasa --limit 20
python src/instagram/instagram.py followers natgeo --limit 100
python src/github/github.py followers torvalds --limit 200      # follower logins
python src/twitter/twitter.py followers nasa --limit 200        # needs X login cookies
python src/github/github.py activity cli/cli --lost      # force-pushed / deleted commits
python src/github/github.py releases pallets/flask
python src/twitch/twitch.py stream jynxzi                # live? title / game / viewers
python src/twitch/twitch.py recent shroud --clips        # top clips, last 30 days
python src/tiktok/tiktok.py profile mrbeast --json
```

### Listing a user's followers / following

`followers --limit N` returns the actual accounts, not just a number, on the
three platforms that expose them. Add `--following` to list who the user
follows instead. Importable as `get_followers(user, limit)` and
`get_following(user, limit)`.

| Platform | Command | Notes |
| --- | --- | --- |
| GitHub | `github.py followers <login> --limit N [--following]` | official API, no token, fully paginated |
| Instagram | `instagram.py followers <user> --limit N [--following]` | needs `IG_SESSIONID`; Instagram throttles hard past a few hundred |
| Twitter / X | `twitter.py followers <user> --limit N [--following]` | login-gated — needs valid `X_AUTH_TOKEN` / `X_CT0` (uses the v1.1 `list.json` endpoints; ~15 pages / 15 min) |

Reddit, YouTube, Discord, **Twitch** and **TikTok** don't publish follower /
subscriber / following identities for arbitrary accounts (Twitch pulled the
lists in 2022; TikTok serves them only through signed requests), so those files
stop at counts.

Each file is also importable — `from youtube import get_video_stats`,
`from github import get_commits`, etc.

### GitHub history commands

- `commits <owner/repo>` — commit log, with `--branch`, `--path`, `--author`,
  `--since`, `--until`.
- `releases <owner/repo>` — every release **and** every plain git tag (past
  versions), newest first, with download counts.
- `activity <owner/repo>` — the ref-level activity log: pushes, **force-pushes**,
  **branch deletions**, merges, each with the before/after SHA. `--lost`
  resolves the SHAs orphaned by force-pushes and deleted branches and lists
  those now-dangling commits.
- `commit <owner/repo> <sha>` — one commit by SHA, **including dangling ones**
  GitHub still keeps reachable after a force-push or branch delete.

## How each platform is reached

- **GitHub** — the official REST API.
- **Twitter, YouTube, Reddit, Discord, TikTok** — the undocumented web / JSON
  endpoints their own sites use, hit directly with `requests`.
- **Twitch** — the public GraphQL endpoint the web player calls, with Twitch's
  static web Client-ID. No app registration.
- **Instagram** — [instagrapi](https://github.com/subzeroid/instagrapi), the
  private mobile API, which keeps working from datacenter IPs given a real
  `sessionid`.

## Tokens

Every file reads one git-ignored `tokens.conf` at the repo root (the loader is
inlined in each — no shared module). Add the keys you need:

```
X_AUTH_TOKEN = ...
X_CT0 = ...
IG_SESSIONID = ...
YT_COOKIE = ...
REDDIT_CLIENT_ID = ...
REDDIT_CLIENT_SECRET = ...
DISCORD_TOKEN = ...
DISCORD_USER_TOKEN = ...
GITHUB_TOKEN = ...
TIKTOK_COOKIE = ...
```

- **X** works logged-out (its timeline just lags); cookies remove the lag.
- **Instagram** requires `IG_SESSIONID`.
- **YouTube** needs nothing unless it bot-gates your IP → `YT_COOKIE`.
- **Reddit** needs `REDDIT_CLIENT_ID` (+ secret for a "script" app) from a
  datacenter / VPN IP; works bare from a home connection.
- **Discord** — no token for `server` / `members` / `widget`; `user` needs
  `DISCORD_TOKEN` (bot), plus `DISCORD_USER_TOKEN` for bio / connections.
- **GitHub** — optional `GITHUB_TOKEN` lifts the limit from 60/hr to 5,000/hr.
- **Twitch** — nothing; `TWITCH_OAUTH` only helps if an IP starts failing
  integrity checks.
- **TikTok** — `count` / `profile` need nothing; `post` is IP-gated on
  datacenters → `TIKTOK_COOKIE` (a browser `Cookie` header).

A matching environment variable overrides the file. See each `tokens.md`.

## Caveats

The scraped endpoints are unofficial, undocumented, rate-limited per IP, subject
to change, and against each platform's Terms of Service. Use throwaway accounts;
for anything that must be reliable, use the official APIs.

Requests are paced a random **3–7 s apart** (via a `PacedSession`, or
instagrapi's `delay_range`; GitHub only paces if you ask). Override with the
`PACE_MIN` / `PACE_MAX` environment variables.
