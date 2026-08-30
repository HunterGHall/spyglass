"""GitHub: profiles, repo stats, commit history, releases, and the activity log
(including force-pushed / deleted-branch commits).

Wraps the real REST API (https://docs.github.com/rest). No token needed, but
anonymous requests are capped at 60/hour per IP; a `GITHUB_TOKEN` in tokens.conf
(no scopes needed) raises that to 5,000/hour.

    python github.py followers <login> [--following] [--limit N] [--json]
    python github.py profile   <login> [--json]
    python github.py repo      <owner/repo> [--fast] [--json]
    python github.py recent    <login> [--repos] [--limit N] [--json]
    python github.py commits   <owner/repo> [--branch B] [--path P] [--author A]
                                            [--since YYYY-MM-DD] [--limit N] [--json]
    python github.py releases  <owner/repo> [--limit N] [--json]
    python github.py activity  <owner/repo> [--limit N] [--lost] [--json]
    python github.py commit    <owner/repo> <sha> [--json]

`activity --lost` resolves the SHAs left behind by force-pushes and deleted
branches and shows those now-dangling commits - GitHub keeps them reachable by
SHA until garbage collection.

Importable: get_profile, get_follower_count, get_followers, get_following,
get_repo_stats, get_commits, get_releases, get_activity, get_commit,
get_recent_events, get_recent_repos.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import random
import re
import sys
import threading
import time

import requests

API = "https://api.github.com"


# ========================================================================== #
# tokens.conf loader  +  session
# ========================================================================== #

def _load_tokens_conf() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        path = os.path.join(here, "tokens.conf")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key, val = key.strip(), val.strip().strip("\"'")
                    if key and val and key not in os.environ:
                        os.environ[key] = val
            return
        parent = os.path.dirname(here)
        if parent == here:
            return
        here = parent


_load_tokens_conf()


class _Session(requests.Session):
    """Only paces requests if PACE_MIN/PACE_MAX are set (GitHub has real limits)."""

    _lock = threading.Lock()
    _next_at = 0.0

    def request(self, *args, **kwargs):
        if "PACE_MIN" in os.environ or "PACE_MAX" in os.environ:
            lo = float(os.environ.get("PACE_MIN", 3))
            hi = float(os.environ.get("PACE_MAX", 7))
            with _Session._lock:
                now = time.monotonic()
                if now < _Session._next_at:
                    time.sleep(_Session._next_at - now)
                _Session._next_at = time.monotonic() + random.uniform(lo, hi)
        return super().request(*args, **kwargs)


class GitHubError(RuntimeError):
    """Any failure talking to the GitHub API."""


def have_token() -> bool:
    return bool(os.environ.get("GITHUB_TOKEN"))


_session: _Session | None = None


def session() -> _Session:
    global _session
    if _session is None:
        s = _Session()
        s.headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "spyglass",
        })
        if os.environ.get("GITHUB_TOKEN"):
            s.headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
        _session = s
    return _session


def _raise_for(resp: requests.Response, path: str) -> None:
    if resp.status_code == 404:
        raise GitHubError(f"not found: {path}")
    if resp.status_code in (403, 429):
        if resp.headers.get("X-RateLimit-Remaining") == "0":
            reset = resp.headers.get("X-RateLimit-Reset", "")
            mins = max(0, (int(reset) - int(time.time())) // 60) if reset.isdigit() else 0
            hint = "" if have_token() else "; set GITHUB_TOKEN in tokens.conf for 5000/hr"
            raise GitHubError(f"GitHub rate limit exhausted (resets in ~{mins} min){hint}")
        raise GitHubError(f"GitHub refused the request (HTTP {resp.status_code})")
    if resp.status_code == 401:
        raise GitHubError("GITHUB_TOKEN is invalid (HTTP 401)")
    resp.raise_for_status()


def api(path: str, **params):
    url = path if path.startswith("http") else API + path
    resp = session().get(url, params=params or None, timeout=20)
    _raise_for(resp, path)
    return resp.json()


def paginate(path: str, *, limit: int, per_page: int = 100, **params):
    url = path if path.startswith("http") else API + path
    got = 0
    params = {**params, "per_page": min(per_page, limit or per_page)}
    while url and (not limit or got < limit):
        resp = session().get(url, params=params, timeout=20)
        _raise_for(resp, path)
        page = resp.json()
        if isinstance(page, dict) and "items" in page:
            page = page["items"]
        for item in page:
            yield item
            got += 1
            if limit and got >= limit:
                return
        url = resp.links.get("next", {}).get("url")
        params = None


def count(path: str, **params) -> int:
    url = path if path.startswith("http") else API + path
    resp = session().get(url, params={**params, "per_page": 1}, timeout=20)
    _raise_for(resp, path)
    last = resp.links.get("last", {}).get("url")
    if last:
        m = re.search(r"[?&]page=(\d+)", last)
        if m:
            return int(m.group(1))
    return len(resp.json())


def _safe_count(path: str, **params) -> int | None:
    try:
        return count(path, **params)
    except GitHubError:
        return None


def _slug(value: str) -> str:
    value = value.strip().rstrip("/")
    if "github.com" in value:
        value = value.split("github.com/", 1)[1]
    parts = value.split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise ValueError(f"expected owner/repo, got {value!r}")
    return f"{parts[0]}/{parts[1]}"


def _login(value: str) -> str:
    return value.strip().lstrip("@").rstrip("/").split("/")[-1]


# ========================================================================== #
# profile
# ========================================================================== #

@dataclasses.dataclass
class Account:
    kind: str
    login: str
    id: int
    name: str | None
    company: str | None
    location: str | None
    bio: str | None
    blog: str | None
    email: str | None
    twitter: str | None
    hireable: bool | None
    followers: int
    following: int
    public_repos: int
    public_gists: int
    created_at: str | None
    updated_at: str | None
    avatar_url: str | None
    html_url: str

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def get_profile(login: str) -> Account:
    d = api(f"/users/{_login(login)}")
    return Account(
        kind=d.get("type", "User"), login=d["login"], id=d["id"],
        name=d.get("name"), company=d.get("company"), location=d.get("location"),
        bio=d.get("bio"), blog=d.get("blog") or None, email=d.get("email"),
        twitter=d.get("twitter_username"), hireable=d.get("hireable"),
        followers=d.get("followers", 0), following=d.get("following", 0),
        public_repos=d.get("public_repos", 0), public_gists=d.get("public_gists", 0),
        created_at=d.get("created_at"), updated_at=d.get("updated_at"),
        avatar_url=d.get("avatar_url"),
        html_url=d.get("html_url", f"https://github.com/{d['login']}"),
    )


def get_follower_count(login: str) -> int:
    return get_profile(login).followers


@dataclasses.dataclass
class Follower:
    login: str
    id: int
    kind: str
    url: str

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def get_followers(login: str, limit: int = 100, *, following: bool = False) -> list[Follower]:
    """List the accounts following `login` (or, with following=True, that it follows)."""
    who = _login(login)
    path = f"/users/{who}/{'following' if following else 'followers'}"
    return [
        Follower(login=d["login"], id=d["id"], kind=d.get("type", "User"),
                 url=d.get("html_url", f"https://github.com/{d['login']}"))
        for d in paginate(path, limit=limit)
    ]


def get_following(login: str, limit: int = 100) -> list[Follower]:
    """List the accounts `login` follows."""
    return get_followers(login, limit, following=True)


# ========================================================================== #
# repo stats
# ========================================================================== #

@dataclasses.dataclass
class RepoStats:
    full_name: str
    url: str
    description: str | None
    homepage: str | None
    default_branch: str
    license: str | None
    language: str | None
    languages: dict[str, int]
    topics: list[str]
    size_kb: int
    stars: int
    forks: int
    watchers: int
    network: int
    open_issues: int
    open_prs: int | None
    commits: int | None
    contributors: int | None
    releases: int | None
    tags: int | None
    latest_release: str | None
    latest_release_at: str | None
    release_downloads: int | None
    is_fork: bool
    archived: bool
    is_template: bool
    has_wiki: bool
    has_discussions: bool
    created_at: str | None
    updated_at: str | None
    pushed_at: str | None

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def get_repo_stats(ref: str, *, fast: bool = False) -> RepoStats:
    slug = _slug(ref)
    d = api(f"/repos/{slug}")
    base = f"/repos/{slug}"
    languages: dict[str, int] = {}
    open_prs = commits = contributors = releases = tags = None
    latest_tag = latest_at = downloads = None

    if not fast:
        try:
            languages = api(f"{base}/languages")
        except GitHubError:
            pass
        open_prs = _safe_count(f"{base}/pulls", state="open")
        commits = _safe_count(f"{base}/commits", sha=d.get("default_branch", ""))
        contributors = _safe_count(f"{base}/contributors", anon="1")
        releases = _safe_count(f"{base}/releases")
        tags = _safe_count(f"{base}/tags")
        try:
            rel = api(f"{base}/releases/latest")
            latest_tag = rel.get("tag_name")
            latest_at = rel.get("published_at")
            downloads = sum(a.get("download_count", 0) for a in rel.get("assets", []))
        except GitHubError:
            pass

    lic = d.get("license") or {}
    return RepoStats(
        full_name=d["full_name"], url=d["html_url"], description=d.get("description"),
        homepage=d.get("homepage") or None,
        default_branch=d.get("default_branch", "main"),
        license=lic.get("spdx_id") if lic.get("spdx_id") not in (None, "NOASSERTION")
        else lic.get("name"),
        language=d.get("language"), languages=languages, topics=d.get("topics", []),
        size_kb=d.get("size", 0), stars=d.get("stargazers_count", 0),
        forks=d.get("forks_count", 0), watchers=d.get("subscribers_count", 0),
        network=d.get("network_count", 0), open_issues=d.get("open_issues_count", 0),
        open_prs=open_prs, commits=commits, contributors=contributors,
        releases=releases, tags=tags, latest_release=latest_tag,
        latest_release_at=latest_at, release_downloads=downloads,
        is_fork=bool(d.get("fork")), archived=bool(d.get("archived")),
        is_template=bool(d.get("is_template")), has_wiki=bool(d.get("has_wiki")),
        has_discussions=bool(d.get("has_discussions")),
        created_at=d.get("created_at"), updated_at=d.get("updated_at"),
        pushed_at=d.get("pushed_at"),
    )


# ========================================================================== #
# commit history
# ========================================================================== #

@dataclasses.dataclass
class Commit:
    sha: str
    url: str
    date: str | None
    author: str
    login: str | None
    message: str
    parents: list[str]
    additions: int | None = None
    deletions: int | None = None
    files_changed: int | None = None
    reachable: bool | None = None

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def _commit_from(d: dict) -> Commit:
    c = d.get("commit", {})
    author = c.get("author") or {}
    stats = d.get("stats") or {}
    return Commit(
        sha=d.get("sha", ""),
        url=d.get("html_url", ""),
        date=author.get("date"),
        author=author.get("name", "?"),
        login=(d.get("author") or {}).get("login"),
        message=c.get("message", "").splitlines()[0] if c.get("message") else "",
        parents=[p.get("sha", "") for p in d.get("parents", [])],
        additions=stats.get("additions"),
        deletions=stats.get("deletions"),
        files_changed=len(d.get("files", [])) or None,
    )


def get_commits(ref: str, *, branch: str | None = None, path: str | None = None,
                author: str | None = None, since: str | None = None,
                until: str | None = None, limit: int = 30) -> list[Commit]:
    slug = _slug(ref)
    params: dict = {}
    if branch:
        params["sha"] = branch
    if path:
        params["path"] = path
    if author:
        params["author"] = author
    if since:
        params["since"] = _iso(since)
    if until:
        params["until"] = _iso(until)
    return [_commit_from(d) for d in paginate(f"/repos/{slug}/commits", limit=limit, **params)]


def get_commit(ref: str, sha: str) -> Commit:
    """Fetch one commit by SHA - works even for dangling / orphaned commits."""
    slug = _slug(ref)
    d = api(f"/repos/{slug}/commits/{sha}")
    commit = _commit_from(d)
    try:
        heads = api(f"/repos/{slug}/commits/{d['sha']}/branches-where-head")
        commit.reachable = bool(heads) or None
    except GitHubError:
        pass
    if commit.reachable is None:
        # a commit not at any branch head may still be reachable; check pulls
        try:
            prs = api(f"/repos/{slug}/commits/{d['sha']}/pulls")
            commit.reachable = True if prs else commit.reachable
        except GitHubError:
            pass
    return commit


def _iso(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value + "T00:00:00Z"
    return value


# ========================================================================== #
# releases / tags  (past versions)
# ========================================================================== #

@dataclasses.dataclass
class Version:
    tag: str
    name: str | None
    kind: str            # release | prerelease | draft | tag
    published_at: str | None
    commit: str | None
    downloads: int | None
    url: str

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def get_releases(ref: str, limit: int = 40) -> list[Version]:
    slug = _slug(ref)
    out: list[Version] = []
    seen: set[str] = set()

    for r in paginate(f"/repos/{slug}/releases", limit=limit):
        tag = r.get("tag_name", "")
        seen.add(tag)
        kind = "draft" if r.get("draft") else ("prerelease" if r.get("prerelease")
                                               else "release")
        out.append(Version(
            tag=tag, name=r.get("name") or None, kind=kind,
            published_at=r.get("published_at") or r.get("created_at"),
            commit=r.get("target_commitish"),
            downloads=sum(a.get("download_count", 0) for a in r.get("assets", [])) or None,
            url=r.get("html_url", ""),
        ))

    # plain git tags that never became releases
    for t in paginate(f"/repos/{slug}/tags", limit=max(0, limit - len(out))):
        if t.get("name") in seen:
            continue
        out.append(Version(
            tag=t.get("name", ""), name=None, kind="tag", published_at=None,
            commit=(t.get("commit") or {}).get("sha"), downloads=None,
            url=f"https://github.com/{slug}/releases/tag/{t.get('name', '')}",
        ))
    return out[:limit]


# ========================================================================== #
# activity log  (force pushes, deleted branches, dangling commits)
# ========================================================================== #

@dataclasses.dataclass
class Activity:
    activity_type: str
    ref: str
    actor: str | None
    timestamp: str | None
    before: str
    after: str
    lost_commits: list[Commit]

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


_ZERO = "0" * 40


def get_activity(ref: str, limit: int = 30, *, resolve_lost: bool = False) -> list[Activity]:
    slug = _slug(ref)
    default_branch = api(f"/repos/{slug}").get("default_branch", "")
    out: list[Activity] = []

    for a in paginate(f"/repos/{slug}/activity", limit=limit):
        at = a.get("activity_type", "")
        before = a.get("before", "") or ""
        after = a.get("after", "") or ""
        act = Activity(
            activity_type=at,
            ref=(a.get("ref") or "").replace("refs/heads/", ""),
            actor=(a.get("actor") or {}).get("login"),
            timestamp=a.get("timestamp"),
            before=before, after=after, lost_commits=[],
        )
        if resolve_lost and at in ("force_push", "branch_deletion") and before not in ("", _ZERO):
            act.lost_commits = _lost_between(slug, before, after, default_branch)
        out.append(act)
    return out


def _lost_between(slug: str, before: str, after: str, default_branch: str) -> list[Commit]:
    """Commits that were on `before` but aren't reachable from `after` / default."""
    base = after if after not in ("", _ZERO) else default_branch
    try:
        cmp = api(f"/repos/{slug}/compare/{base}...{before}")
    except GitHubError:
        # `before` is fully gone from the graph except by direct SHA
        try:
            return [get_commit(slug, before)]
        except GitHubError:
            return []
    lost = []
    for c in cmp.get("commits", []):
        if base != default_branch:
            # double-check it's not on the default branch either
            try:
                api(f"/repos/{slug}/compare/{default_branch}...{c['sha']}")
            except GitHubError:
                pass
        lost.append(_commit_from(c))
    return lost


