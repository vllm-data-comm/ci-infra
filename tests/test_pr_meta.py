from pathlib import Path

from gitrepo import lwd_clone_fork, lwd_commit, lwd_git, lwd_init

from lwd_checks.pr_meta import (
    lwd_check_bugfix_tests,
    lwd_check_rfc_link,
    lwd_referenced_issues,
)
from lwd_checks.scope import lwd_compute_scope


def _scope_with_added_lines(lines: int) -> object:
    # py_added_lines is the only scope input rfc-link reads; build the
    # real thing through a tiny repo to avoid faking the dataclass.
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    upstream, fork = tmp / "up", tmp / "fork"
    lwd_init(upstream)
    lwd_commit(upstream, "chore: base", {"a.txt": "a"})
    lwd_clone_fork(upstream, fork)
    lwd_commit(fork, "feat(lwd): x", {"vllm/big.py": "\n" * lines})
    base = lwd_git(fork, "rev-parse", "upstream/main").strip()
    head = lwd_git(fork, "rev-parse", "HEAD").strip()
    return lwd_compute_scope(fork, base, head, "upstream/main")


def _titles(titles: dict[tuple[str, int], str]):
    return lambda repo, number: titles.get((repo, number))


RFC = "[RFC]: Proposal for the LWD control plane"
REPO = "vllm-data-comm/vllm"


def test_over_500_lines_without_issue_reference_fails():
    scope = _scope_with_added_lines(600)
    result = lwd_check_rfc_link(
        scope, body="no references here", repo=REPO, fetch=_titles({})
    )
    assert result.status == "fail"
    assert "500" in result.summary


def test_over_500_lines_with_rfc_issue_passes():
    scope = _scope_with_added_lines(600)
    fetch = _titles({(REPO, 8): RFC})
    result = lwd_check_rfc_link(
        scope, body="see #8 for the design", repo=REPO, fetch=fetch
    )
    assert result.status == "pass"


def test_reference_to_non_rfc_issue_fails():
    scope = _scope_with_added_lines(600)
    fetch = _titles({(REPO, 8): "Ordinary bug"})
    result = lwd_check_rfc_link(scope, body="closes #8", repo=REPO, fetch=fetch)
    assert result.status == "fail"


def test_cross_repo_issue_url_is_followed():
    scope = _scope_with_added_lines(600)
    other = "vllm-data-comm/vllm-ascend"
    fetch = _titles({(other, 3): RFC})
    body = f"design in https://github.com/{other}/issues/3"
    result = lwd_check_rfc_link(scope, body=body, repo=REPO, fetch=fetch)
    assert result.status == "pass"


def test_at_or_below_500_lines_passes_without_reference():
    scope = _scope_with_added_lines(500)
    result = lwd_check_rfc_link(scope, body="nothing", repo=REPO, fetch=_titles({}))
    assert result.status == "pass"


def test_summary_reports_whether_docs_were_touched(tmp_path: Path):
    upstream, fork = tmp_path / "up", tmp_path / "fork"
    lwd_init(upstream)
    lwd_commit(upstream, "chore: base", {"a.txt": "a"})
    lwd_clone_fork(upstream, fork)
    lwd_commit(fork, "feat(lwd): x", {"vllm/big.py": "\n" * 600, "docs/design.md": "x"})
    base = lwd_git(fork, "rev-parse", "upstream/main").strip()
    head = lwd_git(fork, "rev-parse", "HEAD").strip()
    scope = lwd_compute_scope(fork, base, head, "upstream/main")

    result = lwd_check_rfc_link(scope, body="", repo=REPO, fetch=_titles({}))

    assert "docs/" in result.summary


def test_referenced_issues_parses_both_forms():
    refs = lwd_referenced_issues(
        "closes #8 and https://github.com/vllm-data-comm/vllm-ascend/issues/3"
    )
    assert refs == [("(current)", 8), ("vllm-data-comm/vllm-ascend", 3)]


def _scope_with_files(files: dict[str, str]):
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    upstream, fork = tmp / "up", tmp / "fork"
    lwd_init(upstream)
    lwd_commit(upstream, "chore: base", {"a.txt": "a"})
    lwd_clone_fork(upstream, fork)
    lwd_commit(fork, "feat(lwd): x", files)
    base = lwd_git(fork, "rev-parse", "upstream/main").strip()
    head = lwd_git(fork, "rev-parse", "HEAD").strip()
    return lwd_compute_scope(fork, base, head, "upstream/main")


def test_bugfix_pr_without_tests_is_flagged():
    scope = _scope_with_files({"vllm/mod.py": "A = 1\n"})
    result = lwd_check_bugfix_tests(scope, title="[Bugfix][Lwd] fix the deadlock")
    assert result.status == "fail"


def test_bugfix_pr_with_tests_passes():
    scope = _scope_with_files(
        {"vllm/mod.py": "A = 1\n", "tests/test_mod.py": "A = 1\n"}
    )
    result = lwd_check_bugfix_tests(scope, title="[Bugfix][Lwd] fix the deadlock")
    assert result.status == "pass"


def test_non_bugfix_pr_passes_without_tests():
    scope = _scope_with_files({"vllm/mod.py": "A = 1\n"})
    result = lwd_check_bugfix_tests(scope, title="[Feature][Lwd] add the picker")
    assert result.status == "pass"
