import pytest

from lwd_checks.commits_check import lwd_check_commits
from lwd_checks.scope import LwdCommitInfo


def _commit(
    subject: str,
    *,
    email: str = "dev@example.com",
    signoff: tuple[str, ...] = ("dev@example.com",),
) -> LwdCommitInfo:
    return LwdCommitInfo(
        sha="0123456789abcdef",
        subject=subject,
        author_email=email,
        signoff_emails=signoff,
    )


@pytest.mark.parametrize(
    "subject",
    [
        "feat(lwd): add support for xxx",
        "fix(lwd): repair the sync problem",
        "perf(lwd): avoid CPU-NPU sync",
        "refactor(lwd): rename the link spec",
        "docs(lwd): add the control plane design",
        "test(lwd): cover the failure branch",
        "ci(lwd): add the fast lane",
        "chore(lwd): apply pre-commit fixes",
    ],
)
def test_all_commit_types_pass(subject: str):
    result = lwd_check_commits([_commit(subject)], base_sha="abc")
    assert result.status == "pass"


@pytest.mark.parametrize(
    "subject",
    [
        "feat(npu): wrong scope",
        "feat: missing scope",
        "feat(lwd):no space after colon",
        "add stuff",
        "feat(lwd): 中文标题",
        "fix(lwd): full-width：colon",
    ],
)
def test_bad_subjects_fail(subject: str):
    result = lwd_check_commits([_commit(subject)], base_sha="abc")
    assert result.status == "fail"
    assert result.findings


def test_missing_signoff_fails_with_fix_hint():
    result = lwd_check_commits(
        [_commit("feat(lwd): x", signoff=())], base_sha="abc1234"
    )
    assert result.status == "fail"
    assert "git rebase --signoff abc1234" in result.summary
    assert "0123456" in result.findings[0].message


def test_signoff_email_matches_author_case_insensitively():
    commit = _commit(
        "feat(lwd): x",
        email="Dev@Example.com",
        signoff=("dev@example.com",),
    )
    assert lwd_check_commits([commit], base_sha="abc").status == "pass"


def test_signoff_from_another_email_does_not_count():
    commit = _commit(
        "feat(lwd): x",
        email="dev@example.com",
        signoff=("someone-else@example.com",),
    )
    assert lwd_check_commits([commit], base_sha="abc").status == "fail"


def test_no_own_commits_passes():
    assert lwd_check_commits([], base_sha="abc").status == "pass"


def test_every_bad_commit_is_listed():
    commits = [
        _commit("feat(lwd): good", signoff=()),
        _commit("bad subject"),
        _commit("fix(lwd): also good", signoff=()),
    ]
    result = lwd_check_commits(commits, base_sha="abc")
    assert result.status == "fail"
    assert len(result.findings) == 3
