# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import shutil
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from scenario import make_plan

from lwd_tool import LwdToolError
from lwd_tool.logconf import build_logging_config
from lwd_tool.netprobe import parse_ifconfig_output, parse_ip_addr_output
from lwd_tool.plan import format_summary
from lwd_tool.procfs import find_vllm_processes, scan_lwd_processes
from lwd_tool.runbook import (
    RunRecord,
    health,
    latest_log,
    load_run_records,
    preflight_ports,
    run_plan,
    save_run_record,
    stop,
)


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_preflight_rejects_an_occupied_port():
    port = free_port()
    holder = socket.socket()
    holder.bind(("0.0.0.0", port))
    holder.listen(1)
    try:
        with pytest.raises(LwdToolError, match=str(port)):
            preflight_ports({"serve": port})
    finally:
        holder.close()


def test_preflight_passes_when_free():
    preflight_ports({"serve": free_port()})


def test_logconf_has_only_the_file_handler():
    config = build_logging_config("/tmp/x.log", "DEBUG")
    assert set(config["handlers"]) == {"lwd_file"}
    assert config["handlers"]["lwd_file"]["level"] == "DEBUG"
    assert config["loggers"]["vllm"]["handlers"] == ["lwd_file"]
    assert config["loggers"]["vllm"]["propagate"] is False
    assert "StreamHandler" not in json.dumps(config)


def test_netprobe_parsers():
    ip_out = (
        "1: lo    inet 127.0.0.1/8 scope host lo\n"
        "2: enp189s0f0    inet 76.76.26.187/24 brd 76.76.26.255 scope global\n"
    )
    assert parse_ip_addr_output(ip_out) == {
        "127.0.0.1": "lo",
        "76.76.26.187": "enp189s0f0",
    }
    if_out = (
        "enp189s0f0: flags=4163<UP,BROADCAST,RUNNING,MULTICAST>  mtu 1500\n"
        "        inet 76.76.26.187  netmask 255.255.255.0  broadcast 76.76.26.255\n"
        "lo: flags=73<UP,LOOPBACK,RUNNING>  mtu 65536\n"
        "        inet 127.0.0.1  netmask 255.0.0.0\n"
    )
    assert parse_ifconfig_output(if_out) == {
        "76.76.26.187": "enp189s0f0",
        "127.0.0.1": "lo",
    }


def make_proc(proc_root: Path, pid: int, environ: bytes, cmdline: bytes) -> None:
    pid_dir = proc_root / str(pid)
    pid_dir.mkdir(parents=True)
    (pid_dir / "environ").write_bytes(environ)
    (pid_dir / "cmdline").write_bytes(cmdline)


@pytest.fixture
def fake_proc(tmp_path: Path) -> Path:
    root = tmp_path / "proc"
    root.mkdir()
    make_proc(root, 10, b"PATH=x\0LWD_TOOL_RUN_ID=aaaa\0", b"vllm\0serve\0")
    make_proc(
        root,
        11,
        b"LWD_TOOL_RUN_ID=aaaa\0",
        b"python\0-c\0from vllm.v1 import spawn_main\0",
    )
    make_proc(root, 12, b"LWD_TOOL_RUN_ID=bbbb\0", b"vllm\0serve\0")
    make_proc(root, 13, b"PATH=x\0", b"unrelated\0process\0")  # untagged
    (root / "14").mkdir()  # unreadable/gone entry
    return root


def test_scan_groups_by_run_id(fake_proc: Path):
    groups = scan_lwd_processes(fake_proc)
    assert sorted(groups) == ["aaaa", "bbbb"]
    assert [entry.pid for entry in groups["aaaa"]] == [10, 11]


def test_find_vllm_processes_matches_untagged_too(fake_proc: Path):
    pids = [entry.pid for entry in find_vllm_processes(fake_proc)]
    assert pids == [10, 11, 12]  # 13 is unrelated, 14 has no cmdline


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []
        self.answers: list[str] = []
        self.lines: list[str] = []

    def kill(self, pid: int, sig: int) -> None:
        self.calls.append((pid, sig))

    def prompt(self, text: str) -> str:
        return self.answers.pop(0)

    def out(self, line: str) -> None:
        self.lines.append(line)


