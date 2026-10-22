# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

import pytest
import yaml
from scenario import CLOUD, EDGE

from lwd_tool import LwdToolError
from lwd_tool.args import PeerSpec
from lwd_tool.topology import (
    cross_check,
    find_vllm_root,
    generate_topology,
)


def test_topology_matches_the_card_spec(vllm_root: Path):
    text = generate_topology(
        EDGE,
        CLOUD,
        ctrl_port_base=5550,
        early_recv=False,
        scramble=False,
        vllm_root=vllm_root,
    )
    raw = yaml.safe_load(text)
    assert raw["deployment"]["hccl_world_size"] == 5
    assert raw["edges"][0]["dp"][0]["ranks"] == [0]
    assert raw["edges"][0]["dp"][0]["addr"] == EDGE.ip
    assert raw["clouds"][0]["dp"][0]["ranks"] == [1, 2, 3, 4]
    assert raw["clouds"][0]["dp"][0]["ctrl_port"] == 5550
    assert raw["feature_ctrl"] == {
        "enable_early_recv": False,
        "enable_scramble": False,
    }
    assert raw["instance_links"] == [{"edge": 0, "cloud": 0}]


def test_topology_is_deterministic(vllm_root: Path):
    kwargs = dict(
        ctrl_port_base=5550, early_recv=False, scramble=False, vllm_root=vllm_root
    )
    assert generate_topology(EDGE, CLOUD, **kwargs) == generate_topology(
        EDGE, CLOUD, **kwargs
    )


def test_ctrl_port_base_and_features_flow_through(vllm_root: Path):
    raw = yaml.safe_load(
        generate_topology(
            EDGE,
            CLOUD,
            ctrl_port_base=5560,
            early_recv=True,
            scramble=True,
            vllm_root=vllm_root,
        )
    )
    assert raw["clouds"][0]["dp"][0]["ctrl_port"] == 5560
    assert raw["feature_ctrl"]["enable_early_recv"] is True
    assert raw["feature_ctrl"]["enable_scramble"] is True


def test_cross_check_catches_rank_mismatch():
    raw = {
        "deployment": {"hccl_world_size": 9},
        "edges": [{"dp": [{"ranks": [0, 1], "addr": EDGE.ip}]}],
        "clouds": [{"dp": [{"ranks": [2, 3, 4, 5], "addr": CLOUD.ip}]}],
    }
    with pytest.raises(LwdToolError, match="cross-check"):
        cross_check(raw, EDGE, CLOUD)


def test_cross_check_rejects_wrong_addresses():
    raw = {
        "deployment": {"hccl_world_size": 5},
        "edges": [{"dp": [{"ranks": [0], "addr": "10.9.9.9"}]}],
        "clouds": [{"dp": [{"ranks": [1, 2, 3, 4], "addr": CLOUD.ip}]}],
    }
    with pytest.raises(LwdToolError, match="addresses"):
        cross_check(raw, EDGE, CLOUD)


def test_cross_check_accepts_a_consistent_topology():
    raw = {
        "deployment": {"hccl_world_size": 5},
        "edges": [{"dp": [{"ranks": [0], "addr": EDGE.ip}]}],
        "clouds": [{"dp": [{"ranks": [1, 2, 3, 4], "addr": CLOUD.ip}]}],
    }
    assert cross_check(raw, EDGE, CLOUD) == 5


def test_find_vllm_root_honors_explicit_path(tmp_path: Path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "gen_lwd_topology.py").write_text("", encoding="utf-8")
    assert find_vllm_root(str(tmp_path)) == tmp_path


def test_find_vllm_root_fails_listing_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import lwd_tool.topology as topology

    monkeypatch.setattr(topology, "_sibling_candidates", lambda: [])
    monkeypatch.chdir(tmp_path)
    with pytest.raises(LwdToolError, match="--vllm-root"):
        find_vllm_root()


def test_two_cards_edge_cloud(vllm_root: Path):
    edge = PeerSpec("76.76.26.180", (0,))
    cloud = PeerSpec("76.76.26.187", (0, 1))
    raw = yaml.safe_load(
        generate_topology(
            edge,
            cloud,
            ctrl_port_base=5550,
            early_recv=False,
            scramble=False,
            vllm_root=vllm_root,
        )
    )
    assert raw["deployment"]["hccl_world_size"] == 3
    assert raw["clouds"][0]["dp"][0]["ranks"] == [1, 2]
