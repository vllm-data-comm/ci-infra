"""Render the checklist comment from lane results.

One comment per PR, rewritten in place on every update, so manual edits
to it are always temporary. Checkboxes are display only: committers
accept advisory findings by approving the PR.
"""

from __future__ import annotations

from lwd_checks.catalog import (
    LWD_CHECK_CATALOG,
    LWD_GATING_ADVISORY,
    LWD_GATING_REQUIRED,
)
from lwd_checks.results import LwdCheckResult

LWD_COMMENT_MARKER = "<!-- lwd-ci-checklist -->"
_LWD_MAX_FINDINGS_PER_CHECK = 50
_LWD_MAX_FINDING_CHARS = 200
_LWD_MAX_COMMENT_CHARS = 60000


def lwd_escape(text: str) -> str:
    escaped = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("|", "\\|")
    )
    return escaped.replace("@", "`@`")


def _lwd_check_line(result: LwdCheckResult | None, spec) -> list[str]:
    title = spec.title
    gating = LWD_GATING_REQUIRED if spec.gating == "required" else LWD_GATING_ADVISORY
    if result is None:
        return [f"- [ ] {title} ({gating}) — did not run"]
    if result.status == "pass":
        suffix = f": {result.summary}" if result.summary else ""
        return [f"- [x] {title} ({gating}){suffix}"]
    lines = [f"- [ ] {title} ({gating})"]
    shown = result.findings[:_LWD_MAX_FINDINGS_PER_CHECK]
    for finding in shown:
        location = finding.path
        if finding.line is not None:
            location = f"{finding.path}:{finding.line}"
        prefix = f"`{lwd_escape(location)}` " if location else ""
        message = lwd_escape(finding.message)[:_LWD_MAX_FINDING_CHARS]
        lines.append(f"  - {prefix}{message}")
    hidden = len(result.findings) - len(shown)
    if hidden > 0:
        lines.append(f"  - (+{hidden} more)")
    if result.summary and not result.findings:
        lines.append(f"  - {lwd_escape(result.summary)}")
    return lines


def _lwd_lane_section(
    lane: str, results: list[LwdCheckResult] | None, head_sha: str, bypassed: bool
) -> list[str]:
    label = "Fast lane" if lane == "fast" else "Slow lane"
    lines = [f"**{label}** · `{head_sha[:7]}`", ""]
    if bypassed:
        lines.insert(1, "> The `no-test` label bypassed this lane for merging.")
    if results is None:
        lines.append("- lane did not run yet")
        return lines
    by_id = {r.check_id: r for r in results}
    for spec in LWD_CHECK_CATALOG.values():
        if spec.lane == lane:
            lines.extend(_lwd_check_line(by_id.get(spec.check_id), spec))
    return lines


def lwd_render_checklist(
    fast_results: list[LwdCheckResult] | None,
    slow_results: list[LwdCheckResult] | None,
    head_sha: str,
    *,
    slow_bypassed: bool = False,
) -> str:
    lines = [LWD_COMMENT_MARKER, "### LWD CI checklist", ""]
    lines.extend(_lwd_lane_section("fast", fast_results, head_sha, bypassed=False))
    lines.append("")
    lines.extend(
        _lwd_lane_section("slow", slow_results, head_sha, bypassed=slow_bypassed)
    )
    text = "\n".join(lines) + "\n"
    if len(text) > _LWD_MAX_COMMENT_CHARS:
        note = "\n… (checklist truncated)"
        text = text[: _LWD_MAX_COMMENT_CHARS - len(note)] + note
    return text
