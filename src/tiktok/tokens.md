# TikTok token (optional)

`tiktok.py` scrapes the JSON blob TikTok server-renders into its profile and
video pages (`__UNIVERSAL_DATA_FOR_REHYDRATION__`). No API key, no account.

- `count` and `profile` work **bare** from almost any IP.
- `post` (single-video stats) is bot-gated from datacenter / VPN IPs - TikTok
  returns an empty `webapp.video-detail` (status `10204`). A real browser
  cookie usually fixes it.

## Cookie

Open <https://www.tiktok.com> in a logged-in (or even logged-out) browser, copy
the full `Cookie` request header from any `tiktok.com` request in the network
tab, and put it in `tokens.conf` (repo root):

```
TIKTOK_COOKIE = tt_webid=...; ttwid=...; msToken=...; sessionid=...
```

A `TIKTOK_COOKIE` environment variable overrides the file. Use a throwaway
account; scraping is against TikTok's ToS.

## What TikTok does **not** expose here

Follower / following **lists** and the per-user video feed are served only
through signed API calls (`X-Bogus` / `_signature` / `msToken`), which need a
browser-grade signer this file doesn't ship. `count` / `profile` give the
follower and following **counts**; `post` gives one video's stats by id or URL.
