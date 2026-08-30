# Getting your Reddit credentials

Reddit's public `.json` endpoints work from home connections but return **403**
from datacenter, cloud, and VPN IPs. The fix is a free OAuth app (100 req/min).

## Steps

1. Go to <https://www.reddit.com/prefs/apps> (logged in).
2. Click **"are you a developer? create an app…"**.
3. Fill in:
   - **name:** anything (e.g. `dossier`)
   - **type:** choose **script** (personal use) or **installed app**
   - **redirect uri:** `http://localhost:8080` (required but unused)
4. Create it. You'll see:
   - the **client id** — the short string just under the app name / "personal
     use script"
   - the **secret** — shown as `secret` (only for a **script** app; an
     **installed app** has no secret)

## Use them

In `tokens.conf` (repo root):

```
REDDIT_CLIENT_ID = your_client_id
REDDIT_CLIENT_SECRET = your_secret        # leave blank for an "installed app"
```

The scripts request an app-only OAuth token automatically and route through
`oauth.reddit.com`. With no `REDDIT_CLIENT_ID` set they fall back to the public
`.json` host (fine from a home IP).

## Don't leak them

The secret is a credential for your app. `tokens.conf` is git-ignored — keep it
that way.
