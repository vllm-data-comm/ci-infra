# SPDX-License-Identifier: Apache-2.0
"""Topology generation by reusing vllm's tools/gen_lwd_topology.py."""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any

from . import LwdToolError
from .args import PeerSpec

GENERATOR_REL = Path("tools") / "gen_lwd_topology.py"
RUNTIME_SCHEMA_REL = Path("vllm") / "config" / "lwd" / "topology.py"


def _sibling_candidates() -> list[Path]:
    """Vllm checkouts sitting next to or inside the working directory."""
    package_dir = Path(__file__).resolve().parent
    # list() first: py3.9's PurePath.parents does not support slicing
    candidates = [parent.parent / "vllm" for parent in list(package_dir.parents)[:5]]
    candidates.append(Path.cwd() / "vllm")  # ci test.yml checks out ./vllm
    candidates.append(Path.cwd().parent / "vllm")
    return candidates


def find_vllm_root(explicit: str | None = None) -> Path:
    """Locate a vllm checkout that carries tools/gen_lwd_topology.py."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(Path("/vllm-workspace/vllm"))
    candidates.extend(_sibling_candidates())
    for candidate in candidates:
        if (candidate / GENERATOR_REL).is_file():
            return candidate
    searched = "\n".join(f"  {path}" for path in candidates)
    raise LwdToolError(
        "no vllm checkout with tools/gen_lwd_topology.py found; "
        f"pass --vllm-root. Searched:\n{searched}"
    )


def cross_check(raw: dict[str, Any], edge: PeerSpec, cloud: PeerSpec) -> int:
    """Fail fast if the generated topology does not match the card spec."""
    edge_ranks = raw["edges"][0]["dp"][0]["ranks"]
    cloud_dp = raw["clouds"][0]["dp"][0]
    world = raw["deployment"]["hccl_world_size"]
    expected = 1 + len(cloud.cards)
    problems = []
    if tuple(edge_ranks) != (0,) or len(edge_ranks) != len(edge.cards):
        problems.append(f"edge ranks {edge_ranks} do not match cards {edge.cards}")
    if len(cloud_dp["ranks"]) != len(cloud.cards):
        problems.append(
            f"cloud ranks {cloud_dp['ranks']} do not match cards {cloud.cards}"
        )
    if world != expected:
        problems.append(f"hccl_world_size {world} != {expected}")
    if cloud_dp["addr"] != cloud.ip or raw["edges"][0]["dp"][0]["addr"] != edge.ip:
        problems.append("topology addresses do not match the requested hosts")
    if problems:
        raise LwdToolError("topology cross-check failed: " + "; ".join(problems))
    return world


def validate_with_runtime_schema(raw: dict[str, Any], vllm_root: Path) -> str | None:
    """Run vllm's real pydantic schema when importable; skip on dev machines."""
    schema_path = vllm_root / RUNTIME_SCHEMA_REL
    if not schema_path.is_file():
        return None
    try:
        namespace = runpy.run_path(str(schema_path))
    except ImportError:
        return None  # e.g. no pydantic outside the container
    try:
        namespace["LwdTopology"].from_dict(raw)
    except Exception as exc:  # pydantic raises ValueError subclasses
        raise LwdToolError(f"topology failed vllm schema validation: {exc}") from exc
    return "validated"


def generate_topology(
    edge: PeerSpec,
    cloud: PeerSpec,
    *,
    ctrl_port_base: int,
    early_recv: bool,
    scramble: bool,
    vllm_root: Path,
) -> str:
    """Render lwd_topology.yaml text; rank allocation stays in vllm's tool."""
    # lazy: only this path needs pyyaml, so monitor runs on bare python hosts
    import yaml

    namespace = runpy.run_path(str(vllm_root / GENERATOR_REL))
    if "build_topology" not in namespace:
        raise LwdToolError(
            f"{vllm_root / GENERATOR_REL} has no build_topology(); "
            "the vllm checkout and lwd-tool are out of sync - update both"
        )
    try:
        raw = namespace["build_topology"](
            scene="single_instance",
            edge_machines=edge.ip,
            cloud_machines=cloud.ip,
            dp=1,
            port_base=ctrl_port_base,
            cloud_cards=f"0:{len(cloud.cards)}",
        )
    except (TypeError, ValueError) as exc:
        raise LwdToolError(
            f"build_topology rejected the arguments - the vllm checkout and "
            f"lwd-tool are out of sync: {exc}"
        ) from exc
    raw["feature_ctrl"] = {
        "enable_early_recv": early_recv,
        "enable_scramble": scramble,
    }
    cross_check(raw, edge, cloud)
    validate_with_runtime_schema(raw, vllm_root)
    return yaml.safe_dump(raw, sort_keys=False)
