# SPDX-License-Identifier: Apache-2.0
"""run / stop / logs / health: the operations around one launch plan."""

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Any

from . import LwdToolError
from .plan import LaunchPlan, format_summary, plan_logconf
from .procfs import ProcEntry, find_vllm_processes, scan_lwd_processes

TERM_GRACE_S = 5
HEALTH_INTERVAL_S = 5
RUNS_DIRNAME = "runs"


def preflight_ports(ports: Mapping[str, int]) -> None:
    """Refuse to start when a port we must bind is already taken."""
    occupied: list[str] = []
    for name, port in ports.items():
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("0.0.0.0", port))
        except OSError:
            occupied.append(f"{name} port {port}")
        finally:
            sock.close()
    if occupied:
        hint = ", ".join(occupied)
        raise LwdToolError(
            f"port already in use: {hint}; another deployment running here? "
            "use --port-base/--serve-port, or lwd-tool stop"
        )


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def runs_dir(launch_dir: str) -> Path:
    return Path(launch_dir) / RUNS_DIRNAME


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    role: str
    pid: int
    argv: tuple[str, ...]
    log_path: str
    started_at: str
    serve_port: int

    def to_json(self) -> str:
        return json.dumps(
            {
                "run_id": self.run_id,
                "role": self.role,
                "pid": self.pid,
                "argv": list(self.argv),
                "log_path": self.log_path,
                "started_at": self.started_at,
                "serve_port": self.serve_port,
            },
            indent=2,
        )


def save_run_record(record: RunRecord, launch_dir: str) -> Path:
    stamp = record.started_at.replace(":", "").replace("-", "")
    path = runs_dir(launch_dir) / f"{stamp}_{record.role}_{record.run_id[:6]}.json"
    _write_text(path, record.to_json() + "\n")
    return path


def load_run_records(launch_dir: str) -> list[tuple[Path, dict[str, Any]]]:
    directory = runs_dir(launch_dir)
    records: list[tuple[Path, dict[str, Any]]] = []
    if not directory.is_dir():
        return records
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and "run_id" in data:
            records.append((path, data))
    return records


