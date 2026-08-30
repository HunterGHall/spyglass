# Getting a Discord bot token (only for `discord_user.py`)

Three of the four scripts — `discord_server.py`, `discord_member_count.py`,
`discord_widget.py` — need **no token**. They read the public invite and widget
endpoints.

Only `discord_user.py` (look up a user by ID) needs a bot token.

## Steps

1. Go to <https://discord.com/developers/applications> and click **New
   Application**.
2. Open the **Bot** tab → **Reset Token** → copy the token.
3. You do **not** need to invite the bot anywhere — `GET /users/{id}` works with
   any valid bot token.

## Use it

In `tokens.conf` (repo root):

```
DISCORD_TOKEN = your_bot_token
```

The `Bot ` prefix is added automatically. A raw user token also works but is a
worse idea — use a bot.

## Don't leak it

A bot token controls the bot account. `tokens.conf` is git-ignored. If it
leaks, reset it in the Bot tab.

## Notes

- **Member counts** come from `invites/{code}?with_counts=true` and are
  Discord's `approximate_member_count` — accurate to within a few percent.
- **`discord_widget.py`** only works if the server enabled *Server Settings →
  Widget*. Many large servers do; many don't.
- There is **no public way** to read a server's message history or a user's
  activity without being in the server with a real account.
