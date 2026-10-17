"""Turn a pytest junitxml report into a check result."""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as ElementTree

from lwd_checks.results import LwdCheckResult, LwdFinding, lwd_write_results

_LWD_MAX_MESSAGE_CHARS = 200


def lwd_parse_junit(xml_texts: list[str], *, check_id: str) -> LwdCheckResult:
    """Merge one or more junitxml reports into a single check result."""
    suites: list[ElementTree.Element] = []
    for xml_text in xml_texts:
        root = ElementTree.fromstring(xml_text)
        # pytest writes one <testsuite> root; junit-xml merges into one too.
        suites.extend(
            [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
        )
    tests = sum(int(s.get("tests", 0)) for s in suites)
    failures = sum(int(s.get("failures", 0)) for s in suites)
    errors = sum(int(s.get("errors", 0)) for s in suites)
    skipped = sum(int(s.get("skipped", 0)) for s in suites)

    findings = []
    for suite in suites:
        for case in suite.findall("testcase"):
            for failure in list(case.findall("failure")) + list(case.findall("error")):
                message = (failure.get("message") or "").strip()
                findings.append(
                    LwdFinding(
                        path=case.get("classname") or "",
                        line=None,
                        message=f"{case.get('name', '?')}: {message}"[
                            :_LWD_MAX_MESSAGE_CHARS
                        ],
                    )
                )

    failed = failures + errors
    return LwdCheckResult(
        check_id=check_id,
        status="fail" if failed else "pass",
        summary=f"{tests - failed} passed, {failed} failed, {skipped} skipped",
        findings=tuple(findings),
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="lwd_checks.junit")
    parser.add_argument("--xml", nargs="+", required=True)
    parser.add_argument("--check-id", required=True)
    parser.add_argument("--lane", required=True, choices=["fast", "slow"])
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    result = lwd_parse_junit(
        [open(xml, encoding="utf-8").read() for xml in args.xml],
        check_id=args.check_id,
    )
    lwd_write_results(
        args.out,
        lane=args.lane,
        pr_number=args.pr,
        head_sha=args.head_sha,
        results=[result],
    )
    raise SystemExit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()
