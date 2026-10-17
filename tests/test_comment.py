import io
import json
import zipfile
from pathlib import Path

from lwd_checks.comment import lwd_run_relay
from lwd_checks.render import LWD_COMMENT_MARKER
from lwd_checks.results import (
    LwdCheckResult,
    LwdFinding,
    lwd_parse_results_payload,
    lwd_write_results,
)

REPO = "vllm-data-comm/vllm"
SHA = "0123abcdef0123abcdef0123abcdef0123abcd"


class _FakeApi:
    def __init__(self) -> None:
        self.get_responses: dict[str, object] = {}
        self.binaries: dict[str, bytes] = {}
        self.posts: list[tuple[str, dict]] = []
        self.patches: list[tuple[str, dict]] = []

    def get(self, path: str) -> object:
        return self.get_responses[path]

    def get_binary(self, url: str) -> bytes:
        return self.binaries[url]

    def post(self, path: str, payload: dict) -> object:
        self.posts.append((path, payload))
        return {}

    def patch(self, path: str, payload: dict) -> object:
        self.patches.append((path, payload))
        return {}


def _pr(number: int = 7, labels: tuple[str, ...] = ()) -> dict:
    return {
        "number": number,
        "state": "open",
        "merged_at": None,
        "head": {"sha": SHA},
        "base": {"repo": {"full_name": REPO}},
        "labels": [{"name": name} for name in labels],
    }


def _artifact_zip(checks: list[LwdCheckResult], lane: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for check in checks:
            payload = {
                "schema": 1,
                "lane": lane,
                "pr": 7,
                "head_sha": SHA,
                "checks": [
                    {
                        "check_id": check.check_id,
                        "status": check.status,
                        "summary": check.summary,
                        "findings": [
                            {"path": f.path, "line": f.line, "message": f.message}
                            for f in check.findings
                        ],
                    }
                ],
            }
            archive.writestr(f"{check.check_id}.json", json.dumps(payload))
    return buffer.getvalue()


def _api_with_lanes(
    fast_checks: list[LwdCheckResult] | None,
    slow_checks: list[LwdCheckResult] | None,
    *,
    labels: tuple[str, ...] = (),
    existing_comment: dict | None = None,
) -> _FakeApi:
    api = _FakeApi()
    api.get_responses[f"/repos/{REPO}/actions/runs/11"] = {"head_sha": SHA}
    api.get_responses[f"/repos/{REPO}/commits/{SHA}/pulls"] = [_pr(labels=labels)]
    for workflow_file, checks, lane_run_id in (
        ("lwd_fast.yml", fast_checks, 21),
        ("lwd_slow.yml", slow_checks, 22),
    ):
        if checks is None:
            api.get_responses[
                f"/repos/{REPO}/actions/workflows/{workflow_file}/runs"
                f"?head_sha={SHA}&event=pull_request&per_page=1"
            ] = {"workflow_runs": []}
            continue
        api.get_responses[
            f"/repos/{REPO}/actions/workflows/{workflow_file}/runs"
            f"?head_sha={SHA}&event=pull_request&per_page=1"
        ] = {"workflow_runs": [{"id": lane_run_id}]}
        lane = "fast" if "fast" in workflow_file else "slow"
        api.get_responses[
            f"/repos/{REPO}/actions/runs/{lane_run_id}/artifacts?per_page=100"
        ] = {
            "artifacts": [
                {
                    "name": f"lwd-results-{lane}-job",
                    "archive_download_url": f"zip://{lane}",
                }
            ]
        }
        api.binaries[f"zip://{lane}"] = _artifact_zip(checks, lane)
    api.get_responses[f"/repos/{REPO}/issues/7/comments?per_page=100"] = (
        [existing_comment] if existing_comment else []
    )
    return api


def _fast_all_pass() -> list[LwdCheckResult]:
    return [
        LwdCheckResult(check_id="pr-title", status="pass"),
        LwdCheckResult(check_id="commits", status="pass"),
        LwdCheckResult(check_id="pre-commit", status="pass"),
        LwdCheckResult(
            check_id="param-count",
            status="fail",
            findings=(LwdFinding(path="vllm/x.py", line=3, message="ping @room"),),
        ),
    ]


def test_relay_posts_rendered_comment_for_both_lanes(tmp_path: Path):
    fast = _fast_all_pass()
    slow = [
        LwdCheckResult(check_id="lwd-unit-tests", status="pass", summary="9 passed"),
        LwdCheckResult(check_id="mypy", status="pass"),
    ]
    api = _api_with_lanes(fast, slow)

    lwd_run_relay(api, REPO, 11)

    assert len(api.posts) == 1
    path, payload = api.posts[0]
    assert path == f"/repos/{REPO}/issues/7/comments"
    assert payload["body"].startswith(LWD_COMMENT_MARKER)
    assert "`vllm/x.py:3` ping `@`room" in payload["body"]
    assert "9 passed" in payload["body"]


def test_relay_updates_existing_marker_comment_in_place():
    existing = {"id": 55, "body": LWD_COMMENT_MARKER + "\nold"}
    api = _api_with_lanes(_fast_all_pass(), None, existing_comment=existing)

    lwd_run_relay(api, REPO, 11)

    assert not api.posts
    assert len(api.patches) == 1
    assert api.patches[0][0] == f"/repos/{REPO}/issues/comments/55"


def test_stale_pr_head_makes_relay_skip():
    api = _api_with_lanes(_fast_all_pass(), None)
    moved = _pr()
    moved["head"]["sha"] = "f" * 40
    api.get_responses[f"/repos/{REPO}/commits/{SHA}/pulls"] = [moved]

    lwd_run_relay(api, REPO, 11)

    assert not api.posts and not api.patches


def test_missing_pr_makes_relay_skip():
    api = _api_with_lanes(_fast_all_pass(), None)
    api.get_responses[f"/repos/{REPO}/commits/{SHA}/pulls"] = []

    lwd_run_relay(api, REPO, 11)

    assert not api.posts and not api.patches


def test_missing_slow_lane_renders_as_did_not_run():
    api = _api_with_lanes(_fast_all_pass(), None)

    lwd_run_relay(api, REPO, 11)

    body = api.posts[0][1]["body"]
    assert "Slow lane" in body
    assert "did not run" in body


def test_corrupt_artifact_degrades_to_did_not_run():
    api = _api_with_lanes(_fast_all_pass(), None)
    api.binaries["zip://fast"] = b"not a zip"

    lwd_run_relay(api, REPO, 11)

    body = api.posts[0][1]["body"]
    assert "did not run" in body


def test_no_test_label_marks_slow_lane_bypassed():
    api = _api_with_lanes(_fast_all_pass(), None, labels=("no-test",))

    lwd_run_relay(api, REPO, 11)

    body = api.posts[0][1]["body"]
    assert "no-test" in body


def test_results_roundtrip_through_payload_parser(tmp_path: Path):
    out = tmp_path / "r.json"
    lwd_write_results(
        out,
        lane="fast",
        pr_number=7,
        head_sha=SHA,
        results=[LwdCheckResult(check_id="commits", status="fail", summary="s")],
    )
    lane, results = lwd_parse_results_payload(
        json.loads(out.read_text()), source="r.json"
    )
    assert lane == "fast"
    assert results[0].check_id == "commits"
