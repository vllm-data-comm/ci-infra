from __future__ import annotations

import io
import json
import threading
import zipfile
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from lwd_checks.comment import LwdUrllibApi, lwd_run_relay
from lwd_checks.render import LWD_COMMENT_MARKER
from lwd_checks.results import (
    LwdCheckResult,
    LwdFinding,
    lwd_parse_results_payload,
    lwd_write_results,
)

REPO = "vllm-data-comm/vllm"
SHA = "0123abcdef0123abcdef0123abcdef0123abcd"
HEAD_OWNER = "vllm-data-comm"
HEAD_BRANCH = "feat/x"


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


def _pulls_path(owner: str, branch: str) -> str:
    head = f"{owner}%3A{branch.replace('/', '%2F')}"
    return f"/repos/{REPO}/pulls?state=open&head={head}&per_page=100"


def _api_with_fast_lane(
    fast_checks: list[LwdCheckResult] | None,
    *,
    existing_comment: dict | None = None,
    head_owner: str = HEAD_OWNER,
) -> _FakeApi:
    api = _FakeApi()
    api.get_responses[f"/repos/{REPO}/actions/runs/11"] = {
        "head_sha": SHA,
        "head_branch": HEAD_BRANCH,
        "head_repository": {"owner": {"login": head_owner}},
    }
    api.get_responses[_pulls_path(head_owner, HEAD_BRANCH)] = [_pr()]
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


def test_relay_finds_a_pr_opened_from_a_fork():
    """Regression: fork PRs were looked up by commit SHA, which only knows
    commits of the base repository, so they never got a comment."""
    api = _api_with_fast_lane(_fast_all_pass(), head_owner="contributor")

    lwd_run_relay(api, REPO, 11)

    assert [path for path, _payload in api.posts] == [
        f"/repos/{REPO}/issues/7/comments"
    ]


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
    api.get_responses[_pulls_path(HEAD_OWNER, HEAD_BRANCH)] = [moved]

    lwd_run_relay(api, REPO, 11)

    assert not api.posts and not api.patches


def test_missing_pr_makes_relay_skip():
    api = _api_with_fast_lane(_fast_all_pass())
    api.get_responses[_pulls_path(HEAD_OWNER, HEAD_BRANCH)] = []

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


class _ArtifactServer(BaseHTTPRequestHandler):
    """Mimics the API redirecting an artifact download to blob storage,
    which rejects any Authorization header."""

    def do_GET(self) -> None:
        if self.path == "/artifact":
            self.send_response(302)
            self.send_header("Location", "/blob")
            self.end_headers()
        elif self.headers.get("Authorization") is not None:
            self.send_response(401)
            self.end_headers()
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"zip-bytes")

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def artifact_server_url() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ArtifactServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_artifact_download_drops_the_token_on_redirect(artifact_server_url: str):
    """Regression: the token was forwarded to blob storage, which answered
    401, so the relay crashed on every run."""
    api = LwdUrllibApi("secret-token")

    assert api.get_binary(f"{artifact_server_url}/artifact") == b"zip-bytes"
