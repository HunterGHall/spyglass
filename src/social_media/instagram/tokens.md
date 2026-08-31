# Getting your Instagram token

The Instagram scripts use [instagrapi](https://github.com/subzeroid/instagrapi),
which talks to Instagram's private mobile API. It needs one cookie from a
browser that's logged into instagram.com: **`sessionid`**.

Use a **throwaway account** — driving Instagram with an unofficial client is
against its terms and can get an account checkpointed or banned.

## Steps

1. Log into <https://www.instagram.com>.
2. Open DevTools with `F12`.
3. Go to the cookie list:
   - **Chrome / Edge / Brave:** *Application* tab → *Storage* → *Cookies* → `https://www.instagram.com`
   - **Firefox:** *Storage* tab → *Cookies* → `https://www.instagram.com`
4. Copy the value of the **`sessionid`** row. It looks like
   `71234567890%3AAbCdEf...%3A12%3AAY...`.

## Use it

Open `tokens.conf` (repo root) and paste the value after the `=`:

```
IG_SESSIONID = 71234567890%3AAbCdEf...
```

Paste it exactly as shown in DevTools — keep the `%3A` sequences, don't
URL-decode them. The scripts read `tokens.conf` automatically; a real
`IG_SESSIONID` environment variable overrides it.

## Don't leak it

`sessionid` is full access to the account. `tokens.conf` is git-ignored — keep
it that way. It stops working when you log out of that browser session or
Instagram expires it (usually weeks).
