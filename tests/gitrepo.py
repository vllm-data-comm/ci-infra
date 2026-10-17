"""Helpers to build throwaway git repositories for tests."""

from __future__ import annotations

import subprocess
from pathlib import Path


def lwd_git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def lwd_init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    lwd_git(repo, "init", "-q", "-b", "main")
    lwd_git(repo, "config", "user.name", "tester")
    lwd_git(repo, "config", "user.email", "tester@example.com")


def lwd_commit(
    repo: Path,
    subject: str,
    files: dict[str, str | None],
    *,
    sign: bool = False,
) -> str:
    """Create one commit; a None content deletes the path."""
    for rel, content in files.items():
        path = repo / rel
        if content is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    lwd_git(repo, "add", "-A")
    command = ["commit", "-q", "-m", subject]
    if sign:
        command.append("-s")
    lwd_git(repo, *command)
    return lwd_git(repo, "rev-parse", "HEAD").strip()


def lwd_merge(repo: Path, ref: str, subject: str = "merge upstream") -> str:
    lwd_git(repo, "merge", "-q", "--no-ff", "-m", subject, ref)
    return lwd_git(repo, "rev-parse", "HEAD").strip()


def lwd_clone_fork(upstream: Path, fork: Path) -> None:
    """Clone `upstream` into `fork` and register it as the `upstream` remote."""
    subprocess.run(
        ["git", "clone", "-q", str(upstream), str(fork)],
        check=True,
        capture_output=True,
        text=True,
    )
    lwd_git(fork, "config", "user.name", "tester")
    lwd_git(fork, "config", "user.email", "tester@example.com")
    lwd_git(fork, "remote", "add", "upstream", str(upstream))
    lwd_git(fork, "fetch", "-q", "upstream")
