from __future__ import annotations

import json
from pathlib import Path

import pytest

from lwd_checks.catalog import LwdCheckSpec
from lwd_checks.results import (
    LwdCheckResult,
    LwdFinding,
    lwd_exit_code,
    lwd_load_results,
    lwd_write_results,
)


def _passing(check_id: str) -> LwdCheckResult:
    return LwdCheckResult(check_id=check_id, status="pass")


def test_results_roundtrip(tmp_path: Path):
    out = tmp_path / "r.json"
    result = LwdCheckResult(
        check_id="param-count",
        status="fail",
        summary="1 function over 5 parameters",
        findings=(LwdFinding(path="vllm/x.py", line=42, message="too many"),),
    )
    lwd_write_results(out, lane="fast", pr_number=7, head_sha="abc", results=[result])

    lane, loaded = lwd_load_results(out)
    assert lane == "fast"
    assert loaded == [result]


def test_load_rejects_wrong_schema_version(tmp_path: Path):
    out = tmp_path / "r.json"
    out.write_text(
        json.dumps(
            {"schema": 99, "lane": "fast", "pr": 1, "head_sha": "a", "checks": []}
        )
    )
    with pytest.raises(ValueError, match="schema"):
        lwd_load_results(out)


def test_load_rejects_unknown_status_and_unknown_check(tmp_path: Path):
    out = tmp_path / "r.json"
    lwd_write_results(
        out, lane="fast", pr_number=1, head_sha="a", results=[_passing("pr-title")]
    )
    payload = json.loads(out.read_text())
    payload["checks"][0]["status"] = "maybe"
    out.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="status"):
        lwd_load_results(out)

    payload["checks"][0]["status"] = "pass"
    payload["checks"][0]["check_id"] = "not-in-catalog"
    out.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="not-in-catalog"):
        lwd_load_results(out)


def test_load_accepts_directory_of_check_files(tmp_path: Path):
    for check_id in ("param-count", "class-prefix"):
        lwd_write_results(
            tmp_path / f"{check_id}.json",
            lane="fast",
            pr_number=1,
            head_sha="a",
            results=[_passing(check_id)],
        )
    lane, loaded = lwd_load_results(tmp_path)
    assert lane == "fast"
    assert {r.check_id for r in loaded} == {"param-count", "class-prefix"}


def test_exit_code_follows_catalog_gating():
    required = LwdCheckSpec("pr-title", "PR title", "fast", "required")
    advisory = LwdCheckSpec("param-count", "Parameter count", "fast", "advisory")
    assert lwd_exit_code(_passing("pr-title"), required) == 0
    assert lwd_exit_code(LwdCheckResult("pr-title", "fail"), required) == 1
    assert lwd_exit_code(LwdCheckResult("param-count", "fail"), advisory) == 0
    assert lwd_exit_code(LwdCheckResult("pr-title", "error"), advisory) == 1


def test_write_creates_missing_directories(tmp_path: Path):
    out = tmp_path / "nested" / "results" / "r.json"
    lwd_write_results(out, lane="fast", pr_number=1, head_sha="a", results=[])
    assert out.exists()
