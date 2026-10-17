from lwd_checks.gate import lwd_gate
from lwd_checks.results import LwdCheckResult


def _fast_required_all_pass() -> list[LwdCheckResult]:
    return [
        LwdCheckResult(check_id="pr-title", status="pass"),
        LwdCheckResult(check_id="commits", status="pass"),
        LwdCheckResult(check_id="pre-commit", status="pass"),
    ]


def test_fast_gate_passes_when_required_checks_pass():
    code, result = lwd_gate("fast", _fast_required_all_pass())
    assert code == 0
    assert result.status == "pass"


def test_missing_required_result_fails_gate():
    results = _fast_required_all_pass()[:-1]
    code, result = lwd_gate("fast", results)
    assert code == 1
    assert "pre-commit" in result.summary


def test_required_failure_fails_gate():
    results = _fast_required_all_pass()
    results[1] = LwdCheckResult(check_id="commits", status="fail")
    code, _result = lwd_gate("fast", results)
    assert code == 1


def test_advisory_failure_does_not_fail_gate():
    results = _fast_required_all_pass() + [
        LwdCheckResult(check_id="param-count", status="fail"),
    ]
    code, _result = lwd_gate("fast", results)
    assert code == 0


def test_unknown_check_id_fails_gate():
    results = _fast_required_all_pass() + [
        LwdCheckResult(check_id="made-up-check", status="pass"),
    ]
    code, result = lwd_gate("fast", results)
    assert code == 1
    assert "made-up-check" in result.summary


def test_no_test_bypasses_slow_gate_only():
    slow_results = [LwdCheckResult(check_id="lwd-unit-tests", status="fail")]
    code, result = lwd_gate("slow", slow_results, no_test=True)
    assert code == 0
    assert "no-test" in result.summary

    code, _result = lwd_gate("slow", slow_results, no_test=False)
    assert code == 1

    fast_failed = _fast_required_all_pass()
    fast_failed[0] = LwdCheckResult(check_id="pr-title", status="fail")
    code, _result = lwd_gate("fast", fast_failed, no_test=True)
    assert code == 1
