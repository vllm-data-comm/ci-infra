"""PR metadata advisory rules: the RFC link and bugfix test coverage.

rfc-link: a PR adding more than 500 lines of Python outside tests/ must
reference an issue whose title starts with `[RFC]`. Same-repo `#N` and
`vllm-data-comm` issue URLs are both followed.

bugfix-tests: a `[Bugfix]` PR that touches nothing under tests/ is
listed for the committer to judge.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from functools import partial
from pathlib import Path

from lwd_checks.results import LwdCheckResult, LwdFinding, lwd_write_results
from lwd_checks.scope import LwdScope, lwd_scope_from_json
from lwd_checks.title_check import lwd_parse_title_tags

_LWD_RFC_LINE_THRESHOLD = 500
_LWD_ISSUE_NUMBER_RE = re.compile(r"#(\d+)")
_LWD_ISSUE_URL_RE = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)/issues/(\d+)")
_LWD_CURRENT_REPO = "(current)"

LwdIssueTitleFetch = Callable[[str, int], str | None]


def lwd_fetch_issue_title(
    repo: str, number: int, token: str | None = None
) -> str | None:
    """Fetch an issue title; returns None when unreachable or not found."""
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/issues/{number}",
        headers={"Accept": "application/vnd.github+json"},
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return None
    if "title" not in payload:  # PRs also resolve here; treat as found
        return None
    return payload["title"]


def lwd_referenced_issues(body: str) -> list[tuple[str, int]]:
    """Return (repo, number) pairs; same-repo `#N` maps to the marker."""
    references: list[tuple[str, int]] = []
    occupied = {m.span() for m in _LWD_ISSUE_URL_RE.finditer(body)}
    for match in _LWD_ISSUE_NUMBER_RE.finditer(body):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        references.append((_LWD_CURRENT_REPO, int(match.group(1))))
    for match in _LWD_ISSUE_URL_RE.finditer(body):
        references.append((f"{match.group(1)}/{match.group(2)}", int(match.group(3))))
    return references


def _lwd_is_rfc_title(title: str | None) -> bool:
    return title is not None and title.strip().upper().startswith("[RFC]")


def lwd_check_rfc_link(
    scope: LwdScope, body: str, repo: str, fetch: LwdIssueTitleFetch
) -> LwdCheckResult:
    if scope.py_added_lines <= _LWD_RFC_LINE_THRESHOLD:
        return LwdCheckResult(check_id="rfc-link", status="pass")

    for ref_repo, number in lwd_referenced_issues(body):
        title = fetch(ref_repo if ref_repo != _LWD_CURRENT_REPO else repo, number)
        if _lwd_is_rfc_title(title):
            return LwdCheckResult(check_id="rfc-link", status="pass")

    docs_note = (
        "the PR touches docs/"
        if any(f.path.startswith("docs/") for f in scope.own_files)
        else "the PR does not touch docs/"
    )
    summary = (
        f"{scope.py_added_lines} added Python lines outside tests/ exceed "
        f"{_LWD_RFC_LINE_THRESHOLD}; reference an issue titled [RFC]... in the "
        f"PR description; {docs_note}"
    )
    return LwdCheckResult(
        check_id="rfc-link",
        status="fail",
        summary=summary,
        findings=(LwdFinding(path="", line=None, message="no [RFC] issue referenced"),),
    )


def lwd_check_bugfix_tests(scope: LwdScope, title: str) -> LwdCheckResult:
    tags = lwd_parse_title_tags(title)
    if not tags or tags[0] != "Bugfix":
        return LwdCheckResult(check_id="bugfix-tests", status="pass")
    if any(f.path.startswith("tests/") for f in scope.own_files):
        return LwdCheckResult(check_id="bugfix-tests", status="pass")
    return LwdCheckResult(
        check_id="bugfix-tests",
        status="fail",
        summary="[Bugfix] PR changes no tests; a regression test is expected",
        findings=(LwdFinding(path="", line=None, message="no tests/ file in this PR"),),
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="lwd_checks.pr_meta")
    parser.add_argument("--scope", required=True)
    parser.add_argument("--title-file", required=True)
    parser.add_argument("--body-file", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    payload = json.loads(Path(args.scope).read_text())
    scope = lwd_scope_from_json(payload)
    title = Path(args.title_file).read_text()
    body = Path(args.body_file).read_text()
    token = os.environ.get(args.token_env) or None

    results = [
        lwd_check_rfc_link(
            scope, body, args.repo, partial(lwd_fetch_issue_title, token=token)
        ),
        lwd_check_bugfix_tests(scope, title),
    ]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for result in results:
        lwd_write_results(
            out_dir / f"{result.check_id}.json",
            lane="fast",
            pr_number=args.pr,
            head_sha=scope.head_sha,
            results=[result],
        )


if __name__ == "__main__":
    main()
