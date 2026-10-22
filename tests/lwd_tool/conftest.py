# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

import pytest
from scenario import CLOUD_IP, EDGE_IP

from lwd_tool import LwdToolError
from lwd_tool.topology import find_vllm_root


@pytest.fixture
def interfaces() -> dict[str, str]:
    return {EDGE_IP: "enp189s0f0", CLOUD_IP: "enp189s0f1", "127.0.0.1": "lo"}


@pytest.fixture
def weights_fs(tmp_path: Path) -> Path:
    """187-style layout: the model lives under /weight."""
    (tmp_path / "weight" / "Qwen3.6-27B").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def vllm_root() -> Path:
    try:
        return find_vllm_root()
    except LwdToolError:
        pytest.skip("no vllm checkout with tools/gen_lwd_topology.py nearby")