def run_stop(fake_proc: Path, recorder: Recorder, **kwargs) -> None:
    kwargs.setdefault("launch_dir", "/nonexistent")
    kwargs["proc_root"] = fake_proc
    kwargs["out"] = _StringIO(recorder)
    kwargs["prompt"] = recorder.prompt
    kwargs["killer"] = recorder.kill
    stop(**kwargs)


class _StringIO:
    def __init__(self, recorder: Recorder) -> None:
        self.recorder = recorder

    def write(self, text: str) -> int:
        for line in text.splitlines():
            if line:
                self.recorder.out(line)
        return len(text)


def test_stop_with_run_id_skips_the_menu(fake_proc: Path):
    recorder = Recorder()
    run_stop(fake_proc, recorder, run_id="bbbb")
    assert (12, 15) in recorder.calls  # SIGTERM is 15 on posix
    assert any("run_id=bbbb" in line for line in recorder.lines)


def test_stop_menu_requires_a_choice_for_multiple(fake_proc: Path):
    recorder = Recorder()
    recorder.answers = ["9"]
    with pytest.raises(LwdToolError, match="invalid selection"):
        run_stop(fake_proc, recorder)
    assert recorder.calls == []


def test_stop_menu_picks_one_of_many(fake_proc: Path):
    recorder = Recorder()
    recorder.answers = ["2"]
    run_stop(fake_proc, recorder)
    assert {pid for pid, _ in recorder.calls} == {12}


def test_stop_menu_all(fake_proc: Path):
    recorder = Recorder()
    recorder.answers = ["a"]
    run_stop(fake_proc, recorder)
    assert {pid for pid, _ in recorder.calls} == {10, 11, 12}


def test_stop_without_managed_processes(fake_proc: Path, tmp_path: Path):
    empty = tmp_path / "empty_proc"
    empty.mkdir()
    recorder = Recorder()
    run_stop(empty, recorder)
    assert recorder.calls == []
    assert any("--any-vllm" in line for line in recorder.lines)


def test_stop_any_vllm_hammer(fake_proc: Path):
    recorder = Recorder()
    run_stop(fake_proc, recorder, any_vllm=True)
    assert {pid for pid, _ in recorder.calls} == {10, 11, 12}


def test_run_records_roundtrip(tmp_path: Path):
    record = RunRecord(
        run_id="abcdef123456",
        role="cloud",
        pid=4242,
        argv=("vllm", "serve"),
        log_path="/tmp/x.log",
        started_at="2026-10-21 15:30:00",
        serve_port=9724,
    )
    launch_dir = str(tmp_path / "launch")
    save_run_record(record, launch_dir)
    loaded = load_run_records(launch_dir)
    assert len(loaded) == 1
    assert loaded[0][1]["run_id"] == "abcdef123456"
    assert latest_log("cloud", launch_dir) == "/tmp/x.log"
    with pytest.raises(LwdToolError, match="no run records"):
        latest_log("edge", launch_dir)


def test_latest_log_picks_newest_per_role(tmp_path: Path):
    launch_dir = str(tmp_path / "launch")
    for started, role in (
        ("2026-10-21 09:00:00", "cloud"),
        ("2026-10-21 15:30:00", "cloud"),
        ("2026-10-21 12:00:00", "edge"),
    ):
        save_run_record(
            RunRecord(
                "id" + started, role, 1, ("vllm",), f"/tmp/{role}.log", started, 9724
            ),
            launch_dir,
        )
    assert latest_log(None, launch_dir) == "/tmp/cloud.log"
    assert latest_log("edge", launch_dir) == "/tmp/edge.log"


class FakePopen:
    def __init__(self, argv, env) -> None:
        self.argv = argv
        self.env = env
        self.pid = 4242

    def wait(self) -> int:
        return 7


