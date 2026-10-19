"""Relay lane results into the PR checklist comment.

Runs in the base repository on `workflow_run.completed`, with a token
that can write PR comments - fork pull requests only get a read-only
token in their own runs. Security rules (docs/design/lwd_ci.md):

- PR code is never checked out or executed here.
- The PR number is bound to the trusted `workflow_run.head_sha`: the
  comment is only posted when the PR's current head matches.
- Artifact payloads are parsed as data and validated against the
  results schema; a corrupt artifact degrades to "did not run".
"""

from __future__ import annotations

import io
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from typing import Protocol

from lwd_checks.catalog import LWD_LANE_FAST, LWD_LANES
from lwd_checks.render import LWD_COMMENT_MARKER, lwd_render_checklist
from lwd_checks.results import LwdCheckResult, lwd_parse_results_payload

LWD_LANE_WORKFLOW_FILES = {LWD_LANE_FAST: "lwd_fast.yml"}
_LWD_ARTIFACT_PREFIX = "lwd-results-"
_LWD_REDIRECT_CODES = (301, 302, 303, 307, 308)


class _LwdNoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LwdGithubApi(Protocol):
    def get(self, path: str) -> object: ...
    def get_binary(self, url: str) -> bytes: ...
    def post(self, path: str, payload: dict) -> object: ...
    def patch(self, path: str, payload: dict) -> object: ...


class LwdUrllibApi:
    """Real GitHub API client over urllib, stdlib only."""

    def __init__(self, token: str) -> None:
        self._token = token
        self._api = os.environ.get("GITHUB_API_URL", "https://api.github.com")

    def _json_request(self, path: str, method: str, payload: dict | None = None):
        url = self._api + path
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
        }
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode()
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())

    def get(self, path: str) -> object:
        return self._json_request(path, "GET")

    def get_binary(self, url: str) -> bytes:
        # The token only goes to the API. Artifact downloads redirect to
        # signed blob storage, which rejects a forwarded Authorization header
        # with 401 (urllib keeps headers across redirects).
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
            },
        )
        opener = urllib.request.build_opener(_LwdNoRedirect)
        try:
            with opener.open(request, timeout=30) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code not in _LWD_REDIRECT_CODES:
                raise
            location = urllib.parse.urljoin(url, error.headers["Location"])
        with urllib.request.urlopen(location, timeout=30) as response:
            return response.read()

    def post(self, path: str, payload: dict) -> object:
        return self._json_request(path, "POST", payload)

    def patch(self, path: str, payload: dict) -> object:
        return self._json_request(path, "PATCH", payload)


def _lwd_collect_lane(
    api: LwdGithubApi, repo: str, workflow_file: str, head_sha: str
) -> list[LwdCheckResult] | None:
    """Latest results of one lane for this head SHA, or None if absent."""
    runs = api.get(
        f"/repos/{repo}/actions/workflows/{workflow_file}/runs"
        f"?head_sha={head_sha}&event=pull_request&per_page=1"
    )
    workflow_runs = runs.get("workflow_runs", []) if isinstance(runs, dict) else []
    if not workflow_runs:
        return None
    run_id = workflow_runs[0]["id"]
    artifacts = api.get(f"/repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100")
    results: list[LwdCheckResult] = []
    for artifact in artifacts.get("artifacts", []):
        if not artifact["name"].startswith(_LWD_ARTIFACT_PREFIX):
            continue
        archive = api.get_binary(artifact["archive_download_url"])
        try:
            with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
                for name in zipped.namelist():
                    if name.endswith(".json"):
                        payload = json.loads(zipped.read(name))
                        _lane, loaded = lwd_parse_results_payload(payload, source=name)
                        results.extend(loaded)
        except (ValueError, KeyError, zipfile.BadZipFile, json.JSONDecodeError):
            # Corrupt artifact: degrade this lane to "did not run".
            return None
    return results


def _lwd_find_open_pull(api: LwdGithubApi, repo: str, run: dict) -> dict | None:
    """The open PR into this repository whose head is the run's head branch.

    `/commits/{sha}/pulls` only knows commits of this repository, so it
    returns nothing for fork PRs; the head branch filter covers both.
    """
    head = urllib.parse.quote(
        f"{run['head_repository']['owner']['login']}:{run['head_branch']}", safe=""
    )
    pulls = api.get(f"/repos/{repo}/pulls?state=open&head={head}&per_page=100")
    return next(
        (p for p in pulls if p["base"]["repo"]["full_name"] == repo),
        None,
    )


def lwd_run_relay(api: LwdGithubApi, repo: str, run_id: int) -> None:
    run = api.get(f"/repos/{repo}/actions/runs/{run_id}")
    head_sha = run["head_sha"]

    pull = _lwd_find_open_pull(api, repo, run)
    if pull is None or pull["head"]["sha"] != head_sha:
        return  # stale run: the PR moved on

    results_by_lane = {
        lane: _lwd_collect_lane(api, repo, LWD_LANE_WORKFLOW_FILES[lane], head_sha)
        for lane in LWD_LANES
    }
    text = lwd_render_checklist(results_by_lane, head_sha)

    number = pull["number"]
    comments = api.get(f"/repos/{repo}/issues/{number}/comments?per_page=100")
    existing = next(
        (c for c in comments if str(c.get("body", "")).startswith(LWD_COMMENT_MARKER)),
        None,
    )
    if existing is not None:
        api.patch(f"/repos/{repo}/issues/comments/{existing['id']}", {"body": text})
    else:
        api.post(f"/repos/{repo}/issues/{number}/comments", {"body": text})


def main() -> None:
    repo = os.environ["GITHUB_REPOSITORY"]
    run_id = int(os.environ["LWD_WORKFLOW_RUN_ID"])
    token = os.environ["LWD_GITHUB_TOKEN"]
    lwd_run_relay(LwdUrllibApi(token), repo, run_id)


if __name__ == "__main__":
    main()
