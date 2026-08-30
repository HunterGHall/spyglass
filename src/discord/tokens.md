# Discord tokens

`discord.py` commands and what each needs:

| Command | Needs |
| --- | --- |
| `server`, `members`, `widget` | **nothing** — public invite / widget endpoints |
| `user` — basic fields | `DISCORD_TOKEN` (bot) **or** `DISCORD_USER_TOKEN` |
| `user` — bio, pronouns, connections, Nitro | `DISCORD_USER_TOKEN` only |

The `user` command calls two endpoints:

| Fields | Endpoint | Token |
| --- | --- | --- |
| username, display name, avatar / banner / decoration links, accent colour, badges, server tag, created date | `GET /users/{id}` | bot **or** user |
| About Me (bio), pronouns, connected accounts (Steam / Xbox / Epic / Riot / …), profile badges, Nitro status | `GET /users/{id}/profile` | **user only** — a bot token gets 401/403 here |

Put whichever you have in `tokens.conf` (repo root):

```
DISCORD_TOKEN = your_bot_token          # optional
DISCORD_USER_TOKEN = your_user_token    # optional; needed for bio/connections
```

The script uses the bot token for the basic call and the user token for the
profile. If only one is set, it's used for everything. The `Bot ` prefix is
added to `DISCORD_TOKEN` automatically.

---

## Bot token

1. <https://discord.com/developers/applications> → **New Application**.
2. **Bot** tab → **Reset Token** → copy it.
3. No need to invite the bot anywhere — `GET /users/{id}` works with any valid
   bot token.

## User token

> Automating a user account ("selfbotting") violates Discord's Terms of
> Service and can get the account disabled. Use a throwaway account.

1. Open <https://discord.com/app> logged in, press `F12`.
2. **Network** tab. Click a channel or open **User Settings** so requests
   appear.
3. Filter by `/api/`, click a request to `discord.com/api/v9/…` or `/api/v10/…`.
4. **Request Headers** → copy the **`authorization`** value (that one header,
   nothing else).

A valid user token is one of:

```
MTB2Nzk4… . GaBcDe . 8f7X6q9…       three dot-separated parts, ~70 chars
mfa.aBcD…                            starts with "mfa.", ~90 chars
```

The first part base64-decodes to your own user ID. If what you copied is much
longer, has many dots, or contains `-1788…`-style numbers, it's the wrong value
(a build-number or analytics cookie) — Discord answers **401** and
`discord.py user` prints `couldn't load profile: … (HTTP 401)`.

---

## Don't leak it

A bot token controls the bot; a user token **is** the account login.
`tokens.conf` is git-ignored — keep it that way. Reset a bot token in the Bot
tab; invalidate a user token by changing the account password.

## Limits

- **Member counts** come from `invites/{code}?with_counts=true` — Discord's
  `approximate_member_count`, good to within a few percent.
- the **`widget`** command needs the server to have enabled *Server Settings →
  Widget*.
- There is **no** REST endpoint for a user's game library, their live "now
  playing" status, or a server's message history — those need a gateway
  connection and, for the last two, a shared server.
