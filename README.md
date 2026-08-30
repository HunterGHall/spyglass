# dossier

Small Python scripts that pull **public** profile and engagement data from
social platforms, using the same private endpoints their own apps call — no
developer API keys.

```bash
pip install -r requirements.txt   # requests, plus instagrapi for Instagram
```

## Platforms

| | Scripts | Token setup |
| --- | --- | --- |
| **X / Twitter** | [`src/twitter/`](src/twitter/) | [src/twitter/tokens.md](src/twitter/tokens.md) |
| **Instagram** | [`src/instagram/`](src/instagram/) | [src/instagram/tokens.md](src/instagram/tokens.md) |
| **YouTube** | [`src/youtube/`](src/youtube/) | [src/youtube/tokens.md](src/youtube/tokens.md) (usually none) |
| **Reddit** | [`src/reddit/`](src/reddit/) | [src/reddit/tokens.md](src/reddit/tokens.md) |
| **Discord** | [`src/discord/`](src/discord/) | [src/discord/tokens.md](src/discord/tokens.md) (mostly none) |
| **GitHub** | [`src/github/`](src/github/) | [src/github/tokens.md](src/github/tokens.md) (optional) |

Each folder has the same four tools. Names vary where the platform's concepts
differ:

| Twitter / Instagram | YouTube | Reddit | Discord | GitHub |
| --- | --- | --- | --- | --- |
| `*_follower_count.py` | `yt_subscriber_count.py` | `reddit_karma.py` | `discord_member_count.py` | `gh_follower_count.py` |
| `*_profile.py` | `yt_channel.py` | `reddit_profile.py` (user *or* `r/sub`) | `discord_server.py` | `gh_profile.py` (user *or* org) |
| `*_post_stats.py` | `yt_video_stats.py` | `reddit_post_stats.py` | `discord_user.py` | `gh_repo_stats.py` |
| `*_recent_posts.py` | `yt_recent_videos.py` | `reddit_recent_posts.py` | `discord_widget.py` | `gh_recent_posts.py` (events / `--repos`) |

All take `--json` and can be imported (`from x_profile import get_profile`,
`from yt_video_stats import get_video_stats`, `from reddit_profile import
get_profile`, …).

Instagram also has [`ig_followers.py`](src/instagram/ig_followers.py) — lists
the actual followers (or `--following`) of an account. Instagram throttles
follower enumeration aggressively, so keep `--limit` modest.

GitHub uses its official REST API. Twitter, YouTube, Reddit and Discord hit the
undocumented web/JSON endpoints directly with `requests`. Instagram goes through
[instagrapi](https://github.com/subzeroid/instagrapi), which speaks the private
mobile API and keeps working from datacenter IPs given a real `sessionid`.

## Tokens

Each platform's `*_common.py` reads a single git-ignored `tokens.conf` at the
repo root (the loader is inlined in each file — there is no shared module). Copy
in the keys you need:

```
X_AUTH_TOKEN = ...
X_CT0 = ...
IG_SESSIONID = ...
YT_COOKIE = ...
REDDIT_CLIENT_ID = ...
REDDIT_CLIENT_SECRET = ...
DISCORD_TOKEN = ...
GITHUB_TOKEN = ...
```

- **X** works logged-out (its timeline just lags).
- **Instagram** requires `IG_SESSIONID`.
- **YouTube** needs nothing unless it starts bot-gating your IP → `YT_COOKIE`.
- **Reddit** needs `REDDIT_CLIENT_ID` (+ secret for a "script" app) from a
  datacenter/VPN IP; works bare from a home connection.
- **Discord** needs `DISCORD_TOKEN` only for `discord_user.py`.
- **GitHub** works with nothing; `GITHUB_TOKEN` lifts the limit from 60/hr to
  5,000/hr.

See each platform's `tokens.md` for where to find the values. A matching
environment variable overrides the file.

## Caveats

These endpoints are unofficial, undocumented, rate-limited per IP, change
without notice, and are against each platform's Terms of Service. Use throwaway
accounts. For anything reliable, use the official APIs.

To stay under the rate limits, requests are paced a random **3–7 s apart**
(most platforms via a `PacedSession`; Instagram via instagrapi's `delay_range`).
Multi-request commands like `*_recent_posts.py --limit 100` take a while.
Override with the `PACE_MIN` / `PACE_MAX` environment variables.