# ========================================================================== #
# recent activity feed / repos
# ========================================================================== #

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
        rf = (p.get("ref") or "").split("/")[-1] or "a branch"
        commits = p.get("commits") or []
        n = p.get("size") or (len(commits) or None)
        head = commits[-1].get("message", "").splitlines()[:1] if commits else []
        msg = f" — {head[0]}" if head else ""
        return f"pushed {n} commit{'s' * (n != 1)} to {rf}{msg}" if n else f"pushed to {rf}{msg}"
    if t == "PullRequestEvent":
        pr = p.get("pull_request", {})
        return f"{p.get('action')} PR #{pr.get('number')}: {pr.get('title', '')}"
    if t == "IssuesEvent":
        iss = p.get("issue", {})
        return f"{p.get('action')} issue #{iss.get('number')}: {iss.get('title', '')}"
    if t == "IssueCommentEvent":
        return f"commented on #{p.get('issue', {}).get('number')}"
    if t == "WatchEvent":
        return "starred the repo"
    if t == "ForkEvent":
        return "forked the repo"
    if t == "CreateEvent":
        return f"created {p.get('ref_type')} {p.get('ref') or ''}".rstrip()
    if t == "DeleteEvent":
        return f"deleted {p.get('ref_type')} {p.get('ref') or ''}".rstrip()
    if t == "ReleaseEvent":
        return f"released {p.get('release', {}).get('tag_name', '')}"
    if t == "PublicEvent":
        return "made the repo public"
    return t


