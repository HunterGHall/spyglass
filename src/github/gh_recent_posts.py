"""List a GitHub user's recent public activity (or their recent repos).

    python gh_recent_posts.py torvalds
    python gh_recent_posts.py torvalds --repos --limit 15 --json

Default: the public events feed (pushes, PRs, issues, stars, new repos) - the
same data as the activity feed on a profile page, last ~90 days / 300 events.
`--repos` lists the user's own repos, most recently pushed first.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys

import gh_common as gh


@dataclasses.dataclass
class Event:
    type: str
    repo: str
    created_at: str
    summary: str

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class Repo:
    name: str
    full_name: str
    url: str
    description: str | None
    language: str | None
    stars: int
    forks: int
    open_issues: int
    is_fork: bool
    archived: bool
    pushed_at: str | None
    created_at: str | None

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def _summarise(e: dict) -> str:
    t = e["type"]
    p = e.get("payload", {})
    if t == "PushEvent":
        ref = (p.get("ref") or "").split("/")[-1] or "a branch"
        commits = p.get("commits") or []
        n = p.get("size")
        if n is None and commits:
            n = len(commits)
        head = commits[-1].get("message", "").splitlines()[:1] if commits else []
        msg = f" — {head[0]}" if head else ""
        if n:
            return f"pushed {n} commit{'s' * (n != 1)} to {ref}{msg}"
        return f"pushed to {ref}{msg}"
    if t == "PullRequestEvent":
        pr = p.get("pull_request", {})
        return f"{p.get('action')} PR #{pr.get('number')}: {pr.get('title', '')}"
    if t == "IssuesEvent":
        iss = p.get("issue", {})
        return f"{p.get('action')} issue #{iss.get('number')}: {iss.get('title', '')}"
    if t == "IssueCommentEvent":
        iss = p.get("issue", {})
        return f"commented on #{iss.get('number')}: {iss.get('title', '')}"
    if t == "WatchEvent":
        return "starred the repo"
    if t == "ForkEvent":
        return "forked the repo"
    if t == "CreateEvent":
        return f"created {p.get('ref_type')} {p.get('ref') or ''}".rstrip()
    if t == "ReleaseEvent":
        return f"released {p.get('release', {}).get('tag_name', '')}"
    if t == "PublicEvent":
        return "made the repo public"
    return t


def get_recent_events(login: str, limit: int = 30) -> list[Event]:
    login = login.strip().lstrip("@").rstrip("/").split("/")[-1]
    out = []
    for e in gh.paginate(f"/users/{login}/events/public", limit=limit):
        out.append(
            Event(
                type=e["type"],
                repo=e.get("repo", {}).get("name", ""),
                created_at=e.get("created_at", ""),
                summary=_summarise(e),
            )
        )
    return out


def get_recent_repos(login: str, limit: int = 30) -> list[Repo]:
    login = login.strip().lstrip("@").rstrip("/").split("/")[-1]
    out = []
    for d in gh.paginate(
        f"/users/{login}/repos", limit=limit, sort="pushed", type="owner"
    ):
        out.append(
            Repo(
                name=d["name"],
                full_name=d["full_name"],
                url=d["html_url"],
                description=d.get("description"),
                language=d.get("language"),
                stars=d.get("stargazers_count", 0),
                forks=d.get("forks_count", 0),
                open_issues=d.get("open_issues_count", 0),
                is_fork=bool(d.get("fork")),
                archived=bool(d.get("archived")),
                pushed_at=d.get("pushed_at"),
                created_at=d.get("created_at"),
            )
        )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Recent GitHub activity for a user.")
    ap.add_argument("login")
    ap.add_argument("--repos", action="store_true", help="list repos, not events")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        rows = (
            get_recent_repos(args.login, args.limit)
            if args.repos
            else get_recent_events(args.login, args.limit)
        )
    except (gh.GitHubError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([r.as_dict() for r in rows], indent=2))
        return 0

    if args.repos:
        for r in rows:
            flags = "".join(
                f for f, v in ((" [fork]", r.is_fork), (" [archived]", r.archived)) if v
            )
            print(f"{r.full_name}  ★{r.stars:,}  {r.language or ''}{flags}")
            if r.description:
                print(f"    {r.description}")
            print(f"    pushed {(r.pushed_at or '?')[:10]} · {r.url}")
    else:
        for e in rows:
            print(f"[{e.created_at[:10]}] {e.repo}: {e.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
