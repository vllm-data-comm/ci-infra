"""Turn a mypy log into a check result.

mypy runs as an advisory check: the workflow captures the log, this
module turns error lines into findings, and the job exits zero either
way. The job only goes red when this module itself crashes.
"""

from __future__ import annotations

import argparse
import re

from lwd_checks.results import LwdCheckResult, LwdFinding, lwd_write_results

_LWD_MYPY_ERROR_RE = re.compile(r"^(.+?):(\d+): (?:error|warning): (.*)$")


def lwd_parse_mypy_log(log: str) -> LwdCheckResult:
    findings = []
    for line in log.splitlines():
        match = _LWD_MYPY_ERROR_RE.match(line)
        if match:
            findings.append(
                LwdFinding(
                    path=match.group(1),
                    line=int(match.group(2)),
                    message=match.group(3),
                )
            )
    return LwdCheckResult(
        check_id="mypy",
        status="fail" if findings else "pass",
        summary=(f"{len(findings)} mypy error(s)" if findings else "no mypy errors"),
        findings=tuple(findings),
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="lwd_checks.mypy_report")
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--lane", required=True, choices=["fast", "slow"])
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    result = lwd_parse_mypy_log(open(args.log_file, encoding="utf-8").read())
    lwd_write_results(
        args.out,
        lane=args.lane,
        pr_number=args.pr,
        head_sha=args.head_sha,
        results=[result],
    )
    # Advisory: never fail the job from findings.


if __name__ == "__main__":
    main()
