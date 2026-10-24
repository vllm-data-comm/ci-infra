# SPDX-License-Identifier: Apache-2.0
"""Derive the full launch plan: ports, parallel sizes, env and serve argv."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from . import LwdToolError
from .args import ROLE_CLOUD, ROLE_EDGE, ROLE_SERVER, LaunchArgs, PeerSpec
from .envs import build_dynamic_env, build_static_env
from .logconf import render_config_json
from .netprobe import interface_for_ip
from .profile import ModelProfile
from .topology import generate_topology

WEIGHT_ROOTS = ("/weight", "/home/weight", "/vllm-workspace/weight")
TOPOLOGY_NAME = "lwd_topology.yaml"
GPU_MEMORY_UTILIZATION = 0.95
BASE_COMPILATION: dict[str, dict[str, Any]] = {
    ROLE_EDGE: {"cudagraph_mode": "NONE"},
    ROLE_CLOUD: {
        "cudagraph_mode": "FULL_DECODE_ONLY",
        "cudagraph_capture_sizes": list(range(8, 801, 8)),
    },
}


@dataclass(frozen=True)
class LaunchPlan:
    """Everything needed to start (or show) one role's vllm serve."""

    role: str
    model: ModelProfile
    local_ip: str
    cards: tuple[int, ...]
    tp: int
    serve_port: int
    master_ip: str | None
    master_port: int
    ctrl_port: int | None
    topology_yaml: str | None
    topology_path: str
    weights_path: str
    argv: tuple[str, ...]
    env: dict[str, str]
    log_path: str
    logconf_path: str
    run_id: str
    notes: tuple[str, ...]
    preflight_ports: dict[str, int]
    launch_dir: str
    log_level: str


def resolve_weights(
    profile: ModelProfile,
    explicit: str | None,
    extra_roots: tuple[str, ...],
    fs_root: Path = Path("/"),
) -> tuple[str, str]:
    """Return (path, where-it-came-from); fixed roots first, then extras."""
    if explicit:
        if not (fs_root / explicit.lstrip("/")).is_dir():
            raise LwdToolError(f"--weights path does not exist: {explicit}")
        return explicit, "--weights"
    if not profile.weights_dir:
        raise LwdToolError(
            f"profile {profile.name!r} has no weights_dir; pass --weights"
        )
    roots = (*WEIGHT_ROOTS, *extra_roots)
    for root in roots:
        candidate = fs_root / root.lstrip("/") / profile.weights_dir
        if candidate.is_dir():
            return candidate.as_posix(), root
    searched = ", ".join(roots)
    raise LwdToolError(
        f"weights dir {profile.weights_dir!r} not found under: {searched}; "
        "pass --weights or --weights-root"
    )


def resolve_server_source(
    largs: LaunchArgs, interfaces: Mapping[str, str]
) -> tuple[str, tuple[int, ...], tuple[str, ...]]:
    """Pick the centralized host/cards; notes explain every ignored input."""
    if largs.host is not None and largs.cards is not None:
        notes = []
        if largs.edge or largs.cloud:
            notes.append("server mode: using --host/--cards, ignoring --edge/--cloud")
        return largs.host, largs.cards, tuple(notes)
    local_peers = [
        (name, peer)
        for name, peer in ((ROLE_EDGE, largs.edge), (ROLE_CLOUD, largs.cloud))
        if peer is not None and peer.ip in interfaces
    ]
    if not local_peers:
        raise LwdToolError(
            "server mode: neither --edge nor --cloud host is local; "
            "run on the machine you want to serve from, or pass --host/--cards"
        )
    if len(local_peers) == 2:
        name, peer = ROLE_CLOUD, largs.cloud
        assert peer is not None
        notes = [f"server mode: both hosts are local, using --{name} {peer.ip}"]
    else:
        name, peer = local_peers[0]
        other = largs.cloud if name == ROLE_EDGE else largs.edge
        ignored = (
            f", ignoring --{'cloud' if name == 'edge' else 'edge'} {other.ip}"
            if other
            else ""
        )
        notes = [
            f"server mode: using --{name} {peer.ip}:{_cards_text(peer.cards)}{ignored}"
        ]
    return peer.ip, peer.cards, tuple(notes)


def _cards_text(cards: tuple[int, ...]) -> str:
    return ",".join(str(card) for card in cards)


def derive_serve_port(
    role: str, host_ip: str, edge: PeerSpec | None, cloud: PeerSpec | None, base: int
) -> int:
    """One port per HTTP process on a host, edge first, deterministic."""
    order = [
        name
        for name, peer in ((ROLE_EDGE, edge), (ROLE_CLOUD, cloud))
        if peer is not None and peer.ip == host_ip
    ]
    if role not in order:
        order.append(role)  # server role or unexpected host: sole process
    return base + order.index(role)


