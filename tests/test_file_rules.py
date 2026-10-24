from __future__ import annotations

from pathlib import Path

from gitrepo import lwd_clone_fork, lwd_commit, lwd_git, lwd_init

from lwd_checks.file_rules import lwd_run_file_rules
from lwd_checks.scope import lwd_compute_scope


def _repo(tmp_path: Path, base_files: dict[str, str] | None = None) -> Path:
    upstream = tmp_path / "upstream"
    fork = tmp_path / "fork"
    lwd_init(upstream)
    lwd_commit(upstream, "chore: base", {"a.txt": "a"} | (base_files or {}))
    lwd_clone_fork(upstream, fork)
    return fork


def _run(fork: Path, source_root: str = "vllm/"):
    base = lwd_git(fork, "rev-parse", "upstream/main").strip()
    head = lwd_git(fork, "rev-parse", "HEAD").strip()
    scope = lwd_compute_scope(fork, base, head, "upstream/main")
    results = lwd_run_file_rules(fork, scope, source_root, "upstream/main")
    return {r.check_id: r for r in results}


def test_new_files_inside_lwd_paths_pass(tmp_path: Path):
    fork = _repo(tmp_path)
    lwd_commit(
        fork,
        "feat(lwd): x",
        {
            "vllm/v1/lwd_control/x.py": "A = 1\n",
            "tools/gen_lwd_topology.py": "A = 1\n",
            "tests/config/lwd/conftest.py": "A = 1\n",
            "vllm/foo.py": "A = 1\n",
        },
    )

    results = _run(fork)

    assert [f.path for f in results["new-file-path"].findings] == ["vllm/foo.py"]


def test_renamed_out_of_lwd_path_is_flagged(tmp_path: Path):
    fork = _repo(tmp_path)
    lwd_commit(fork, "feat(lwd): add", {"vllm/v1/lwd_control/a.py": "x = 1\n" * 20})
    (fork / "vllm" / "plain").mkdir()
    lwd_git(fork, "mv", "vllm/v1/lwd_control/a.py", "vllm/plain/b.py")
    lwd_git(fork, "commit", "-q", "-m", "refactor(lwd): move out")

    results = _run(fork)

    assert [f.path for f in results["new-file-path"].findings] == ["vllm/plain/b.py"]


def test_patch_dir_changes_are_flagged(tmp_path: Path):
    fork = _repo(tmp_path)
    lwd_commit(fork, "feat(lwd): x", {"vllm_ascend/patch/foo.py": "A = 1\n"})

    results = _run(fork, source_root="vllm_ascend/")

    findings = results["patch-usage"].findings
    assert len(findings) == 1
    assert "vllm_ascend/patch/foo.py" in findings[0].message


def test_monkey_patch_assignment_on_added_lines_is_flagged(tmp_path: Path):
    fork = _repo(tmp_path)
    lwd_commit(
        fork,
        "feat(lwd): x",
        {
            "vllm/mod.py": (
                "import torch\n"
                "import vllm.config\n"
                "torch.npu.is_available = lambda: True\n"
                "setattr(vllm.config, 'LWD', 1)\n"
            )
        },
    )

    results = _run(fork)

    messages = " ".join(f.message for f in results["patch-usage"].findings)
    assert "torch.npu.is_available" in messages
    assert "vllm.config" in messages


def test_mock_patch_usage_is_flagged(tmp_path: Path):
    fork = _repo(tmp_path)
    lwd_commit(
        fork,
        "feat(lwd): x",
        {
            "vllm/mod.py": (
                "from unittest import mock\n"
                "@mock.patch('vllm.X')\n"
                "def f(x):\n    pass\n"
            )
        },
    )

    results = _run(fork)

    assert results["patch-usage"].status == "fail"


def test_local_attribute_assignment_is_not_flagged(tmp_path: Path):
    fork = _repo(tmp_path)
    lwd_commit(
        fork,
        "feat(lwd): x",
        {"vllm/mod.py": ("class C:\n    pass\nc = C()\nc.attr = 1\n")},
    )

    results = _run(fork)

    assert results["patch-usage"].status == "pass"


def test_pre_existing_patch_line_not_touched_by_pr_is_not_flagged(tmp_path: Path):
    fork = _repo(
        tmp_path,
        base_files={
            "vllm/mod.py": (
                "import torch\ntorch.npu.is_available = lambda: True\nother = 2\n"
            )
        },
    )
    lwd_commit(
        fork,
        "fix(lwd): unrelated",
        {
            "vllm/mod.py": (
                "import torch\ntorch.npu.is_available = lambda: True\nother = 3\n"
            )
        },
    )

    results = _run(fork)

    assert results["patch-usage"].status == "pass"