def get_recent_events(login: str, limit: int = 30) -> list[Event]:
    return [
        Event(type=e["type"], repo=e.get("repo", {}).get("name", ""),
              created_at=e.get("created_at", ""), summary=_summarise(e))
        for e in paginate(f"/users/{_login(login)}/events/public", limit=limit)
    ]


def get_recent_repos(login: str, limit: int = 30) -> list[Repo]:
    out = []
    for d in paginate(f"/users/{_login(login)}/repos", limit=limit,
                      sort="pushed", type="owner"):
        out.append(Repo(
            name=d["name"], full_name=d["full_name"], url=d["html_url"],
            description=d.get("description"), language=d.get("language"),
            stars=d.get("stargazers_count", 0), forks=d.get("forks_count", 0),
            open_issues=d.get("open_issues_count", 0), is_fork=bool(d.get("fork")),
            archived=bool(d.get("archived")), pushed_at=d.get("pushed_at"),
            created_at=d.get("created_at"),
        ))
    return out


# ========================================================================== #
# CLI printers
# ========================================================================== #

def _print_profile(a: Account) -> None:
    print(f"{a.login}" + (f" — {a.name}" if a.name else "") + f"  [{a.kind}]")
    print(f"  {a.html_url}")
    if a.bio:
        print(f"\n{a.bio}\n")
    for label, val in (("company", a.company), ("location", a.location),
                       ("blog", a.blog), ("email", a.email),
                       ("twitter", f"@{a.twitter}" if a.twitter else None)):
        if val:
            print(f"  {label}:       {val}")
    print(f"  followers:   {a.followers:,}")
    print(f"  following:   {a.following:,}")
    print(f"  public repos:{a.public_repos:>6,}")
    print(f"  public gists:{a.public_gists:>6,}")
    if a.created_at:
        print(f"  joined:      {a.created_at[:10]}")
    if a.hireable:
        print("  hireable:    yes")


