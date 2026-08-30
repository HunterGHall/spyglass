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

Each folder has the same four tools (YouTube names in parentheses):

| Script | Gives |
| --- | --- |
| `*_follower_count.py` / `yt_subscriber_count.py` | follower / subscriber count |
| `*_profile.py` / `yt_channel.py` | bio/description, links, lifetime counts, flags |
| `*_post_stats.py` / `yt_video_stats.py` | one post/video: views, likes, comments, and (YT) engagement rates, format/resolution/HDR/codecs, captions, live & age flags |
| `*_recent_posts.py` / `yt_recent_videos.py` | recent posts/uploads, each with stats (`yt_recent_videos.py --deep` for exact per-video numbers) |

All take `--json` and can be imported (`from x_profile import get_profile`,
`from ig_profile import get_profile`, `from yt_video_stats import get_video_stats`).

Twitter and YouTube hit the web endpoints directly with `requests`. Instagram
goes through [instagrapi](https://github.com/subzeroid/instagrapi), which speaks
the private mobile API and keeps working from datacenter IPs given a real
`sessionid`.

## Tokens

Each platform's `*_common.py` reads a single git-ignored `tokens.conf` at the
repo root (the loader is inlined in each file — there is no shared module). Copy
in the keys you need:

```
X_AUTH_TOKEN = ...
X_CT0 = ...
IG_SESSIONID = ...
YT_COOKIE = ...
```

X works logged-out (its timeline just lags); Instagram requires `IG_SESSIONID`;
YouTube needs nothing unless it starts bot-gating your IP, in which case set
`YT_COOKIE`. See each platform's `tokens.md` for where to find the values. A
matching environment variable overrides the file.

## Caveats

These endpoints are unofficial, undocumented, rate-limited per IP, change
without notice, and are against each platform's Terms of Service. Use throwaway
accounts. For anything reliable, use the official APIs.

To stay under the rate limits, requests are paced a random **3–7 s apart**
(Twitter/YouTube via a `PacedSession`; Instagram via instagrapi's
`delay_range`). Multi-request commands like `*_recent_posts.py --limit 100` take
a while. Override with the `PACE_MIN` / `PACE_MAX` environment variables.
