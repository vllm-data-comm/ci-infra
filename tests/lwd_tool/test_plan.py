# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from scenario import (
    CLOUD_IP,
    EDGE_IP,
    make_plan,
)

from lwd_tool import LwdToolError
from lwd_tool.args import PeerSpec

# plan derivation executes vllm's own gen_lwd_topology.py, which needs py3.10+
pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 10), reason="runs vllm's py3.10+ gen_lwd_topology.py"
)


def argv_text(plan) -> str:
    return " ".join(plan.argv)


def test_cloud_plan_derives_the_two_machine_case(interfaces, weights_fs, vllm_root):
    plan = make_plan(interfaces, weights_fs, vllm_root, role="cloud")
    assert plan.tp == 4
    assert plan.serve_port == 9724
    assert plan.master_ip == EDGE_IP
    assert plan.master_port == 29600
    assert plan.ctrl_port == 5550
    assert plan.topology_yaml is not None
    assert EDGE_IP in plan.topology_yaml and CLOUD_IP in plan.topology_yaml
    assert plan.env["MASTER_ADDR"] == EDGE_IP
    assert plan.env["VLLM_HOST_IP"] == CLOUD_IP
    for name in ("GLOO_SOCKET_IFNAME", "TP_SOCKET_IFNAME", "HCCL_SOCKET_IFNAME"):
        assert plan.env[name] == "enp189s0f1"
    assert plan.env["ASCEND_RT_VISIBLE_DEVICES"] == "0,1,2,3"
    assert plan.env["LWD_TOOL_RUN_ID"]
    assert plan.env["VLLM_LOGGING_CONFIG_PATH"].endswith(".logging.json")
    assert "TORCH_DISTRIBUTED_DETAIL" not in plan.env
    assert "--no-enable-prefix-caching" in plan.argv
    assert "--speculative-config" in plan.argv
    assert "/weight/Qwen3.6-27B" in plan.weights_path
    assert plan.preflight_ports == {"serve": 9724, "ctrl": 5550}


def test_edge_plan_uses_edge_side_defaults(tmp_path, interfaces, vllm_root):
    (tmp_path / "home" / "weight" / "Qwen3.6-27B").mkdir(parents=True)
    plan = make_plan(interfaces, tmp_path, vllm_root, role="edge")
    assert plan.tp == 1
    assert plan.cards == (4,)
    assert plan.serve_port == 9724
    assert plan.master_ip == EDGE_IP
    assert plan.env["ASCEND_RT_VISIBLE_DEVICES"] == "4"
    assert plan.env["VLLM_HOST_IP"] == EDGE_IP
    assert plan.env["GLOO_SOCKET_IFNAME"] == "enp189s0f0"
    assert "--speculative-config" not in plan.argv
    assert "--no-enable-prefix-caching" not in plan.argv
    assert '"cudagraph_mode":"NONE"' in argv_text(plan)
    assert "/home/weight/Qwen3.6-27B" in plan.weights_path
    assert plan.preflight_ports == {"serve": 9724, "master": 29600}


def test_single_machine_gives_each_role_a_unique_port(
    interfaces, weights_fs, vllm_root
):
    same_edge = PeerSpec(CLOUD_IP, (4,))
    edge_plan = make_plan(
        interfaces, weights_fs, vllm_root, role="edge", edge=same_edge
    )
    cloud_plan = make_plan(
        interfaces, weights_fs, vllm_root, role="cloud", edge=same_edge
    )
    assert edge_plan.serve_port == 9724
    assert cloud_plan.serve_port == 9725
    assert edge_plan.master_ip == CLOUD_IP
    assert edge_plan.run_id == cloud_plan.run_id


def test_server_role_picks_the_local_cloud_side(interfaces, weights_fs):
    plan = make_plan(interfaces, weights_fs, role="server")
    assert plan.local_ip == CLOUD_IP
    assert plan.cards == (0, 1, 2, 3)
    assert plan.master_ip is None
    assert "MASTER_ADDR" not in plan.env
    assert plan.topology_yaml is None
    assert plan.serve_port == 9724
    assert "--speculative-config" in plan.argv  # centralized keeps MTP
    assert "--no-enable-prefix-caching" not in plan.argv  # role mismatch
    assert "lwd_config" not in argv_text(plan)
    assert any("server mode" in note for note in plan.notes)


def test_server_role_with_explicit_host(interfaces, weights_fs):
    plan = make_plan(
        interfaces,
        weights_fs,
        role="server",
        host=CLOUD_IP,
        cards=(0, 1),
        edge=None,
        cloud=None,
    )
    assert plan.local_ip == CLOUD_IP
    assert plan.cards == (0, 1)
    assert plan.tp == 2


def test_server_role_without_local_side_fails():
    with pytest.raises(LwdToolError, match="neither --edge nor --cloud"):
        make_plan({"10.0.0.5": "eth0"}, Path("/"), role="server")


def test_server_role_with_both_local_takes_cloud(interfaces, weights_fs):
    both_local = {EDGE_IP: "eth9", CLOUD_IP: "eth8", "127.0.0.1": "lo"}
    plan = make_plan(both_local, weights_fs, role="server")
    assert plan.local_ip == CLOUD_IP
    assert any("both hosts are local" in note for note in plan.notes)


def test_local_ip_must_be_on_an_interface(interfaces, weights_fs, vllm_root):
    wrong = {EDGE_IP: "eth0", "10.1.1.1": "eth1"}
    with pytest.raises(LwdToolError, match="not on any local interface"):
        make_plan(wrong, weights_fs, vllm_root, role="cloud")


def test_cli_overrides_win(interfaces, weights_fs, vllm_root):
    plan = make_plan(
        interfaces,
        weights_fs,
        vllm_root,
        max_num_seqs=100,
        serve_port=9731,
        env_overrides={"HCCL_BUFFSIZE": "2048"},
        passthrough=("--enable-lora",),
    )
    text = argv_text(plan)
    assert "--max-num-seqs 100" in text
    assert "--serve-port" not in text  # --port carries it
    assert "--port 9731" in text
    assert plan.env["HCCL_BUFFSIZE"] == "2048"
    assert plan.argv[-1] == "--enable-lora"


def test_no_spec_and_spec_override(interfaces, weights_fs):
    plain = make_plan(interfaces, weights_fs, role="server", no_spec=True)
    assert "--speculative-config" not in plain.argv
    custom = make_plan(interfaces, weights_fs, role="server", spec="dummy_mtp:2")
    config = json.loads(custom.argv[custom.argv.index("--speculative-config") + 1])
    assert config["method"] == "dummy_mtp"
    assert config["num_speculative_tokens"] == 2


def test_explicit_prefix_caching_beats_workaround(interfaces, weights_fs, vllm_root):
    plan = make_plan(
        interfaces, weights_fs, vllm_root, role="cloud", prefix_caching=True
    )
    assert "--enable-prefix-caching" in plan.argv
    assert "--no-enable-prefix-caching" not in plan.argv
    assert any("workaround skipped" in note for note in plan.notes)


def test_weights_search_covers_extra_roots(tmp_path, interfaces, vllm_root):
    (tmp_path / "data" / "weight" / "Qwen3.6-27B").mkdir(parents=True)
    plan = make_plan(interfaces, tmp_path, vllm_root, weights_root=("/data/weight",))
    assert "/data/weight/Qwen3.6-27B" in plan.weights_path


def test_missing_weights_fails_listing_roots(tmp_path, interfaces, vllm_root):
    with pytest.raises(LwdToolError, match="--weights"):
        make_plan(interfaces, tmp_path, vllm_root)