def _pct_langs(languages: dict[str, int], n: int = 4) -> str:
    total = sum(languages.values()) or 1
    return ", ".join(f"{k} {v * 100 / total:.0f}%"
                     for k, v in sorted(languages.items(), key=lambda kv: -kv[1])[:n])


def _print_repo(r: RepoStats) -> None:
    print(f"{r.full_name}" + ("  [archived]" if r.archived else ""))
    print(f"  {r.url}")
    if r.description:
        print(f"\n{r.description}\n")
    print(f"  stars:        {r.stars:,}")
    print(f"  forks:        {r.forks:,}")
    print(f"  watchers:     {r.watchers:,}")
    print(f"  open issues:  {r.open_issues:,}")
    if r.open_prs is not None:
        print(f"  open PRs:     {r.open_prs:,}")
    if r.commits is not None:
        print(f"  commits:      {r.commits:,}  (on {r.default_branch})")
    if r.contributors is not None:
        print(f"  contributors: {r.contributors:,}")
    if r.releases is not None:
        print(f"  releases:     {r.releases:,}" + (f" ({r.tags} tags)" if r.tags else ""))
    if r.latest_release:
        dl = f", {r.release_downloads:,} downloads" if r.release_downloads else ""
        print(f"  latest:       {r.latest_release} ({(r.latest_release_at or '')[:10]}{dl})")
    if r.languages:
        print(f"  languages:    {_pct_langs(r.languages)}")
    elif r.language:
        print(f"  language:     {r.language}")
    if r.license:
        print(f"  license:      {r.license}")
    if r.topics:
        print(f"  topics:       {', '.join(r.topics)}")
    print(f"  size:         {r.size_kb / 1024:,.1f} MB")
    if r.pushed_at:
        print(f"  last push:    {r.pushed_at[:10]}")
    if r.created_at:
        print(f"  created:      {r.created_at[:10]}")


