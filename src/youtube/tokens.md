# YouTube token (usually not needed)

The YouTube scripts read public data straight from the page HTML and the
InnerTube API — **no token required** in most cases. `yt_channel.py`,
`yt_subscriber_count.py`, and `yt_recent_videos.py` (without `--deep`) work
with no setup.

You only need a cookie when YouTube starts answering with *"Sign in to confirm
you're not a bot"* — which it does for watch-page requests from datacenter,
cloud, and VPN IP ranges. That mainly affects `yt_video_stats.py` and
`yt_recent_videos.py --deep`.

Use a **throwaway account**.

## Steps

1. Log into <https://www.youtube.com>.
2. Open DevTools with `F12`, go to the **Network** tab.
3. Reload the page, click the first `www.youtube.com` document request.
4. Under **Request Headers**, find **`cookie:`** and copy the entire value
   (one long line — it contains `SID`, `SAPISID`, `HSID`, `SSID`, `__Secure-*`,
   etc.). The scripts derive the `SAPISIDHASH` authorization header from it
   automatically.

## Use it

Paste the whole cookie string into `tokens.conf` (repo root), on one line:

```
YT_COOKIE = VISITOR_INFO1_LIVE=abc; SID=xxxx; SAPISID=yyyy; HSID=zzzz; ...
```

A real `YT_COOKIE` environment variable overrides the file.

## Don't leak it

That cookie string is full access to the Google account. `tokens.conf` is
git-ignored — keep it that way. It expires when the browser session ends.