def compute_run_id(
    largs: LaunchArgs, model: str, server: tuple[str, tuple[int, ...]] | None
) -> str:
    """Deployment identity shared by both sides; role-independent."""
    if server is not None:
        spec: dict[str, Any] = {
            "mode": ROLE_SERVER,
            "host": server[0],
            "cards": list(server[1]),
            "port_base": largs.port_base,
        }
    else:
        assert largs.edge is not None and largs.cloud is not None
        spec = {
            "model": model,
            "edge": [largs.edge.ip, list(largs.edge.cards)],
            "cloud": [largs.cloud.ip, list(largs.cloud.cards)],
            "port_base": largs.port_base,
            "master_port": largs.master_port,
            "ctrl_port_base": largs.ctrl_port_base,
            "early_recv": largs.early_recv,
            "scramble": largs.scramble,
        }
    return sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:12]


def _effective_spec(
    largs: LaunchArgs, profile: ModelProfile, role: str
) -> dict[str, Any] | None:
    if largs.no_spec or role not in (ROLE_CLOUD, ROLE_SERVER):
        return None
    spec = dict(profile.speculative or {})
    if largs.spec:
        method, _, num_text = largs.spec.partition(":")
        spec["method"] = method
        if num_text:
            if not num_text.isdigit():
                raise LwdToolError(f"--spec expects METHOD[:NUM], got {largs.spec!r}")
            spec["num_speculative_tokens"] = int(num_text)
    return spec or None


def _caching_flags(
    largs: LaunchArgs, profile: ModelProfile, role: str
) -> tuple[list[str], list[str]]:
    """Explicit --[no-]prefix-caching wins over profile workarounds."""
    disabling = [
        flag
        for wa in profile.workarounds
        if wa.role == role
        for flag in wa.flags
        if flag == "--no-enable-prefix-caching"
    ]
    notes: list[str] = []
    if largs.prefix_caching is True:
        if disabling:
            notes.append(
                "prefix caching kept enabled by --prefix-caching; "
                f"workaround skipped ({profile.name})"
            )
        return ["--enable-prefix-caching"], notes
    if largs.prefix_caching is False:
        return ["--no-enable-prefix-caching"], notes
    return disabling, notes


def _compilation_config(
    largs: LaunchArgs, profile: ModelProfile, role: str
) -> dict[str, Any]:
    key = ROLE_CLOUD if role == ROLE_SERVER else role
    config = dict(BASE_COMPILATION[key])
    if profile.compilation and key in profile.compilation:
        config.update(profile.compilation[key])
    if largs.cudagraph == "none":
        config = {"cudagraph_mode": "NONE"}
        config.pop("cudagraph_capture_sizes", None)
    elif largs.cudagraph == "full-decode-only":
        config["cudagraph_mode"] = "FULL_DECODE_ONLY"
        config.setdefault("cudagraph_capture_sizes", list(range(8, 801, 8)))
    return config


def build_argv(
    largs: LaunchArgs,
    profile: ModelProfile,
    *,
    weights_path: str,
    tp: int,
    serve_port: int,
    master_ip: str | None,
    topology_path: str,
) -> tuple[list[str], list[str]]:
    notes: list[str] = []
    additional: dict[str, Any] = {"enable_cpu_binding": True}
    if master_ip is not None:
        additional["lwd_config"] = {
            "path": topology_path,
            "role": largs.role,
            "instance_id": 0,
        }
    argv = [
        "vllm",
        "serve",
        weights_path,
        "--host",
        "0.0.0.0",
        "--port",
        str(serve_port),
    ]
    if master_ip is not None:
        argv += ["--master-addr", master_ip, "--master-port", str(largs.master_port)]
    argv += [
        "--max-model-len",
        str(largs.max_model_len or profile.max_model_len),
        "--served-model-name",
        largs.served_name or profile.served_name or profile.name,
        "--trust-remote-code",
        "--max-num-seqs",
        str(largs.max_num_seqs or profile.max_num_seqs),
        "--max-num-batched-tokens",
        str(largs.max_num_batched_tokens or profile.max_num_batched_tokens),
        "--async-scheduling",
        "--gpu-memory-utilization",
        str(largs.gpu_memory_utilization or GPU_MEMORY_UTILIZATION),
        "--pipeline-parallel-size",
        "1",
        "--tensor-parallel-size",
        str(tp),
        "--additional-config",
        json.dumps(additional, separators=(",", ":")),
        "--compilation-config",
        json.dumps(
            _compilation_config(largs, profile, largs.role), separators=(",", ":")
        ),
    ]
    spec = _effective_spec(largs, profile, largs.role)
    if spec is not None:
        argv += ["--speculative-config", json.dumps(spec, separators=(",", ":"))]
    caching_flags, caching_notes = _caching_flags(largs, profile, largs.role)
    argv += caching_flags
    notes += caching_notes
    other_flags = [
        flag
        for wa in profile.workarounds
        if wa.role == largs.role
        for flag in wa.flags
        if flag != "--no-enable-prefix-caching"
    ]
    argv += other_flags + list(profile.extra_flags) + list(largs.passthrough)
    return argv, notes


