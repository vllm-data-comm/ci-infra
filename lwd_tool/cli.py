# SPDX-License-Identifier: Apache-2.0
"""The lwd-tool command line: run / plan / stop / logs / health / monitor."""

import argparse
import os
import sys
from collections.abc import Sequence

from . import LwdToolError
from .args import ROLE_EDGE, ROLE_SERVER, ROLES, LaunchArgs
from .monitor import DEFAULT_URL, monitor
from .netprobe import probe_interfaces
from .plan import derive_launch_plan, derive_serve_port, format_summary
from .profile import load_profile
from .runbook import health, logs, run_plan, stop
from .topology import find_vllm_root

PASSTHROUGH_SEPARATOR = "--"


def add_launch_args(parser: argparse.ArgumentParser) -> None:
    deploy = parser.add_argument_group("deployment")
    deploy.add_argument("--role", required=True, choices=ROLES)
    deploy.add_argument("--model", required=True, help="model profile name")
    deploy.add_argument("--edge", metavar="IP:CARDS", help="e.g. 76.76.26.180:4")
    deploy.add_argument("--cloud", metavar="IP:CARDS", help="e.g. 76.76.26.187:0-3")
    deploy.add_argument("--host", metavar="IP", help="server role only")
    deploy.add_argument("--cards", metavar="CARDS", help="server role only")
    deploy.add_argument("--early-recv", action="store_true")
    deploy.add_argument("--scramble", action="store_true")

    ports = parser.add_argument_group("ports")
    ports.add_argument("--serve-port", type=int)
    ports.add_argument("--port-base", type=int, default=9724)
    ports.add_argument("--master-port", type=int, default=29600)
    ports.add_argument("--ctrl-port-base", type=int, default=5550)

    engine = parser.add_argument_group("engine (defaults come from the model profile)")
    engine.add_argument("--max-model-len", type=int)
    engine.add_argument("--max-num-seqs", type=int)
    engine.add_argument("--max-num-batched-tokens", type=int)
    engine.add_argument("--gpu-memory-utilization", type=float)
    spec = engine.add_mutually_exclusive_group()
    spec.add_argument("--spec", metavar="METHOD[:NUM]", help="e.g. qwen3_5_mtp:3")
    spec.add_argument("--no-spec", action="store_true")
    engine.add_argument("--cudagraph", choices=("none", "full-decode-only"))
    caching = engine.add_mutually_exclusive_group()
    caching.add_argument(
        "--prefix-caching", dest="prefix_caching", action="store_true", default=None
    )
    caching.add_argument(
        "--no-prefix-caching", dest="prefix_caching", action="store_false", default=None
    )
    engine.add_argument("--served-name")

    paths = parser.add_argument_group("paths and observability")
    paths.add_argument("--weights", metavar="PATH")
    paths.add_argument("--weights-root", action="append", metavar="PATH", default=[])
    paths.add_argument("--launch-dir", default="/vllm-workspace/lwd_launch")
    paths.add_argument("--log-dir", default="/home/lwd/logs")
    paths.add_argument("--log-level", default="INFO")
    paths.add_argument("--lwd-log-level", default="INFO")
    paths.add_argument("--env", action="append", metavar="VAR=VAL", default=[])
    paths.add_argument("--vllm-root", metavar="PATH")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lwd-tool",
        description="Launch LWD edge/cloud/centralized vllm serve from one command.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("run", "derive everything and start vllm serve in the foreground"),
        ("plan", "show the derivation without starting anything"),
    ):
        child = sub.add_parser(name, help=help_text)
        add_launch_args(child)
    stop_p = sub.add_parser("stop", help="stop deployments running on this machine")
    stop_p.add_argument("--run-id", help="act on one deployment, no menu")
    stop_p.add_argument(
        "--any-vllm",
        action="store_true",
        help="kill every vllm-like process on this machine (heavy hammer)",
    )
    stop_p.add_argument("--launch-dir", default="/vllm-workspace/lwd_launch")
    logs_p = sub.add_parser("logs", help="tail the latest log of a role")
    logs_p.add_argument("--role", choices=ROLES)
    logs_p.add_argument("--launch-dir", default="/vllm-workspace/lwd_launch")
    logs_p.add_argument(
        "--no-follow", action="store_true", help="print the tail and exit"
    )
    health_p = sub.add_parser(
        "health", help="wait for the edge endpoint, then send one request"
    )
    health_p.add_argument("--timeout", type=int, default=600)
    add_launch_args(health_p)
    monitor_p = sub.add_parser(
        "monitor", help="poll a vllm /metrics endpoint and show a dashboard"
    )
    monitor_p.add_argument(
        "url",
        nargs="?",
        default=DEFAULT_URL,
        help=f"metrics base URL (default: {DEFAULT_URL})",
    )
    monitor_p.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="seconds between refreshes (default: 2)",
    )
    monitor_p.add_argument(
        "--once", action="store_true", help="print one snapshot and exit"
    )
    return parser


