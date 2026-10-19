"""Result records: what each check job reports and the gate renders.

A results file is a small JSON document, one per check (or one file per
check inside a job artifact directory):

    {"schema": 1, "lane": "fast", "pr": 123, "head_sha": "0123abc",
     "checks": [{"check_id": "param-count", "status": "fail",
                 "summary": "...", "findings": [{"path": "...", "line": 42,
                                                 "message": "..."}]}]}
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from lwd_checks.catalog import LWD_CHECK_CATALOG, LWD_LANES, LwdCheckSpec

LWD_RESULTS_SCHEMA = 1
LWD_STATUSES = ("pass", "fail", "error")


@dataclass(frozen=True)
class LwdFinding:
    path: str
    line: int | None
    message: str


@dataclass(frozen=True)
class LwdCheckResult:
    check_id: str
    status: str
    summary: str = ""
    findings: tuple[LwdFinding, ...] = ()


def lwd_write_results(
    path: Path | str,
    lane: str,
    pr_number: int,
    head_sha: str,
    results: list[LwdCheckResult],
) -> None:
    payload = {
        "schema": LWD_RESULTS_SCHEMA,
        "lane": lane,
        "pr": pr_number,
        "head_sha": head_sha,
        "checks": [
            {
                "check_id": r.check_id,
                "status": r.status,
                "summary": r.summary,
                "findings": [
                    {"path": f.path, "line": f.line, "message": f.message}
                    for f in r.findings
                ],
            }
            for r in results
        ],
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n")


def lwd_parse_results_payload(
    payload: dict, source: str
) -> tuple[str, list[LwdCheckResult]]:
    if payload.get("schema") != LWD_RESULTS_SCHEMA:
        raise ValueError(f"{source}: unsupported schema {payload.get('schema')}")
    lane = payload["lane"]
    if lane not in LWD_LANES:
        raise ValueError(f"{source}: unknown lane {lane}")
    results = []
    for check in payload["checks"]:
        check_id = check["check_id"]
        if check_id not in LWD_CHECK_CATALOG:
            raise ValueError(f"{source}: {check_id} not-in-catalog")
        if check["status"] not in LWD_STATUSES:
            raise ValueError(f"{source}: unknown status {check['status']}")
        findings = tuple(
            LwdFinding(
                path=f["path"],
                line=f["line"],
                message=f["message"],
            )
            for f in check.get("findings", [])
        )
        results.append(
            LwdCheckResult(
                check_id=check_id,
                status=check["status"],
                summary=check.get("summary", ""),
                findings=findings,
            )
        )
    return lane, results


def lwd_load_results(path: Path | str) -> tuple[str, list[LwdCheckResult]]:
    """Load one results file, or every results file in a directory."""
    path = Path(path)
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    if not files:
        raise ValueError(f"{path}: no results files")
    lanes: set[str] = set()
    results: list[LwdCheckResult] = []
    for file in files:
        lane, loaded = lwd_parse_results_payload(
            json.loads(file.read_text()), source=str(file)
        )
        lanes.add(lane)
        results.extend(loaded)
    if len(lanes) > 1:
        raise ValueError(f"{path}: mixed lanes {sorted(lanes)}")
    return lanes.pop(), results


def lwd_exit_code(result: LwdCheckResult, spec: LwdCheckSpec) -> int:
    """Exit code for a check job: advisory failures stay green, errors do not."""
    if result.status == "pass":
        return 0
    if result.status == "error":
        return 1
    return 1 if spec.gating == "required" else 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="lwd_checks.results")
    sub = parser.add_subparsers(dest="command", required=True)
    report = sub.add_parser("report", help="write one check result from CLI args")
    report.add_argument("--check-id", required=True)
    report.add_argument("--status", required=True, choices=LWD_STATUSES)
    report.add_argument("--summary", default="")
    report.add_argument("--lane", required=True, choices=LWD_LANES)
    report.add_argument("--pr", type=int, required=True)
    report.add_argument("--head-sha", required=True)
    report.add_argument("--out", required=True)
    args = parser.parse_args()

    findings = tuple(
        LwdFinding(path="", line=None, message=line.strip())
        for line in _stdin_findings()
    )
    result = LwdCheckResult(
        check_id=args.check_id,
        status=args.status,
        summary=args.summary,
        findings=findings,
    )
    lwd_write_results(
        args.out,
        lane=args.lane,
        pr_number=args.pr,
        head_sha=args.head_sha,
        results=[result],
    )
    spec = LWD_CHECK_CATALOG[args.check_id]
    raise SystemExit(lwd_exit_code(result, spec))


def _stdin_findings() -> list[str]:
    import sys

    return [] if sys.stdin is None or sys.stdin.isatty() else sys.stdin.readlines()


if __name__ == "__main__":
    main()
