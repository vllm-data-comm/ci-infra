"""Commit check: subject format `type(lwd): ...` plus the DCO signoff.

Only the PR's own commits are checked (see lwd_checks.scope); merge
commits and upstream history are out of scope by construction.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from lwd_checks.results import LwdCheckResult, LwdFinding, lwd_write_results
from lwd_checks.scope import LwdCommitInfo, lwd_scope_from_json

_LWD_COMMIT_TYPES = ("feat", "fix", "perf", "refactor", "docs", "test", "ci", "chore")
_LWD_COMMIT_SUBJECT_RE = re.compile(rf"^({'|'.join(_LWD_COMMIT_TYPES)})\(lwd\): \S")
# CJK ideographs, compatibility ideographs, full-width forms, CJK punctuation.
_LWD_CJK_RE = re.compile("[⺀-鿿豈-﫿＀-￯　-〿]")


def lwd_check_commits(
    own_commits: list[LwdCommitInfo], base_sha: str
) -> LwdCheckResult:
    findings = []
    for commit in own_commits:
        problems = []
        if not _LWD_COMMIT_SUBJECT_RE.match(commit.subject):
            problems.append(
                f"subject must match `type(lwd): description` with type one of "
                f"{', '.join(_LWD_COMMIT_TYPES)}, got {commit.subject!r}"
            )
        elif _LWD_CJK_RE.search(commit.subject):
            problems.append("subject must not contain CJK characters or punctuation")
        if commit.author_email.lower() not in {
            e.lower() for e in commit.signoff_emails
        }:
            problems.append(
                f"missing a Signed-off-by matching the author email "
                f"({commit.author_email})"
            )
        if problems:
            findings.append(f"{commit.sha[:7]}: " + "; ".join(problems))

    status = "fail" if findings else "pass"
    summary = (
        ""
        if not findings
        else f"{len(findings)} commit(s) need fixes; to re-sign, run "
        f"`git rebase --signoff {base_sha}`"
    )
    return LwdCheckResult(
        check_id="commits",
        status=status,
        summary=summary,
        findings=tuple(LwdFinding(path="", line=None, message=m) for m in findings),
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="lwd_checks.commits_check")
    parser.add_argument("--scope", required=True, help="scope.json path")
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    payload = json.loads(Path(args.scope).read_text())
    scope = lwd_scope_from_json(payload)
    result = lwd_check_commits(list(scope.own_commits), scope.base_sha)
    lwd_write_results(
        args.out,
        lane="fast",
        pr_number=args.pr,
        head_sha=scope.head_sha,
        results=[result],
    )
    raise SystemExit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()
