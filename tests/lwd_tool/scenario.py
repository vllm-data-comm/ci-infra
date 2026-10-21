# SPDX-License-Identifier: Apache-2.0
"""Shared two-machine scenario constants and plan builder for the tests."""

from datetime import datetime
from pathlib import Path

from lwd_tool.args import LaunchArgs, PeerSpec
from lwd_tool.plan import derive_launch_plan
from lwd_tool.profile import load_profile

EDGE_IP = "76.76.26.180"
CLOUD_IP = "76.76.26.187"
EDGE = PeerSpec(EDGE_IP, (4,))
CLOUD = PeerSpec(CLOUD_IP, (0, 1, 2, 3))
FIXED_NOW = datetime(2026, 10, 21, 15, 30, 0)


def make_args(**overrides) -> LaunchArgs:
    base: dict = dict(role="cloud", model="qwen3.6-27b", edge=EDGE, cloud=CLOUD)
    base.update(overrides)
    return LaunchArgs(**base)


def make_plan(
    interfaces: dict[str, str],
    weights_fs: Path,
    vllm_root: Path | None = None,
    **overrides,
):
    largs = make_args(**overrides)
    profile = load_profile(largs.model)
    return derive_launch_plan(
        largs,
        profile,
        interfaces,
        vllm_root=vllm_root,
        fs_root=weights_fs,
        now=FIXED_NOW,
    )
