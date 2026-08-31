# Twitch tokens (none required)

`twitch.py` calls the same public GraphQL endpoint (`gql.twitch.tv/gql`) the
logged-out twitch.tv web player uses, with Twitch's static web **Client-ID**
(`kimne78kx3ncx6brgo4mv6wki5h1ko`, hard-coded in the file). Every command works
with **no token and no account**.

## Optional overrides

In `tokens.conf` (repo root):

```
TWITCH_CLIENT_ID = ...     # use a different GQL client id
TWITCH_OAUTH     = ...     # a browser "OAuth" token (the value after "OAuth "
                           # in the Authorization header of a logged-in session)
```

`TWITCH_OAUTH` is only worth setting if you start seeing `failed integrity
check` errors on an IP - a logged-in token clears most of them. Use a throwaway
account; scraping is against Twitch's ToS. Matching environment variables
override the file.

## What Twitch does **not** expose

Follower and following **lists** were made private API-side in 2022. `followers`
returns the count only; there is no per-account follower list to retrieve.
