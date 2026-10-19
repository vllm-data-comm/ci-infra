"""Lane gates: the required checks of a lane must all have run and passed.

A skipped job counts as passing in GitHub's branch protection, so the
gate refuses to succeed when a required check is simply missing.
"""

from __future__ import annotations

import argparse

from lwd_checks.catalog import LWD_CHECK_CATALOG, LWD_GATING_REQUIRED, LWD_LANES
from lwd_checks.results import (
    LwdCheckResult,
    LwdFinding,
    lwd_load_results,
)


def lwd_gate(lane: str, results: list[LwdCheckResult]) -> tuple[int, LwdCheckResult]:
    check_id = f"{lane}-gate"
    known_ids = set(LWD_CHECK_CATALOG)
    unknown = sorted(r.check_id for r in results if r.check_id not in known_ids)
    if unknown:
        return (
            1,
            LwdCheckResult(
                check_id=check_id,
                status="error",
                summary=f"unknown check ids: {', '.join(unknown)}",
            ),
        )

    by_id = {r.check_id: r for r in results}
    problems: list[str] = []
    for spec in LWD_CHECK_CATALOG.values():
        if spec.lane != lane or spec.gating != LWD_GATING_REQUIRED:
            continue
        result = by_id.get(spec.check_id)
        if result is None:
            problems.append(f"{spec.check_id} did not run")
        elif result.status != "pass":
            problems.append(f"{spec.check_id} {result.status}")

    if problems:
        return (
            1,
            LwdCheckResult(
                check_id=check_id,
                status="fail",
                summary="; ".join(problems),
                findings=tuple(
                    LwdFinding(path="", line=None, message=p) for p in problems
                ),
            ),
        )
    return (0, LwdCheckResult(check_id=check_id, status="pass"))


def main() -> None:
    parser = argparse.ArgumentParser(prog="lwd_checks.gate")
    parser.add_argument("--lane", required=True, choices=LWD_LANES)
    parser.add_argument("--results-dir", required=True)
    args = parser.parse_args()

    _lane, results = lwd_load_results(args.results_dir)
    code, _gate_result = lwd_gate(args.lane, results)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