def _print_commit(c: Commit, full: bool = False) -> None:
    who = c.login or c.author
    print(f"{c.sha[:10]}  {(c.date or '')[:10]}  {who}")
    print(f"    {c.message}")
    if full:
        if c.additions is not None:
            print(f"    +{c.additions} -{c.deletions}  ({c.files_changed} files)")
        print(f"    parents: {', '.join(p[:10] for p in c.parents) or 'none (root)'}")
        if c.reachable is False:
            print("    ⚠ DANGLING - not reachable from any branch head")
        print(f"    {c.url}")


# ========================================================================== #
# CLI commands
# ========================================================================== #

def _cmd_followers(args) -> int:
    if not args.following and not args.limit:
        print(f"{_login(args.login)}: {get_follower_count(args.login):,} followers")
        return 0
    a = get_profile(args.login)
    total = a.following if args.following else a.followers
    people = get_followers(args.login, args.limit or 100, following=args.following)
    if args.json:
        print(json.dumps([p.as_dict() for p in people], indent=2))
        return 0
    label = "following" if args.following else "followers"
    print(f"{a.login} — {len(people):,} of {total:,} {label} shown\n")
    for p in people:
        tag = "" if p.kind == "User" else f"  [{p.kind}]"
        print(f"  {p.login}{tag}")
    return 0


