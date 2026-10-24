from __future__ import annotations

from pathlib import Path

from gitrepo import lwd_clone_fork, lwd_commit, lwd_git, lwd_init

from lwd_checks.scope import lwd_compute_scope
from lwd_checks.standards import lwd_run_standards

_SIX_PARAMS = "def build(a, b, c, d, e, f):\n    return a\n"
_SEVEN_PARAMS = "def build(a, b, c, d, e, f, g):\n    return a\n"


def _body(lines: int) -> str:
    return "def f():\n" + "".join("    pass\n" for _ in range(lines))


def _repo(tmp_path: Path, base_files: dict[str, str] | None = None) -> Path:
    upstream = tmp_path / "upstream"
    fork = tmp_path / "fork"
    lwd_init(upstream)
    files = {"a.txt": "a"} | (base_files or {})
    lwd_commit(upstream, "chore: base", files)
    lwd_clone_fork(upstream, fork)
    return fork


def _run(fork: Path, source_root: str = "vllm/"):
    base = lwd_git(fork, "rev-parse", "upstream/main").strip()
    head = lwd_git(fork, "rev-parse", "HEAD").strip()
    scope = lwd_compute_scope(fork, base, head, "upstream/main")
    results = lwd_run_standards(fork, scope, source_root, "upstream/main")
    return {r.check_id: r for r in results}


def test_new_function_with_six_params_is_flagged(tmp_path: Path):
    fork = _repo(tmp_path)
    lwd_commit(fork, "feat(lwd): x", {"vllm/mod.py": _SIX_PARAMS})

    results = _run(fork)

    assert results["param-count"].status == "fail"
    assert results["param-count"].findings[0].line == 1


def test_param_count_ignores_function_already_over_limit_at_base(tmp_path: Path):
    fork = _repo(tmp_path, base_files={"vllm/mod.py": _SIX_PARAMS})
    lwd_commit(fork, "fix(lwd): one more", {"vllm/mod.py": _SEVEN_PARAMS})

    results = _run(fork)

    assert results["param-count"].status == "pass"


def test_param_count_flags_function_newly_over_limit(tmp_path: Path):
    fork = _repo(
        tmp_path, base_files={"vllm/mod.py": "def build(a, b, c, d):\n    return a\n"}
    )
    lwd_commit(fork, "fix(lwd): grow", {"vllm/mod.py": _SIX_PARAMS})

    results = _run(fork)

    assert results["param-count"].status == "fail"


def test_function_length_flags_only_newly_over(tmp_path: Path):
    fork = _repo(tmp_path)
    lwd_commit(fork, "feat(lwd): x", {"vllm/mod.py": _body(121)})
    assert _run(fork)["function-length"].status == "fail"

    fork2 = _repo(tmp_path / "second", base_files={"vllm/mod.py": _body(120)})
    lwd_commit(fork2, "fix(lwd): longer", {"vllm/mod.py": _body(121)})
    assert _run(fork2)["function-length"].status == "pass"


def test_class_prefix_requires_lwd_prefix(tmp_path: Path):
    fork = _repo(tmp_path)
    lwd_commit(
        fork,
        "feat(lwd): x",
        {
            "vllm/mod.py": (
                "class Dispatcher:\n    pass\n\nclass LwdLink:\n    pass\n"
                "class _LwdPeer:\n    pass\n"
            )
        },
    )

    results = _run(fork)

    findings = results["class-prefix"].findings
    assert [f.message for f in findings] == ["class `Dispatcher` is not prefixed Lwd"]


def test_tests_and_other_roots_are_ignored(tmp_path: Path):
    fork = _repo(tmp_path)
    lwd_commit(
        fork,
        "feat(lwd): x",
        {"tests/test_mod.py": _SIX_PARAMS, "tools/gen.py": _SIX_PARAMS},
    )

    results = _run(fork)

    assert results["param-count"].status == "pass"


def test_renamed_file_compares_against_old_path_base(tmp_path: Path):
    # A filler body keeps the similarity above git's rename threshold,
    # which a two-line file with one changed line would not meet.
    filler = "".join(f"VALUE_{i} = {i}\n" for i in range(10))
    fork = _repo(
        tmp_path,
        base_files={"vllm/x.py": "def build(a, b):\n    return a\n" + filler},
    )
    lwd_git(fork, "mv", "vllm/x.py", "vllm/y.py")
    (fork / "vllm" / "y.py").write_text(_SIX_PARAMS + filler)
    lwd_git(fork, "add", "-A")
    lwd_git(fork, "commit", "-q", "-m", "refactor(lwd): rename and grow")

    results = _run(fork)

    assert results["param-count"].status == "fail"
    finding = results["param-count"].findings[0]
    assert (finding.path, finding.line) == ("vllm/y.py", 1)


def test_renamed_function_already_over_limit_at_base_stays_silent(tmp_path: Path):
    filler = "".join(f"VALUE_{i} = {i}\n" for i in range(10))
    fork = _repo(tmp_path, base_files={"vllm/x.py": _SIX_PARAMS + filler})
    lwd_git(fork, "mv", "vllm/x.py", "vllm/y.py")
    (fork / "vllm" / "y.py").write_text(_SEVEN_PARAMS + filler)
    lwd_git(fork, "add", "-A")
    lwd_git(fork, "commit", "-q", "-m", "refactor(lwd): rename and grow")

    results = _run(fork)

    assert results["param-count"].status == "pass"