def derive_launch_plan(
    largs: LaunchArgs,
    profile: ModelProfile,
    interfaces: Mapping[str, str],
    *,
    vllm_root: Path | None = None,
    existing_env: Mapping[str, str] | None = None,
    fs_root: Path = Path("/"),
    now: datetime | None = None,
) -> LaunchPlan:
    notes: list[str] = []
    server: tuple[str, tuple[int, ...]] | None = None
    topology_yaml: str | None = None
    if largs.role == ROLE_SERVER:
        host_ip, cards, server_notes = resolve_server_source(largs, interfaces)
        server = (host_ip, cards)
        notes += list(server_notes)
        master_ip = None
        ctrl_port = None
    else:
        assert largs.edge is not None and largs.cloud is not None
        peer = largs.edge if largs.role == ROLE_EDGE else largs.cloud
        host_ip, cards = peer.ip, peer.cards
        master_ip = largs.edge.ip
        ctrl_port = largs.ctrl_port_base if largs.role == ROLE_CLOUD else None
        assert vllm_root is not None, "edge/cloud need a vllm checkout"
        topology_yaml = generate_topology(
            largs.edge,
            largs.cloud,
            ctrl_port_base=largs.ctrl_port_base,
            early_recv=largs.early_recv,
            scramble=largs.scramble,
            vllm_root=vllm_root,
        )
    interface = interface_for_ip(host_ip, interfaces)
    weights_path, weights_source = resolve_weights(
        profile, largs.weights, largs.weights_root, fs_root
    )
    notes.append(f"weights: {weights_path} ({weights_source})")
    run_id = compute_run_id(largs, profile.name, server)
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    log_path = f"{largs.log_dir}/{stamp}_{largs.role}_{run_id[:6]}.log"
    logconf_path = f"{largs.log_dir}/{stamp}_{largs.role}_{run_id[:6]}.logging.json"
    env = build_static_env(existing_env)
    env.update(
        build_dynamic_env(
            host_ip=host_ip,
            cards=cards,
            interface=interface,
            master_ip=master_ip,
            lwd_log_level=largs.lwd_log_level,
            run_id=run_id,
            logconf_path=logconf_path,
        )
    )
    if profile.env_overrides:
        env.update(profile.env_overrides)
    env.update(largs.env_overrides)
    if largs.role == ROLE_SERVER:
        # One process, no peers: the centralized serve always takes the base.
        serve_port = largs.serve_port or largs.port_base
    else:
        serve_port = largs.serve_port or derive_serve_port(
            largs.role, host_ip, largs.edge, largs.cloud, largs.port_base
        )
    argv, argv_notes = build_argv(
        largs,
        profile,
        weights_path=weights_path,
        tp=len(cards),
        serve_port=serve_port,
        master_ip=master_ip,
        topology_path=f"{largs.launch_dir}/{TOPOLOGY_NAME}",
    )
    notes += argv_notes
    preflight = {"serve": serve_port}
    if ctrl_port is not None:
        preflight["ctrl"] = ctrl_port
    if largs.role == ROLE_EDGE:
        preflight["master"] = largs.master_port
    return LaunchPlan(
        role=largs.role,
        model=profile,
        local_ip=host_ip,
        cards=cards,
        tp=len(cards),
        serve_port=serve_port,
        master_ip=master_ip,
        master_port=largs.master_port,
        ctrl_port=ctrl_port,
        topology_yaml=topology_yaml,
        topology_path=f"{largs.launch_dir}/{TOPOLOGY_NAME}",
        weights_path=weights_path,
        argv=tuple(argv),
        env=env,
        log_path=log_path,
        logconf_path=logconf_path,
        run_id=run_id,
        notes=tuple(notes),
        preflight_ports=preflight,
        launch_dir=largs.launch_dir,
        log_level=largs.log_level,
    )


def topology_digest(plan: LaunchPlan) -> str | None:
    if plan.topology_yaml is None:
        return None
    return sha256(plan.topology_yaml.encode()).hexdigest()[:8]


def format_summary(plan: LaunchPlan, lwd_tool_version: str = "") -> str:
    lines = [
        f"[plan] role={plan.role} model={plan.model.name} host={plan.local_ip} "
        f"cards={_cards_text(plan.cards)} tp={plan.tp} serve_port={plan.serve_port}",
    ]
    if plan.master_ip is not None:
        lines.append(
            f"[plan] master={plan.master_ip}:{plan.master_port} "
            f"ctrl_port={plan.ctrl_port}"
        )
    digest = topology_digest(plan)
    if digest:
        lines.append(
            f"[plan] topology {plan.topology_path} digest={digest} "
            "(both machines must print the same digest)"
        )
    lines.append(f"[plan] run_id={plan.run_id} log={plan.log_path}")
    for note in plan.notes:
        lines.append(f"[plan] {note}")
    log_dir = dirname(plan.log_path)
    lines.append(f"[plan] mount the log dir from the host: -v {log_dir}:{log_dir}")
    lines.append("[plan] $ " + " ".join(plan.argv))
    for name, value in sorted(plan.env.items()):
        lines.append(f"[plan] env {name}={value}")
    return "\n".join(lines)


def dirname(path: str) -> str:
    return path.rpartition("/")[0] or "/"


def plan_logconf(plan: LaunchPlan, level: str) -> str:
    return render_config_json(plan.log_path, level)