def _cmd_profile(args) -> int:
    a = get_profile(args.login)
    print(json.dumps(a.as_dict(), indent=2)) if args.json else _print_profile(a)
    return 0


def _cmd_repo(args) -> int:
    r = get_repo_stats(args.repo, fast=args.fast)
    print(json.dumps(r.as_dict(), indent=2)) if args.json else _print_repo(r)
    return 0


def _cmd_recent(args) -> int:
    if args.repos:
        rows = get_recent_repos(args.login, args.limit)
        if args.json:
            print(json.dumps([r.as_dict() for r in rows], indent=2))
            return 0
        for r in rows:
            flags = "".join(f for f, v in ((" [fork]", r.is_fork),
                                           (" [archived]", r.archived)) if v)
            print(f"{r.full_name}  ★{r.stars:,}  {r.language or ''}{flags}")
            if r.description:
                print(f"    {r.description}")
            print(f"    pushed {(r.pushed_at or '?')[:10]} · {r.url}")
        return 0
    rows = get_recent_events(args.login, args.limit)
    if args.json:
        print(json.dumps([e.as_dict() for e in rows], indent=2))
        return 0
    for e in rows:
        print(f"[{e.created_at[:10]}] {e.repo}: {e.summary}")
    return 0


def _cmd_commits(args) -> int:
    rows = get_commits(args.repo, branch=args.branch, path=args.path,
                       author=args.author, since=args.since, until=args.until,
                       limit=args.limit)
    if args.json:
        print(json.dumps([c.as_dict() for c in rows], indent=2))
        return 0
    for c in rows:
        _print_commit(c)
    return 0


