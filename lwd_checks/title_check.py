"""PR title check: `[Type]...[Lwd] description`.

Rules (AGENTS.md section 4): the first tag is a type tag, `[Lwd]` appears
among the tags, and a space plus a description follow the tags.
"""

from __future__ import annotations

import argparse
import re

from lwd_checks.results import LwdCheckResult, lwd_write_results

_LWD_TITLE_TYPE_TAGS = (
    "Bugfix",
    "Feature",
    "Perf",
    "Refactor",
    "CI",
    "CI/Build",
    "Test",
    "Doc",
    "Docs",
    "Misc",
)
_LWD_TITLE_TAG_RE = re.compile(r"^\[[^][]+\]")
_LWD_TITLE_DESC_RE = re.compile(r" +\S")


def _lwd_parse_tags_with_end(title: str) -> tuple[list[str], int]:
    """Return the leading `[...]` tag contents and where they end."""
    tags: list[str] = []
    end = 0
    rest = title
    while True:
        match = _LWD_TITLE_TAG_RE.match(rest)
        if match is None:
            return tags, end
        tags.append(match.group(0)[1:-1])
        end += match.end()
        rest = rest[match.end() :]


def lwd_parse_title_tags(title: str) -> list[str]:
    return _lwd_parse_tags_with_end(title)[0]


def lwd_check_title(title: str) -> LwdCheckResult:
    reasons: list[str] = []
    tags, tags_end = _lwd_parse_tags_with_end(title)
    if not tags:
        reasons.append(
            "title must start with type tags such as [Bugfix][Lwd], got no tags"
        )
    else:
        if tags[0] not in _LWD_TITLE_TYPE_TAGS:
            reasons.append(
                f"first tag [{tags[0]}] is not a type tag "
                f"(one of {', '.join(_LWD_TITLE_TYPE_TAGS)})"
            )
        if "Lwd" not in tags:
            reasons.append("missing the [Lwd] scope tag")
        if not _LWD_TITLE_DESC_RE.match(title[tags_end:]):
            reasons.append("tags must be followed by a space and a description")
    return LwdCheckResult(
        check_id="pr-title",
        status="fail" if reasons else "pass",
        summary="; ".join(reasons),
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="lwd_checks.title_check")
    parser.add_argument("--title", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    result = lwd_check_title(args.title)
    lwd_write_results(
        args.out,
        lane="fast",
        pr_number=args.pr,
        head_sha=args.head_sha,
        results=[result],
    )
    raise SystemExit(0 if result.status == "pass" else 1)


if __name__ == "__main__":
    main()