@pytest.mark.skipif(
    sys.version_info < (3, 10), reason="runs vllm's py3.10+ gen_lwd_topology.py"
)
def test_run_plan_writes_artifacts_and_reports_exit_code(
    tmp_path: Path, interfaces, weights_fs, vllm_root
):
    port = free_port()
    plan = make_plan(
        interfaces,
        weights_fs,
        vllm_root,
        role="cloud",
        log_dir=str(tmp_path / "logs"),
        launch_dir=str(tmp_path / "launch"),
        serve_port=port,
    )
    spawned: list[FakePopen] = []

    def spawn(argv, env):
        child = FakePopen(argv, env)
        spawned.append(child)
        return child

    code = run_plan(plan, spawn=spawn)
    assert code == 7
    assert list(spawned[0].argv) == list(plan.argv)
    assert spawned[0].env["LWD_TOOL_RUN_ID"] == plan.run_id
    assert Path(plan.topology_path).read_text(encoding="utf-8") == (plan.topology_yaml)
    logconf = json.loads(Path(plan.logconf_path).read_text(encoding="utf-8"))
    assert logconf["handlers"]["lwd_file"]["filename"] == plan.log_path
    header = Path(plan.log_path).read_text(encoding="utf-8")
    assert header.startswith("[plan] role=cloud")
    records = load_run_records(str(tmp_path / "launch"))
    assert records[0][1]["pid"] == 4242
    assert records[0][1]["serve_port"] == port


@pytest.mark.skipif(
    sys.version_info < (3, 10), reason="runs vllm's py3.10+ gen_lwd_topology.py"
)
def test_run_plan_refuses_a_taken_port(tmp_path, interfaces, weights_fs, vllm_root):
    port = free_port()
    holder = socket.socket()
    holder.bind(("0.0.0.0", port))
    holder.listen(1)
    try:
        plan = make_plan(
            interfaces,
            weights_fs,
            vllm_root,
            role="cloud",
            log_dir=str(tmp_path / "logs"),
            launch_dir=str(tmp_path / "launch"),
            serve_port=port,
        )
        with pytest.raises(LwdToolError, match="port already in use"):
            run_plan(plan, spawn=lambda argv, env: pytest.fail("must not spawn"))
    finally:
        holder.close()


class _Handler(BaseHTTPRequestHandler):
    status = 200

    def do_GET(self):  # noqa: N802 - http.server API
        self.send_response(self.status)
        self.end_headers()

    def do_POST(self):  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self.send_response(self.status)
        self.end_headers()

    def log_message(self, *args):  # keep the test output clean
        pass


@pytest.fixture
def http_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1], _Handler
    server.shutdown()


def test_health_passes_on_a_healthy_server(http_server):
    port, _ = http_server
    assert health("127.0.0.1", port, "qwen3.6", timeout_s=10) == 0


def test_health_fails_fast_on_a_sick_server(http_server):
    port, handler = http_server
    handler.status = 503
    with pytest.raises(LwdToolError, match="503"):
        health("127.0.0.1", port, "qwen3.6", timeout_s=0)


@pytest.mark.skipif(shutil.which("tail") is None, reason="no tail on this platform")
def test_logs_tails_the_latest_record(tmp_path: Path):
    launch_dir = str(tmp_path / "launch")
    log_path = tmp_path / "edge.log"
    log_path.write_text("hello\n", encoding="utf-8")
    save_run_record(
        RunRecord(
            "id1", "edge", 1, ("vllm",), str(log_path), "2026-10-21 15:30:00", 9724
        ),
        launch_dir,
    )
    from lwd_tool.runbook import logs

    assert logs("edge", launch_dir=launch_dir, follow=False) == 0


@pytest.mark.skipif(
    sys.version_info < (3, 10), reason="runs vllm's py3.10+ gen_lwd_topology.py"
)
def test_format_summary_lists_the_digest_and_mount_hint(
    interfaces, weights_fs, vllm_root
):
    plan = make_plan(interfaces, weights_fs, vllm_root)
    text = format_summary(plan)
    assert "digest=" in text
    assert "-v /home/lwd/logs:/home/lwd/logs" in text
    assert "must print the same digest" in text
