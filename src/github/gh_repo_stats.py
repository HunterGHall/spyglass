"""In-depth public stats for a GitHub repository.

Stars, forks, watchers, open issues/PRs, commits, contributors, releases,
language breakdown, latest release + download totals, size, license, topics,
and the activity dates.

    python gh_repo_stats.py torvalds/linux
    python gh_repo_stats.py https://github.com/pallets/flask --json
    python gh_repo_stats.py cli/cli --fast     # skip the extra count queries

Each extra metric (commits, contributors, open PRs, release downloads) is its
own API call, so a full run is ~7 requests - fine with a GITHUB_TOKEN, close to
the 60/hr anonymous cap without one. `--fast` sticks to the single repo call.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import sys

import gh_common as gh


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


def _slug(value: str) -> tuple[str, str]:
    value = value.strip().rstrip("/")
    if "github.com" in value:
        value = value.split("github.com/", 1)[1]
    parts = value.split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise ValueError(f"expected owner/repo, got {value!r}")
    return parts[0], parts[1]


def get_repo_stats(ref: str, *, fast: bool = False) -> RepoStats:
    owner, repo = _slug(ref)
    d = gh.api(f"/repos/{owner}/{repo}")
    base = f"/repos/{owner}/{repo}"

    languages: dict[str, int] = {}
    open_prs = commits = contributors = releases = None
    latest_tag = latest_at = downloads = None

    if not fast:
        try:
            languages = gh.api(f"{base}/languages")
        except gh.GitHubError:
            pass
        open_prs = _safe_count(f"{base}/pulls", state="open")
        commits = _safe_count(f"{base}/commits", sha=d.get("default_branch", ""))
        contributors = _safe_count(f"{base}/contributors", anon="1")
        releases = _safe_count(f"{base}/releases")
        try:
            rel = gh.api(f"{base}/releases/latest")
            latest_tag = rel.get("tag_name")
            latest_at = rel.get("published_at")
            downloads = sum(
                a.get("download_count", 0) for a in rel.get("assets", [])
            )
        except gh.GitHubError:
            pass

    lic = d.get("license") or {}
    return RepoStats(
        full_name=d["full_name"],
        url=d["html_url"],
        description=d.get("description"),
        homepage=d.get("homepage") or None,
        default_branch=d.get("default_branch", "main"),
        license=lic.get("spdx_id") if lic.get("spdx_id") not in (None, "NOASSERTION")
        else lic.get("name"),
        language=d.get("language"),
        languages=languages,
        topics=d.get("topics", []),
        size_kb=d.get("size", 0),
        stars=d.get("stargazers_count", 0),
        forks=d.get("forks_count", 0),
        watchers=d.get("subscribers_count", 0),
        network=d.get("network_count", 0),
        open_issues=d.get("open_issues_count", 0),
        open_prs=open_prs,
        commits=commits,
        contributors=contributors,
        releases=releases,
        latest_release=latest_tag,
        latest_release_at=latest_at,
        release_downloads=downloads,
        is_fork=bool(d.get("fork")),
        archived=bool(d.get("archived")),
        is_template=bool(d.get("is_template")),
        has_wiki=bool(d.get("has_wiki")),
        has_discussions=bool(d.get("has_discussions")),
        created_at=d.get("created_at"),
        updated_at=d.get("updated_at"),
        pushed_at=d.get("pushed_at"),
    )


def _safe_count(path: str, **params) -> int | None:
    try:
        return gh.count(path, **params)
    except gh.GitHubError:
        return None


def _pct_langs(languages: dict[str, int], n: int = 4) -> str:
    total = sum(languages.values()) or 1
    top = sorted(languages.items(), key=lambda kv: -kv[1])[:n]
    return ", ".join(f"{k} {v * 100 / total:.0f}%" for k, v in top)


def _print_human(r: RepoStats) -> None:
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
        print(f"  releases:     {r.releases:,}")
    if r.latest_release:
        dl = f", {r.release_downloads:,} asset downloads" if r.release_downloads else ""
        print(f"  latest:       {r.latest_release} ({r.latest_release_at[:10]}{dl})")
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="In-depth stats for a GitHub repo.")
    ap.add_argument("repo", help="owner/repo or repo URL")
    ap.add_argument("--fast", action="store_true", help="single API call only")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        stats = get_repo_stats(args.repo, fast=args.fast)
    except (gh.GitHubError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(stats.as_dict(), indent=2))
    else:
        _print_human(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
