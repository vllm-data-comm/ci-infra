# SPDX-License-Identifier: Apache-2.0
"""Full argv golden snapshots for scenarios A/B/C.

The expected values were diffed line by line against the hand-run serve
scripts in vllm issue #18 (2026-10-21); any diff here means the generator
drifted and needs a second look.
"""

from __future__ import annotations

import json
import sys

import pytest
from scenario import CLOUD_IP, EDGE_IP, make_plan

# argv goldens come from full plan derivation, which runs vllm's own
# gen_lwd_topology.py (py3.10+ syntax)
pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 10), reason="runs vllm's py3.10+ gen_lwd_topology.py"
)

W = "<WEIGHTS>"

ADDITIONAL_EDGE = (
    '{"enable_cpu_binding":true,"lwd_config":{"path":'
    '"/vllm-workspace/lwd_launch/lwd_topology.yaml","role":"edge","instance_id":0}}'
)
ADDITIONAL_CLOUD = (
    '{"enable_cpu_binding":true,"lwd_config":{"path":'
    '"/vllm-workspace/lwd_launch/lwd_topology.yaml","role":"cloud","instance_id":0}}'
)
COMPILE_EDGE = '{"cudagraph_mode":"NONE"}'
COMPILE_CLOUD = json.dumps(
    {
        "cudagraph_mode": "FULL_DECODE_ONLY",
        "cudagraph_capture_sizes": list(range(8, 801, 8)),
    },
    separators=(",", ":"),
)
SPEC = json.dumps(
    {
        "method": "qwen3_5_mtp",
        "num_speculative_tokens": 3,
        "enforce_eager": True,
    },
    separators=(",", ":"),
)


def head(weights: str, port: int) -> list[str]:
    return ["vllm", "serve", weights, "--host", "0.0.0.0", "--port", str(port)]


def tail(tp: int) -> list[str]:
    return [
        "--max-model-len",
        "262144",
        "--served-model-name",
        "qwen3.6",
        "--trust-remote-code",
        "--max-num-seqs",
        "230",
        "--max-num-batched-tokens",
        "8192",
        "--async-scheduling",
        "--gpu-memory-utilization",
        "0.95",
        "--pipeline-parallel-size",
        "1",
        "--tensor-parallel-size",
        str(tp),
    ]


def test_golden_two_machine_cloud(interfaces, weights_fs, vllm_root):
    plan = make_plan(interfaces, weights_fs, vllm_root, role="cloud")
    expected = head(W, 9724) + [
        "--master-addr",
        EDGE_IP,
        "--master-port",
        "29600",
        *tail(4),
        "--additional-config",
        ADDITIONAL_CLOUD,
        "--compilation-config",
        COMPILE_CLOUD,
        "--speculative-config",
        SPEC,
        "--no-enable-prefix-caching",
    ]
    assert list(plan.argv) == [arg.replace(W, plan.weights_path) for arg in expected]
    assert plan.env["PYTHONPATH"] == (
        "/vllm-workspace/vllm-ascend:/vllm-workspace/vllm"
    )
    assert plan.env["LD_PRELOAD"] == ("/usr/lib/aarch64-linux-gnu/libjemalloc.so.2")
    assert plan.env["TASK_QUEUE_ENABLE"] == "1"
    assert plan.env["HCCL_BUFFSIZE"] == "1024"
    assert plan.log_path.startswith("/home/lwd/logs/20261021_153000_cloud_")


def test_golden_two_machine_edge(tmp_path, interfaces, vllm_root):
    (tmp_path / "home" / "weight" / "Qwen3.6-27B").mkdir(parents=True)
    plan = make_plan(interfaces, tmp_path, vllm_root, role="edge")
    expected = head(W, 9724) + [
        "--master-addr",
        EDGE_IP,
        "--master-port",
        "29600",
        *tail(1),
        "--additional-config",
        ADDITIONAL_EDGE,
        "--compilation-config",
        COMPILE_EDGE,
    ]
    assert list(plan.argv) == [arg.replace(W, plan.weights_path) for arg in expected]


def test_golden_single_machine_ports(interfaces, weights_fs, vllm_root):
    from lwd_tool.args import PeerSpec

    edge = PeerSpec(CLOUD_IP, (4,))
    edge_plan = make_plan(interfaces, weights_fs, vllm_root, role="edge", edge=edge)
    cloud_plan = make_plan(interfaces, weights_fs, vllm_root, role="cloud", edge=edge)
    assert "--port 9724" in " ".join(edge_plan.argv)
    assert "--port 9725" in " ".join(cloud_plan.argv)
    assert edge_plan.run_id == cloud_plan.run_id


def test_golden_centralized(interfaces, weights_fs):
    plan = make_plan(interfaces, weights_fs, role="server")
    expected = head(W, 9724) + [
        *tail(4),
        "--additional-config",
        '{"enable_cpu_binding":true}',
        "--compilation-config",
        COMPILE_CLOUD,
        "--speculative-config",
        SPEC,
    ]
    assert list(plan.argv) == [arg.replace(W, plan.weights_path) for arg in expected]
    assert "MASTER_ADDR" not in plan.env