def run_plan(
    plan: LaunchPlan,
    *,
    spawn: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> int:
    # plan.launch_dir is where topology + run records live; it comes from the
    # plan itself so callers cannot drift from what was printed.
    log_dir_path = Path(plan.log_path).parent
    log_dir_path.mkdir(parents=True, exist_ok=True)
    probe = log_dir_path / ".lwd-tool-write-probe"
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as exc:
        raise LwdToolError(
            f"log dir {log_dir_path} is not writable - is it mounted from the "
            f"host? ({exc})"
        ) from exc
    preflight_ports(plan.preflight_ports)
    summary = format_summary(plan)
    print(summary)
    if plan.topology_yaml is not None:
        _write_text(Path(plan.topology_path), plan.topology_yaml)
    _write_text(Path(plan.logconf_path), plan_logconf(plan, plan.log_level))
    with open(plan.log_path, "w", encoding="utf-8") as log_file:
        log_file.write(summary + "\n")
    process = spawn(plan.argv, env={**os.environ, **plan.env})
    record = RunRecord(
        run_id=plan.run_id,
        role=plan.role,
        pid=process.pid,
        argv=plan.argv,
        log_path=plan.log_path,
        started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        serve_port=plan.serve_port,
    )
    record_path = save_run_record(record, plan.launch_dir)
    print(f"[run] pid={process.pid} log={plan.log_path}")
    print(f"[run] record={record_path}")
    returncode = process.wait()
    print(f"[run] exited with code {returncode}; log: {plan.log_path}")
    return returncode


def _kill_entries(
    entries: tuple[ProcEntry, ...],
    killer: Callable[[int, int], None],
    sleeper: Callable[[float], None] = time.sleep,
    alive: Callable[[int], bool] = lambda pid: Path(f"/proc/{pid}").exists(),
) -> list[tuple[ProcEntry, str]]:
    """SIGTERM, wait, then SIGKILL the survivors; report per entry."""
    for entry in entries:
        try:
            killer(entry.pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + TERM_GRACE_S
    remaining = list(entries)
    while remaining and time.monotonic() < deadline:
        sleeper(0.2)
        remaining = [entry for entry in remaining if alive(entry.pid)]
    for entry in remaining:
        try:
            killer(entry.pid, signal.SIGKILL)
        except OSError:
            pass
    report = []
    for entry in entries:
        state = "killed" if entry in remaining else "terminated"
        report.append((entry, state))
    return report


def _print_entries(
    out: IO[str], entries: tuple[ProcEntry, ...], state: str = ""
) -> None:
    for entry in entries:
        cmdline = " ".join(entry.cmdline[:6])
        suffix = f" ({state})" if state else ""
        print(f"  pid {entry.pid}{suffix}: {cmdline}", file=out)


def _menu(
    groups: list[tuple[str, tuple[ProcEntry, ...]]],
    records: Mapping[str, dict[str, Any]],
    out: IO[str],
    prompt: Callable[[str], str],
) -> list[tuple[str, tuple[ProcEntry, ...]]]:
    print(f"this machine runs {len(groups)} lwd-tool deployment(s):", file=out)
    for index, (run_id, entries) in enumerate(groups, start=1):
        record = records.get(run_id, {})
        role = record.get("role", "?")
        started = record.get("started_at", "?")
        serve = record.get("serve_port", "?")
        log = record.get("log_path", "?")
        print(
            f"  [{index}] run_id={run_id[:12]} role={role} serve={serve} "
            f"started={started} processes={len(entries)}",
            file=out,
        )
        print(f"      log={log}", file=out)
        _print_entries(out, entries)
    answer = prompt(
        "select deployments to stop (comma-separated numbers, a=all, q=quit): "
    ).strip()
    if answer.lower() == "q":
        return []
    if answer.lower() == "a":
        return groups
    picked = []
    for item in answer.split(","):
        item = item.strip()
        if not item.isdigit() or not 1 <= int(item) <= len(groups):
            raise LwdToolError(f"invalid selection: {item!r}")
        picked.append(groups[int(item) - 1])
    return picked


def stop(
    *,
    run_id: str | None = None,
    any_vllm: bool = False,
    launch_dir: str,
    proc_root: str | Path = "/proc",
    out: IO[str] = sys.stdout,
    prompt: Callable[[str], str] = input,
    killer: Callable[[int, int], None] = os.kill,
) -> int:
    records = {data["run_id"]: data for _, data in load_run_records(launch_dir)}
    if any_vllm:
        entries = find_vllm_processes(proc_root)
        if not entries:
            print("no vllm-like processes found", file=out)
            return 0
        print(f"killing {len(entries)} vllm-like process(es):", file=out)
        _print_entries(out, entries)
        for entry, state in _kill_entries(entries, killer):
            print(f"  pid {entry.pid}: {state}", file=out)
        return 0
    groups = scan_lwd_processes(proc_root)
    if not groups:
        print(
            "no lwd-tool managed processes are running; "
            "for stray processes try: lwd-tool stop --any-vllm",
            file=out,
        )
        return 0
    ordered = sorted(groups.items())
    if run_id is not None:
        wanted = [group for group in ordered if group[0].startswith(run_id)]
        if not wanted:
            raise LwdToolError(f"no running deployment matches --run-id {run_id!r}")
    elif len(ordered) == 1:
        wanted = ordered
        print("one lwd-tool deployment is running:", file=out)
    else:
        wanted = _menu(ordered, records, out, prompt)
    stopped = 0
    for group_run_id, entries in wanted:
        record = records.get(group_run_id, {})
        print(
            f"stopping run_id={group_run_id[:12]} "
            f"role={record.get('role', '?')} ({len(entries)} process(es))",
            file=out,
        )
        for entry, state in _kill_entries(entries, killer):
            print(f"  pid {entry.pid}: {state}", file=out)
        stopped += 1
    print(f"stopped {stopped} deployment(s)", file=out)
    return 0


def latest_log(role: str | None, launch_dir: str) -> str:
    records = [data for _, data in load_run_records(launch_dir)]
    if role is not None:
        records = [data for data in records if data.get("role") == role]
    if not records:
        target = f" for role {role!r}" if role else ""
        raise LwdToolError(f"no run records{target}; nothing was launched here")
    newest = max(records, key=lambda data: str(data.get("started_at", "")))
    return str(newest["log_path"])


def logs(
    role: str | None,
    *,
    launch_dir: str,
    follow: bool = True,
    out: IO[str] = sys.stdout,
) -> int:
    log_path = latest_log(role, launch_dir)
    if not Path(log_path).is_file():
        raise LwdToolError(f"log file is gone: {log_path}")
    print(f"[logs] {log_path}", file=out)
    command = (
        ["tail", "-n", "100", "-f", log_path]
        if follow
        else ["tail", "-n", "100", log_path]
    )
    completed = subprocess.run(command, check=False)
    return completed.returncode


def _http(url: str, payload: dict[str, Any] | None = None) -> int:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except urllib.error.URLError as error:
        raise LwdToolError(f"request to {url} failed: {error.reason}") from error


def health(
    host: str,
    port: int,
    served_name: str,
    *,
    timeout_s: int = 600,
    out: IO[str] = sys.stdout,
) -> int:
    base = f"http://{host}:{port}"
    deadline = time.monotonic() + timeout_s
    while True:
        status = _http(f"{base}/health")
        if status == 200:
            break
        if time.monotonic() >= deadline:
            raise LwdToolError(f"/health still {status} after {timeout_s}s: {base}")
        time.sleep(HEALTH_INTERVAL_S)
    started = time.monotonic()
    payload = {
        "model": served_name,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "ping"}],
    }
    status = _http(f"{base}/v1/chat/completions", payload)
    if status != 200:
        raise LwdToolError(f"chat request failed with {status}: {base}")
    elapsed = (time.monotonic() - started) * 1000
    curl = (
        f"curl -s http://{host}:{port}/v1/chat/completions -H "
        f"'Content-Type: application/json' -d "
        f"'{json.dumps(payload, separators=(',', ':'))}'"
    )
    print(f"[health] {base} healthy, chat request ok in {elapsed:.0f} ms")
    print(f"[health] replay: {curl}")
    return 0
