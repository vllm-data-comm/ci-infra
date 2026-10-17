"""Compute the PR's own commits and files, excluding upstream history.

A PR that syncs upstream `main` must not be judged commit by commit on
hundreds of upstream changes. "Own" means: reachable from head, not from
the base, not already on upstream main, and not a merge commit. Pure sync
PRs therefore have no own commits at all; a foreign commit sneaking into
a feature branch is own and gets checked.

Conflict resolutions inside a sync merge commit are not own changes; this
is an accepted blind spot (see docs/design/lwd_ci.md).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

_LWD_EMAIL_RE = re.compile(r"<([^<>]+)>")


@dataclass(frozen=True)
class LwdCommitInfo:
    sha: str
    subject: str
    author_email: str
    signoff_emails: tuple[str, ...]


@dataclass(frozen=True)
class LwdOwnFile:
    path: str
    status: str  # added | modified | deleted | renamed
    old_path: str | None = None


@dataclass(frozen=True)
class LwdScope:
    base_sha: str
    head_sha: str
    own_commits: tuple[LwdCommitInfo, ...]
    own_files: tuple[LwdOwnFile, ...]
    py_added_lines: int


def _lwd_git(repo_dir: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def _lwd_commit_info(repo_dir: Path, sha: str) -> LwdCommitInfo:
    raw = _lwd_git(
        repo_dir,
        "show",
        "-s",
        "--format=%H%x1f%s%x1f%ae%x1f%(trailers:key=Signed-off-by,valueonly)",
        sha,
    )
    sha_line, subject, author_email, *trailer_lines = raw.split("\x1f")
    signoff_emails = []
    for value in "".join(trailer_lines).splitlines():
        match = _LWD_EMAIL_RE.search(value)
        email = match.group(1) if match else value.strip()
        if email:
            signoff_emails.append(email.lower())
    return LwdCommitInfo(
        sha=sha_line.strip(),
        subject=subject.strip(),
        author_email=author_email.strip().lower(),
        signoff_emails=tuple(signoff_emails),
    )


def _lwd_own_files(
    repo_dir: Path, shas: list[str], head_sha: str
) -> tuple[LwdOwnFile, ...]:
    """Fold per-commit name-status into one final entry per surviving path."""
    events: dict[str, tuple[str, str | None]] = {}
    for sha in shas:
        for line in _lwd_git(
            repo_dir,
            "diff-tree",
            "--no-commit-id",
            "--name-status",
            "-r",
            "-M",
            "--root",
            sha,
        ).splitlines():
            fields = line.split("\t")
            if fields[0].startswith("R"):
                old_path, new_path = fields[1], fields[2]
                # A rename moves the content: the old path vanishes rather
                # than being deleted, so it is dropped from tracking.
                events.pop(old_path, None)
                events[new_path] = ("renamed", old_path)
            else:
                status = {"A": "added", "M": "modified", "D": "deleted"}[fields[0]]
                events[fields[1]] = (status, None)

    own_files = []
    for path, (status, old_path) in events.items():
        if status != "deleted" and not _lwd_path_exists(repo_dir, head_sha, path):
            continue
        own_files.append(LwdOwnFile(path=path, status=status, old_path=old_path))
    return tuple(sorted(own_files, key=lambda f: f.path))


def _lwd_path_exists(repo_dir: Path, sha: str, path: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(repo_dir), "cat-file", "-e", f"{sha}:{path}"],
        capture_output=True,
    )
    return completed.returncode == 0


def _lwd_py_added_lines(repo_dir: Path, shas: list[str]) -> int:
    added = 0
    for sha in shas:
        for line in _lwd_git(
            repo_dir, "show", "--numstat", "--format=", sha
        ).splitlines():
            fields = line.split("\t")
            if len(fields) != 3 or fields[0] == "-":
                continue
            path = fields[2]
            if path.endswith(".py") and not path.startswith("tests/"):
                added += int(fields[0])
    return added


def lwd_compute_scope(
    repo_dir: Path | str,
    base_sha: str,
    head_sha: str,
    upstream_ref: str,
) -> LwdScope:
    repo_dir = Path(repo_dir)
    shas = _lwd_git(
        repo_dir,
        "rev-list",
        "--no-merges",
        f"{base_sha}..{head_sha}",
        "--not",
        upstream_ref,
    ).split()
    # rev-list prints newest first; fold events oldest first.
    shas.reverse()
    return LwdScope(
        base_sha=base_sha,
        head_sha=head_sha,
        own_commits=tuple(_lwd_commit_info(repo_dir, sha) for sha in shas),
        own_files=_lwd_own_files(repo_dir, shas, head_sha),
        py_added_lines=_lwd_py_added_lines(repo_dir, shas),
    )


def lwd_scope_from_json(payload: dict) -> LwdScope:
    return LwdScope(
        base_sha=payload["base_sha"],
        head_sha=payload["head_sha"],
        own_commits=tuple(
            LwdCommitInfo(
                sha=c["sha"],
                subject=c["subject"],
                author_email=c["author_email"],
                signoff_emails=tuple(c["signoff_emails"]),
            )
            for c in payload["own_commits"]
        ),
        own_files=tuple(
            LwdOwnFile(
                path=f["path"],
                status=f["status"],
                old_path=f.get("old_path"),
            )
            for f in payload["own_files"]
        ),
        py_added_lines=payload["py_added_lines"],
    )


def lwd_scope_to_json(scope: LwdScope) -> dict:
    payload = asdict(scope)
    payload["own_commits"] = [
        {**asdict(c), "signoff_emails": list(c.signoff_emails)}
        for c in scope.own_commits
    ]
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(prog="lwd_checks.scope")
    parser.add_argument("--repo-dir", default=".")
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    scope = lwd_compute_scope(
        args.repo_dir, args.base_sha, args.head_sha, args.upstream
    )
    Path(args.out).write_text(json.dumps(lwd_scope_to_json(scope), indent=2) + "\n")


if __name__ == "__main__":
    main()
