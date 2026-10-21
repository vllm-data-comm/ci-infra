# SPDX-License-Identifier: Apache-2.0
"""Scan /proc for processes tagged with a lwd-tool run id."""

from dataclasses import dataclass
from pathlib import Path

RUN_ID_ENV = "LWD_TOOL_RUN_ID"


@dataclass(frozen=True)
class ProcEntry:
    pid: int
    run_id: str
    cmdline: tuple[str, ...]


def _read_file(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:  # gone, or not ours to read
        return None


def _entry(pid_dir: Path) -> ProcEntry | None:
    environ = _read_file(pid_dir / "environ")
    cmdline = _read_file(pid_dir / "cmdline")
    if environ is None or cmdline is None:
        return None
    run_id = None
    for item in environ.split(b"\0"):
        name, sep, value = item.partition(b"=")
        if sep and name.decode("utf-8", "replace") == RUN_ID_ENV:
            run_id = value.decode("utf-8", "replace")
            break
    if run_id is None:
        return None
    argv = tuple(
        item.decode("utf-8", "replace") for item in cmdline.split(b"\0") if item
    )
    return ProcEntry(int(pid_dir.name), run_id, argv)


def scan_lwd_processes(
    proc_root: str | Path = "/proc",
) -> dict[str, tuple[ProcEntry, ...]]:
    """Group tagged processes by run id, ordered by pid."""
    root = Path(proc_root)
    groups: dict[str, list[ProcEntry]] = {}
    for pid_dir in (
        sorted(root.iterdir(), key=lambda p: p.name) if root.is_dir() else []
    ):
        if not pid_dir.name.isdigit():
            continue
        entry = _entry(pid_dir)
        if entry is not None:
            groups.setdefault(entry.run_id, []).append(entry)
    return {run_id: tuple(entries) for run_id, entries in groups.items()}


def find_vllm_processes(proc_root: str | Path = "/proc") -> tuple[ProcEntry, ...]:
    """Every process that looks like vllm, for the --any-vllm hammer."""
    root = Path(proc_root)
    found: list[ProcEntry] = []
    if not root.is_dir():
        return ()
    for pid_dir in root.iterdir():
        if not pid_dir.name.isdigit():
            continue
        cmdline = _read_file(pid_dir / "cmdline")
        if cmdline is None:
            continue
        argv = tuple(
            item.decode("utf-8", "replace") for item in cmdline.split(b"\0") if item
        )
        if any("vllm" in arg for arg in argv):
            found.append(ProcEntry(int(pid_dir.name), "", argv))
    return tuple(sorted(found, key=lambda entry: entry.pid))
