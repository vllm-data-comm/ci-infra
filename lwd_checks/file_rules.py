"""File-level advisory rules: new file paths and patch usage.

new-file-path: an added or renamed-into file must live under a path with
at least one `lwd` component (directory or file name).

patch-usage: flags changes under `vllm_ascend/patch/`, and inside the
source roots new occurrences of monkey patching — assignment to an
attribute chain rooted at an imported name (`module.attr = ...`,
`SomeClass.method = ...`), `setattr(<imported name>, ...)`, or use of
`mock.patch` / `patch.object` / `unittest.mock`. Only lines the PR's own
commits add are considered.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from lwd_checks.results import LwdCheckResult, LwdFinding, lwd_write_results
from lwd_checks.scope import LwdOwnFile, LwdScope, lwd_scope_from_json

_LWD_PATCH_DIR = "vllm_ascend/patch/"
_LWD_MOCK_PATCH_RE = re.compile(r"mock\.patch|patch\.object|unittest\.mock")
_LWD_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _lwd_git_lines(repo_dir: Path, *args: str) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.splitlines()


def _lwd_added_lines(
    repo_dir: Path, scope: LwdScope, own_file: LwdOwnFile, upstream_ref: str
) -> set[int]:
    """Line numbers (new file) that the PR's own commits added."""
    shas = _lwd_git_lines(
        repo_dir,
        "rev-list",
        "--reverse",
        f"{scope.base_sha}..{scope.head_sha}",
        "--not",
        upstream_ref,
        "--",
        own_file.path,
    )
    added: set[int] = set()
    for sha in shas:
        lines = _lwd_git_lines(
            repo_dir, "show", "--unified=0", "--format=", sha, "--", own_file.path
        )
        new_line = 0
        for line in lines:
            hunk = _LWD_HUNK_RE.match(line)
            if hunk:
                new_line = int(hunk.group(1))
            elif line.startswith("+") and not line.startswith("+++"):
                added.add(new_line)
                new_line += 1
            elif line.startswith("-"):
                pass
            else:
                new_line += 1
    return added


@dataclass(frozen=True)
class LwdPatchDetector:
    imported_names: set[str]

    def visit(self, tree: ast.AST) -> list[tuple[int, str]]:
        findings: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                for target in targets:
                    chain = _lwd_attribute_chain(target)
                    if (
                        len(chain) >= 2
                        and chain[0] in self.imported_names
                        and node.lineno
                    ):
                        findings.append((node.lineno, ".".join(chain)))
            elif isinstance(node, ast.Call):
                func_chain = _lwd_attribute_chain(node.func)
                if func_chain == ["setattr"] and node.args:
                    arg_chain = _lwd_attribute_chain(node.args[0])
                    if arg_chain and arg_chain[0] in self.imported_names:
                        dotted = ".".join(arg_chain)
                        findings.append((node.lineno, f"setattr({dotted}, ...)"))
                dotted_func = ".".join(func_chain)
                if _LWD_MOCK_PATCH_RE.search(dotted_func):
                    findings.append((node.lineno, dotted_func))
        return findings


def _lwd_attribute_chain(node: ast.AST) -> list[str]:
    """`torch.npu.is_available` -> ['torch', 'npu', 'is_available']."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return list(reversed(parts))
    return []


def _lwd_imported_names(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def lwd_run_file_rules(
    repo_dir: Path | str,
    scope: LwdScope,
    source_root: str,
    upstream_ref: str,
) -> list[LwdCheckResult]:
    repo_dir = Path(repo_dir)

    path_findings = [
        LwdFinding(
            path=f.path,
            line=None,
            message=f"new file is not under an lwd path: {f.path}",
        )
        for f in scope.own_files
        if f.status in ("added", "renamed")
        and not any("lwd" in part.lower() for part in Path(f.path).parts)
    ]

    patch_findings: list[LwdFinding] = []
    for own_file in scope.own_files:
        if own_file.status not in ("added", "modified", "renamed"):
            continue
        if own_file.path.startswith(_LWD_PATCH_DIR):
            patch_findings.append(
                LwdFinding(
                    path=own_file.path,
                    line=None,
                    message=f"changes a patch module: {own_file.path}",
                )
            )
            continue
        if not own_file.path.startswith(source_root) or not own_file.path.endswith(
            ".py"
        ):
            continue
        source = (repo_dir / own_file.path).read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
            imported = _lwd_imported_names(source)
        except SyntaxError:
            continue  # pre-commit owns syntax errors
        added = _lwd_added_lines(repo_dir, scope, own_file, upstream_ref)
        detector = LwdPatchDetector(imported_names=imported)
        for line, what in detector.visit(tree):
            if line in added:
                patch_findings.append(
                    LwdFinding(
                        path=own_file.path,
                        line=line,
                        message=f"monkey patch on an added line: {what}",
                    )
                )

    return [
        LwdCheckResult(
            check_id="new-file-path",
            status="fail" if path_findings else "pass",
            summary=(
                f"{len(path_findings)} new file(s) outside lwd paths"
                if path_findings
                else ""
            ),
            findings=tuple(path_findings),
        ),
        LwdCheckResult(
            check_id="patch-usage",
            status="fail" if patch_findings else "pass",
            summary=(
                f"{len(patch_findings)} patch usage(s) introduced"
                if patch_findings
                else ""
            ),
            findings=tuple(patch_findings),
        ),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(prog="lwd_checks.file_rules")
    parser.add_argument("--repo-dir", default=".")
    parser.add_argument("--scope", required=True)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    payload = json.loads(Path(args.scope).read_text())
    scope = lwd_scope_from_json(payload)
    results = lwd_run_file_rules(args.repo_dir, scope, args.source_root, args.upstream)
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