def _cmd_releases(args) -> int:
    rows = get_releases(args.repo, args.limit)
    if args.json:
        print(json.dumps([v.as_dict() for v in rows], indent=2))
        return 0
    for v in rows:
        badge = "" if v.kind == "release" else f"  [{v.kind}]"
        when = f"  {v.published_at[:10]}" if v.published_at else ""
        dl = f"  ·  {v.downloads:,} downloads" if v.downloads else ""
        print(f"{v.tag}{badge}{when}{dl}")
        if v.name and v.name != v.tag:
            print(f"    {v.name}")
    return 0


def _cmd_activity(args) -> int:
    rows = get_activity(args.repo, args.limit, resolve_lost=args.lost)
    if args.json:
        print(json.dumps([a.as_dict() for a in rows], indent=2))
        return 0
    for a in rows:
        b = a.before[:8] if a.before not in ("", _ZERO) else "∅"
        af = a.after[:8] if a.after not in ("", _ZERO) else "∅"
        print(f"[{(a.timestamp or '')[:19]}] {a.activity_type:16} {a.ref}  "
              f"{b} → {af}  ({a.actor or '?'})")
        for c in a.lost_commits:
            print(f"      lost  {c.sha[:10]}  {c.author}: {c.message}")
    return 0


def _cmd_commit(args) -> int:
    c = get_commit(args.repo, args.sha)
    if args.json:
        print(json.dumps(c.as_dict(), indent=2))
        return 0
    _print_commit(c, full=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="github", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("followers", help="follower count, or the list with --limit")
    p.add_argument("login")
    p.add_argument("--following", action="store_true", help="list who they follow")
    p.add_argument("--limit", type=int, default=0,
                   help="list up to N accounts; 0 = just the count")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_followers)

    p = sub.add_parser("profile", help="user / org profile")
    p.add_argument("login")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_profile)

    p = sub.add_parser("repo", help="in-depth repo stats")
    p.add_argument("repo")
    p.add_argument("--fast", action="store_true", help="single API call")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_repo)

    p = sub.add_parser("recent", help="recent activity / repos")
    p.add_argument("login")
    p.add_argument("--repos", action="store_true")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_recent)

    p = sub.add_parser("commits", help="commit history")
    p.add_argument("repo")
    p.add_argument("--branch")
    p.add_argument("--path")
    p.add_argument("--author")
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_commits)

    p = sub.add_parser("releases", help="releases + tags (past versions)")
    p.add_argument("repo")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_releases)

    p = sub.add_parser("activity", help="ref activity: pushes, force-pushes, deletions")
    p.add_argument("repo")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--lost", action="store_true",
                   help="resolve force-pushed / deleted-branch commits")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_activity)

    p = sub.add_parser("commit", help="one commit by SHA (incl. dangling)")
    p.add_argument("repo")
    p.add_argument("sha")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_commit)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (GitHubError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
