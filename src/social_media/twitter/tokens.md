# Getting your X tokens

The scripts can run logged out, but the timeline they see lags real time. To get
live data, give them two cookies from a browser that's logged into x.com.

Use a **throwaway account** — driving X with an unofficial client can get an
account locked.

## Steps

1. Log into <https://x.com>.
2. Open DevTools with `F12`.
3. Go to the cookie list:
   - **Chrome / Edge / Brave:** *Application* tab → *Storage* → *Cookies* → `https://x.com`
   - **Firefox:** *Storage* tab → *Cookies* → `https://x.com`
4. Copy two values from that list:

   | Cookie name  | Goes into      |
   | ------------ | -------------- |
   | `auth_token` | `X_AUTH_TOKEN` |
   | `ct0`        | `X_CT0`        |

## Use them

Open `tokens.conf` and paste the values after the `=`:

```
X_AUTH_TOKEN = your_auth_token_value
X_CT0 = your_ct0_value
```

The scripts read `tokens.conf` automatically. (A real `X_AUTH_TOKEN` /
`X_CT0` environment variable, if set, takes precedence.)

## Don't leak them

These two values are full access to the account. `tokens.conf` is already in
`.gitignore` — keep it there. They stop working once that browser session logs
out.
