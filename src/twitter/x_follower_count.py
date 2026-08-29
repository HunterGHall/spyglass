"""Get an X (Twitter) user's follower count without a developer API key.

Thin wrapper over x_profile / x_common. Tries the guest-token GraphQL path
first, then falls back to the public syndication embed endpoint.

    python x_follower_count.py nasa
"""

from __future__ import annotations

import sys

import x_common as xc


def _via_graphql(username: str, session) -> int:
    from x_profile import get_profile

    return get_profile(username, session=session).followers


def _via_syndication(username: str, session) -> int:
    url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{username}"
    resp = session.get(url, params={"showReplies": "false"}, timeout=15)
    resp.raise_for_status()
    data = xc.next_data(resp.text)
    count = xc.deep_find(data, "followers_count")
    if count is None:
        raise xc.XScrapeError("syndication: no follower count in payload")
    return int(count)


def get_follower_count(username: str) -> int:
    username = xc.clean_username(username)
    session = xc.new_session()

    errors = []
    for strategy in (_via_graphql, _via_syndication):
        try:
            return strategy(username, session)
        except Exception as exc:  # noqa: BLE001 - fall through to next strategy
            errors.append(f"{strategy.__name__}: {exc}")

    raise xc.XScrapeError("all strategies failed:\n  " + "\n  ".join(errors))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <username>", file=sys.stderr)
        return 2
    try:
        count = get_follower_count(argv[1])
    except (xc.XScrapeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"@{argv[1].lstrip('@')}: {count:,} followers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
