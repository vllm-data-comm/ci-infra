from __future__ import annotations

from lwd_checks.catalog import LWD_CHECK_CATALOG
from lwd_checks.render import lwd_render_checklist
from lwd_checks.results import LwdCheckResult, LwdFinding


def _all_pass(lane: str) -> list[LwdCheckResult]:
    return [
        LwdCheckResult(check_id=c.check_id, status="pass")
        for c in LWD_CHECK_CATALOG.values()
        if c.lane == lane
    ]


def test_checklist_renders_checks_in_catalog_order():
    fast = _all_pass("fast")
    fast[1] = LwdCheckResult(
        check_id="commits",
        status="fail",
        summary="1 commit(s) need fixes",
        findings=(LwdFinding(path="", line=None, message="0123456: missing signoff"),),
    )

    text = lwd_render_checklist({"fast": fast}, head_sha="0123abcdef")

    lines = text.splitlines()
    assert lines[0] == "<!-- lwd-ci-checklist -->"
    assert "**Fast lane** · `0123abc`" in text
    # Catalog order: pr-title before commits before pre-commit.
    assert text.index("PR title") < text.index("Commits") < text.index("pre-commit")
    assert "- [x] PR title (required)" in text
    assert "- [ ] Commits (required)" in text
    assert "0123456: missing signoff" in text


def test_findings_render_as_location_then_message():
    result = [
        LwdCheckResult(
            check_id="param-count",
            status="fail",
            findings=(LwdFinding(path="vllm/x.py", line=42, message="`build` has 7"),),
        )
    ]
    text = lwd_render_checklist({"fast": result}, head_sha="0123abc")
    assert "  - `vllm/x.py:42` `build` has 7" in text


def test_findings_are_escaped():
    result = [
        LwdCheckResult(
            check_id="param-count",
            status="fail",
            findings=(
                LwdFinding(
                    path="v/x.py",
                    line=1,
                    message="<b> & `x` | someone@example.com",
                ),
            ),
        )
    ]
    text = lwd_render_checklist({"fast": result}, head_sha="0123abc")
    assert "<b>" not in text
    assert "&lt;b&gt;" in text
    assert "&amp;" in text
    assert "|" not in text.replace("\|", "")
    assert "someone@example.com" not in text


def test_more_than_fifty_findings_are_truncated():
    findings = tuple(LwdFinding(path="v/x.py", line=i, message="m") for i in range(60))
    result = [LwdCheckResult(check_id="param-count", status="fail", findings=findings)]
    text = lwd_render_checklist({"fast": result}, head_sha="0123abc")
    assert "(+10 more)" in text
    assert text.count("m\n") <= 51


def test_lane_without_results_renders_not_run_yet():
    text = lwd_render_checklist({"fast": None}, head_sha="0123abc")
    assert "**Fast lane**" in text
    assert "- lane did not run yet" in text


def test_missing_check_renders_did_not_run():
    fast = [r for r in _all_pass("fast") if r.check_id != "pre-commit"]
    text = lwd_render_checklist({"fast": fast}, head_sha="0123abc")
    assert "- [ ] pre-commit (required) — did not run" in text


def test_comment_is_capped():
    findings = tuple(
        LwdFinding(path="v/x.py", line=i, message="m" * 200) for i in range(500)
    )
    result = [LwdCheckResult(check_id="param-count", status="fail", findings=findings)]
    text = lwd_render_checklist({"fast": result}, head_sha="0123abc")
    assert len(text) <= 60000
