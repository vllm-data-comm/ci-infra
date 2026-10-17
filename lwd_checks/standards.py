"""Advisory standards rules: parameter count, function length, class prefix.

Each rule only flags problems the PR introduces itself: a function that
already exceeded the limit before the PR is not flagged, because most
upstream code would otherwise be flagged by every PR. "Before" means the
version at the parent of the PR's oldest own commit touching the file
(via the old path for renames).
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from lwd_checks.ast_diff import LwdFunctionInfo, lwd_parse_classes, lwd_parse_functions
from lwd_checks.results import LwdCheckResult, LwdFinding, lwd_write_results
from lwd_checks.scope import LwdOwnFile, LwdScope, lwd_scope_from_json

_LWD_MAX_PARAMS = 5
_LWD_MAX_FUNCTION_LINES = 100
_LWD_CLASS_PREFIXES = ("Lwd", "_Lwd")


def _lwd_git(repo_dir: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def _lwd_base_source(
    repo_dir: Path, scope: LwdScope, own_file: LwdOwnFile, upstream_ref: str
) -> str | None:
    """The file's content before the PR's own changes, or None if new."""
    filter_path = own_file.old_path or own_file.path
    shas = _lwd_git(
        repo_dir,
        "rev-list",
        "--reverse",
        f"{scope.base_sha}..{scope.head_sha}",
        "--not",
        upstream_ref,
        "--",
        filter_path,
    ).split()
    if not shas:
        return None
    completed = subprocess.run(
        ["git", "-C", str(repo_dir), "show", f"{shas[0]}^:{filter_path}"],
        capture_output=True,
        text=True,
    )
    return completed.stdout if completed.returncode == 0 else None


def _lwd_own_python_files(scope: LwdScope, source_root: str) -> list[LwdOwnFile]:
    return [
        f
        for f in scope.own_files
        if f.status in ("added", "modified", "renamed")
        and f.path.startswith(source_root)
        and f.path.endswith(".py")
        and not f.path.startswith("tests/")
    ]


def lwd_run_standards(
    repo_dir: Path | str,
    scope: LwdScope,
    source_root: str,
    upstream_ref: str,
) -> list[LwdCheckResult]:
    repo_dir = Path(repo_dir)
    param_findings: list[LwdFinding] = []
    length_findings: list[LwdFinding] = []
    class_findings: list[LwdFinding] = []
    for own_file in _lwd_own_python_files(scope, source_root):
        head_source = (repo_dir / own_file.path).read_text(encoding="utf-8")
        try:
            head_functions = lwd_parse_functions(head_source)
            head_classes = lwd_parse_classes(head_source)
        except SyntaxError:
            continue  # pre-commit owns syntax errors
        base_source = _lwd_base_source(repo_dir, scope, own_file, upstream_ref)
        if base_source is None:
            base_functions: dict[str, LwdFunctionInfo] = {}
            base_classes: set[str] = set()
        else:
            try:
                base_functions = lwd_parse_functions(base_source)
                base_classes = lwd_parse_classes(base_source)
            except SyntaxError:
                base_functions, base_classes = {}, set()

        for qualname, info in head_functions.items():
            base_info = base_functions.get(qualname)
            if info.params > _LWD_MAX_PARAMS and (
                base_info is None or base_info.params <= _LWD_MAX_PARAMS
            ):
                param_findings.append(
                    LwdFinding(
                        path=own_file.path,
                        line=info.start_line,
                        message=f"`{qualname}` has {info.params} parameters",
                    )
                )
            if info.length > _LWD_MAX_FUNCTION_LINES and (
                base_info is None or base_info.length <= _LWD_MAX_FUNCTION_LINES
            ):
                length_findings.append(
                    LwdFinding(
                        path=own_file.path,
                        line=info.start_line,
                        message=f"`{qualname}` is {info.length} lines long",
                    )
                )
        for qualname in head_classes:
            if qualname not in base_classes and not qualname.rsplit(".", 1)[
                -1
            ].startswith(_LWD_CLASS_PREFIXES):
                class_findings.append(
                    LwdFinding(
                        path=own_file.path,
                        line=None,
                        message=f"class `{qualname}` is not prefixed Lwd",
                    )
                )

    return [
        LwdCheckResult(
            check_id="param-count",
            status="fail" if param_findings else "pass",
            summary=(
                f"{len(param_findings)} function(s) over {_LWD_MAX_PARAMS} parameters"
                if param_findings
                else ""
            ),
            findings=tuple(param_findings),
        ),
        LwdCheckResult(
            check_id="function-length",
            status="fail" if length_findings else "pass",
            summary=(
                f"{len(length_findings)} function(s) over "
                f"{_LWD_MAX_FUNCTION_LINES} lines"
                if length_findings
                else ""
            ),
            findings=tuple(length_findings),
        ),
        LwdCheckResult(
            check_id="class-prefix",
            status="fail" if class_findings else "pass",
            summary=(
                f"{len(class_findings)} new class(es) not prefixed Lwd"
                if class_findings
                else ""
            ),
            findings=tuple(class_findings),
        ),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(prog="lwd_checks.standards")
    parser.add_argument("--repo-dir", default=".")
    parser.add_argument("--scope", required=True)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    payload = json.loads(Path(args.scope).read_text())
    scope = lwd_scope_from_json(payload)
    results = lwd_run_standards(args.repo_dir, scope, args.source_root, args.upstream)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for result in results:
        lwd_write_results(
            out_dir / f"{result.check_id}.json",
            lane="fast",
            pr_number=args.pr,
            head_sha=scope.head_sha,
            results=[result],
        )


if __name__ == "__main__":
    main()
