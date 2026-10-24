from __future__ import annotations

from pathlib import Path

from gitrepo import lwd_clone_fork, lwd_commit, lwd_git, lwd_init, lwd_merge

from lwd_checks.scope import lwd_compute_scope


def _repo_pair(tmp_path: Path) -> tuple[Path, Path]:
    upstream = tmp_path / "upstream"
    fork = tmp_path / "fork"
    lwd_init(upstream)
    lwd_commit(upstream, "chore: base", {"a.txt": "a"})
    lwd_clone_fork(upstream, fork)
    return upstream, fork


def test_own_commit_is_listed_with_metadata(tmp_path: Path):
    _upstream, fork = _repo_pair(tmp_path)
    base = lwd_git(fork, "rev-parse", "HEAD").strip()
    head = lwd_commit(fork, "feat(lwd): add b", {"b.txt": "b"}, sign=True)

    scope = lwd_compute_scope(fork, base, head, "upstream/main")

    assert len(scope.own_commits) == 1
    commit = scope.own_commits[0]
    assert commit.sha == head
    assert commit.subject == "feat(lwd): add b"
    assert commit.author_email == "tester@example.com"
    assert commit.signoff_emails == ("tester@example.com",)


def test_upstream_commits_and_merges_are_excluded(tmp_path: Path):
    upstream, fork = _repo_pair(tmp_path)
    base = lwd_git(fork, "rev-parse", "HEAD").strip()
    lwd_commit(upstream, "fix: upstream move", {"a.txt": "a2"})
    lwd_git(fork, "fetch", "-q", "upstream")
    lwd_merge(fork, "upstream/main")
    head = lwd_commit(fork, "fix(lwd): own change", {"a.txt": "a3"}, sign=True)

    scope = lwd_compute_scope(fork, base, head, "upstream/main")

    assert [c.subject for c in scope.own_commits] == ["fix(lwd): own change"]


def test_pure_sync_pr_has_no_own_changes(tmp_path: Path):
    upstream, fork = _repo_pair(tmp_path)
    base = lwd_git(fork, "rev-parse", "HEAD").strip()
    lwd_commit(upstream, "feat: upstream only", {"up.txt": "up"})
    lwd_git(fork, "fetch", "-q", "upstream")
    head = lwd_merge(fork, "upstream/main")

    scope = lwd_compute_scope(fork, base, head, "upstream/main")

    assert scope.own_commits == ()
    assert scope.own_files == ()


def test_rename_tracks_new_path_with_old(tmp_path: Path):
    _upstream, fork = _repo_pair(tmp_path)
    base = lwd_git(fork, "rev-parse", "HEAD").strip()
    lwd_commit(fork, "feat(lwd): add a", {"vllm/x.py": "1\n2\n"})
    lwd_git(fork, "mv", "vllm/x.py", "vllm/y.py")
    lwd_git(fork, "commit", "-q", "-m", "refactor(lwd): rename")
    head = lwd_git(fork, "rev-parse", "HEAD").strip()

    scope = lwd_compute_scope(fork, base, head, "upstream/main")

    paths = {f.path: f for f in scope.own_files}
    assert set(paths) == {"vllm/y.py"}
    assert paths["vllm/y.py"].status == "renamed"
    assert paths["vllm/y.py"].old_path == "vllm/x.py"
    # Rename adds no lines; the original add of x.py counts once.
    assert scope.py_added_lines == 2


def test_py_added_lines_counts_own_python_outside_tests(tmp_path: Path):
    _upstream, fork = _repo_pair(tmp_path)
    base = lwd_git(fork, "rev-parse", "HEAD").strip()
    head = lwd_commit(
        fork,
        "feat(lwd): add files",
        {"vllm/big.py": "1\n2\n3\n", "tests/test_big.py": "1\n", "notes.txt": "x"},
    )

    scope = lwd_compute_scope(fork, base, head, "upstream/main")

    assert scope.py_added_lines == 3
