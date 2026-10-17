from pathlib import Path

from lwd_checks.discover import lwd_discover_test_dirs
from lwd_checks.junit import lwd_parse_junit
from lwd_checks.mypy_report import lwd_parse_mypy_log

_JUNIT_SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="tests" tests="45" failures="1" errors="0" skipped="3" time="1.2">
  <testcase name="test_api.py::test_roundtrip" classname="test_api" time="0.1"/>
  <testcase name="test_lwd_config.py::test_bad" classname="test_lwd_config" time="0.2">
    <failure message="AssertionError: assert False">Traceback here</failure>
  </testcase>
</testsuite>
"""


def test_junit_counts_and_failures():
    result = lwd_parse_junit([_JUNIT_SAMPLE], check_id="lwd-unit-tests")

    assert result.status == "fail"
    assert result.summary == "44 passed, 1 failed, 3 skipped"
    assert len(result.findings) == 1
    assert "test_lwd_config.py::test_bad" in result.findings[0].message


def test_junit_all_pass():
    passing = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="tests" tests="45" failures="0" errors="0" skipped="0" time="1.2">
  <testcase name="ok" time="0.1"/>
</testsuite>
"""
    result = lwd_parse_junit([passing], check_id="lwd-unit-tests")
    assert result.status == "pass"
    assert result.summary == "45 passed, 0 failed, 0 skipped"


def test_mypy_log_errors_become_findings():
    log = (
        "vllm/x.py:57: error: Incompatible return value type "
        '(got "int", expected "str")  [return-value]\n'
        "vllm/x.py:60: note: def f\n"
        "Found 1 error in 1 file (checked 3 source files)\n"
    )
    result = lwd_parse_mypy_log(log)

    assert result.status == "fail"
    assert len(result.findings) == 1
    assert result.findings[0].line == 57
    assert "return-value" in result.findings[0].message


def test_mypy_log_clean_passes():
    result = lwd_parse_mypy_log("Success: no issues found in 12 source files\n")
    assert result.status == "pass"


def test_discover_picks_topmost_lwd_dirs(tmp_path: Path):
    for rel in (
        "tests/config/lwd/x.py",
        "tests/v1/lwd_control/y.py",
        "tests/models/lang.py",
        "tests/config/other.py",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")

    dirs = lwd_discover_test_dirs(tmp_path / "tests")

    assert dirs == [tmp_path / "tests/config/lwd", tmp_path / "tests/v1/lwd_control"]


def test_discover_empty_when_no_lwd_dirs(tmp_path: Path):
    (tmp_path / "tests/models").mkdir(parents=True)
    assert lwd_discover_test_dirs(tmp_path / "tests") == []


def test_junit_merges_multiple_reports():
    one = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="tests" tests="10" failures="0" errors="0" skipped="1" time="0.1">
</testsuite>
"""
    two = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="tests" tests="5" failures="1" errors="0" skipped="0" time="0.1">
  <testcase name="bad" classname="c" time="0.1">
    <failure message="boom">tb</failure>
  </testcase>
</testsuite>
"""
    result = lwd_parse_junit([one, two], check_id="lwd-unit-tests")
    assert result.summary == "14 passed, 1 failed, 1 skipped"
    assert result.status == "fail"
