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


def _pr(number: int = 7) -> dict:
    return {
        "number": number,
        "state": "open",
        "merged_at": None,
        "head": {"sha": SHA},
        "base": {"repo": {"full_name": REPO}},
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


def _api_with_fast_lane(
    fast_checks: list[LwdCheckResult] | None,
    *,
    existing_comment: dict | None = None,
) -> _FakeApi:
    api = _FakeApi()
    api.get_responses[f"/repos/{REPO}/actions/runs/11"] = {"head_sha": SHA}
    api.get_responses[f"/repos/{REPO}/commits/{SHA}/pulls"] = [_pr()]
    runs_path = (
        f"/repos/{REPO}/actions/workflows/lwd_fast.yml/runs"
        f"?head_sha={SHA}&event=pull_request&per_page=1"
    )
    if fast_checks is None:
        api.get_responses[runs_path] = {"workflow_runs": []}
    else:
        api.get_responses[runs_path] = {"workflow_runs": [{"id": 21}]}
        api.get_responses[f"/repos/{REPO}/actions/runs/21/artifacts?per_page=100"] = {
            "artifacts": [
                {"name": "lwd-results-fast-job", "archive_download_url": "zip://fast"}
            ]
        }
        api.binaries["zip://fast"] = _artifact_zip(fast_checks, "fast")
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


def test_relay_posts_rendered_comment():
    api = _api_with_fast_lane(_fast_all_pass())

    lwd_run_relay(api, REPO, 11)

    assert len(api.posts) == 1
    path, payload = api.posts[0]
    assert path == f"/repos/{REPO}/issues/7/comments"
    assert payload["body"].startswith(LWD_COMMENT_MARKER)
    assert "`vllm/x.py:3` ping `@`room" in payload["body"]


def test_relay_updates_existing_marker_comment_in_place():
    existing = {"id": 55, "body": LWD_COMMENT_MARKER + "\nold"}
    api = _api_with_fast_lane(_fast_all_pass(), existing_comment=existing)

    lwd_run_relay(api, REPO, 11)

    assert not api.posts
    assert len(api.patches) == 1
    assert api.patches[0][0] == f"/repos/{REPO}/issues/comments/55"


def test_stale_pr_head_makes_relay_skip():
    api = _api_with_fast_lane(_fast_all_pass())
    moved = _pr()
    moved["head"]["sha"] = "f" * 40
    api.get_responses[f"/repos/{REPO}/commits/{SHA}/pulls"] = [moved]

    lwd_run_relay(api, REPO, 11)

    assert not api.posts and not api.patches


def test_missing_pr_makes_relay_skip():
    api = _api_with_fast_lane(_fast_all_pass())
    api.get_responses[f"/repos/{REPO}/commits/{SHA}/pulls"] = []

    lwd_run_relay(api, REPO, 11)

    assert not api.posts and not api.patches


def test_corrupt_artifact_degrades_to_did_not_run():
    api = _api_with_fast_lane(_fast_all_pass())
    api.binaries["zip://fast"] = b"not a zip"

    lwd_run_relay(api, REPO, 11)

    body = api.posts[0][1]["body"]
    assert "did not run" in body


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
