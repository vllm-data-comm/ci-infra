"""Check catalog: the single place defining each check's lane and gating.

Every check has two independent attributes:

- lane: when the result arrives. The fast lane reports in ~2 min and
  reruns on every PR edit.
- gating: "required" (blocks merging via the lane gate) or "advisory"
  (only appears in the checklist comment).

Promoting a check from advisory to required is a one-line change here and
applies to every calling repository at once.
"""

from __future__ import annotations

from dataclasses import dataclass

LWD_LANE_FAST = "fast"
# TODO(tu10ng): add the slow lane (LWD unit tests, mypy) once main's tests
# are meaningful, vllm-data-comm/ci-infra#4
LWD_LANES = (LWD_LANE_FAST,)
LWD_GATING_REQUIRED = "required"
LWD_GATING_ADVISORY = "advisory"


@dataclass(frozen=True)
class LwdCheckSpec:
    check_id: str
    title: str
    lane: str
    gating: str


LWD_CHECK_CATALOG: dict[str, LwdCheckSpec] = {
    spec.check_id: spec
    for spec in (
        LwdCheckSpec("pr-title", "PR title", LWD_LANE_FAST, LWD_GATING_REQUIRED),
        LwdCheckSpec("commits", "Commits", LWD_LANE_FAST, LWD_GATING_REQUIRED),
        LwdCheckSpec("pre-commit", "pre-commit", LWD_LANE_FAST, LWD_GATING_REQUIRED),
        LwdCheckSpec(
            "param-count", "Parameter count", LWD_LANE_FAST, LWD_GATING_ADVISORY
        ),
        LwdCheckSpec(
            "function-length", "Function length", LWD_LANE_FAST, LWD_GATING_ADVISORY
        ),
        LwdCheckSpec(
            "class-prefix", "Class prefix", LWD_LANE_FAST, LWD_GATING_ADVISORY
        ),
        LwdCheckSpec(
            "new-file-path", "New file path", LWD_LANE_FAST, LWD_GATING_ADVISORY
        ),
        LwdCheckSpec("patch-usage", "Patch usage", LWD_LANE_FAST, LWD_GATING_ADVISORY),
        LwdCheckSpec("rfc-link", "RFC link", LWD_LANE_FAST, LWD_GATING_ADVISORY),
        LwdCheckSpec(
            "bugfix-tests", "Bugfix tests", LWD_LANE_FAST, LWD_GATING_ADVISORY
        ),
    )
}