def split_passthrough(argv: Sequence[str]) -> tuple[list[str], list[str]]:
    """Everything after a bare "--" goes to vllm serve verbatim."""
    argv = list(argv)
    if PASSTHROUGH_SEPARATOR in argv:
        index = argv.index(PASSTHROUGH_SEPARATOR)
        return argv[:index], argv[index + 1 :]
    return argv, []


def _derive(ns: argparse.Namespace, passthrough: list[str]):
    ns.passthrough = passthrough
    largs = LaunchArgs.from_namespace(ns)
    profile = load_profile(ns.model)
    interfaces = probe_interfaces()
    vllm_root = None
    if largs.role != ROLE_SERVER:
        vllm_root = find_vllm_root(largs.vllm_root)
    plan = derive_launch_plan(
        largs, profile, interfaces, vllm_root=vllm_root, existing_env=dict(os.environ)
    )
    return largs, profile, plan


def _health_target(largs: LaunchArgs, served_name: str) -> tuple[str, int, str]:
    """The edge side is the client entry; the server role serves itself."""
    if largs.role == ROLE_SERVER:
        from .netprobe import interface_for_ip  # local interfaces already probed
        from .plan import resolve_server_source

        interfaces = probe_interfaces()
        host_ip, cards, _ = resolve_server_source(largs, interfaces)
        interface_for_ip(host_ip, interfaces)  # same wrong-machine guard as run
        port = largs.serve_port or largs.port_base
        return host_ip, port, served_name
    assert largs.edge is not None and largs.cloud is not None
    port = largs.serve_port or derive_serve_port(
        ROLE_EDGE, largs.edge.ip, largs.edge, largs.cloud, largs.port_base
    )
    return largs.edge.ip, port, served_name


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    head, passthrough = split_passthrough(argv)
    parser = build_parser()
    ns = parser.parse_args(head)
    try:
        if ns.command in ("run", "plan"):
            _, profile, plan = _derive(ns, passthrough)
            if ns.command == "plan":
                print(format_summary(plan))
                return 0
            return run_plan(plan)
        if ns.command == "stop":
            return stop(
                run_id=ns.run_id,
                any_vllm=ns.any_vllm,
                launch_dir=ns.launch_dir,
            )
        if ns.command == "logs":
            return logs(ns.role, launch_dir=ns.launch_dir, follow=not ns.no_follow)
        if ns.command == "health":
            largs = LaunchArgs.from_namespace(ns)
            profile = load_profile(largs.model)
            served_name = largs.served_name or profile.served_name or profile.name
            host, port, _ = _health_target(largs, served_name)
            return health(host, port, served_name, timeout_s=ns.timeout)
        if ns.command == "monitor":
            return monitor(ns.url, interval_s=ns.interval, once=ns.once)
        parser.error(f"unknown command {ns.command!r}")
    except LwdToolError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0
