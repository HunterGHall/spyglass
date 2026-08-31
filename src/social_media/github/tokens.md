# GitHub token (optional but recommended)

`github.py` just calls the real REST API - no scraping, no account cookie.
Everything works with **no token**, but anonymous requests are limited to
**60 per hour per IP**, and the `repo` command alone spends ~7 of those.

A **personal access token** raises the limit to **5,000/hour**.

## Steps

1. Go to <https://github.com/settings/tokens>.
2. Either kind works:
   - **Fine-grained token** → "Generate new token", no extra permissions needed
     for public data (leave everything at "no access").
   - **Classic token** → "Generate new token (classic)", tick **no scopes** (a
     scopeless classic token still gets the 5,000/hr public limit).
3. Copy the token (starts with `github_pat_` or `ghp_`).

## Use it

In `tokens.conf` (repo root):

```
GITHUB_TOKEN = github_pat_xxxxxxxx
```

A `GITHUB_TOKEN` environment variable (as set in GitHub Actions) overrides it.

## Don't leak it

Even a scopeless token is tied to your account and counts against your rate
limit. `tokens.conf` is git-ignored. Revoke leaked tokens at the link above.
