# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from lwd_tool import LwdToolError
from lwd_tool.profile import (
    MODELS_DIR,
    expand_capture_sizes,
    load_profile,
)


def test_packaged_profile_loads():
    profile = load_profile("qwen3.6-27b")
    assert profile.weights_dir == "Qwen3.6-27B"
    assert profile.served_name == "qwen3.6"
    assert profile.max_model_len == 262144
    assert profile.max_num_seqs == 230
    assert profile.max_num_batched_tokens == 8192
    assert profile.speculative == {
        "method": "qwen3_5_mtp",
        "num_speculative_tokens": 3,
        "enforce_eager": True,
    }
    assert len(profile.workarounds) == 1
    workaround = profile.workarounds[0]
    assert workaround.role == "cloud"
    assert workaround.flags == ("--no-enable-prefix-caching",)
    assert "Bug #8" in workaround.reason


def test_unknown_model_lists_available_profiles():
    with pytest.raises(LwdToolError, match="qwen3.6-27b"):
        load_profile("no-such-model", MODELS_DIR)


def write_profile(tmp_path: Path, body: str) -> Path:
    models = tmp_path / "models"
    models.mkdir()
    (models / "probe.yaml").write_text(textwrap.dedent(body), encoding="utf-8")
    return models


def test_workaround_without_reason_is_rejected(tmp_path: Path):
    models = write_profile(
        tmp_path,
        """
        model: probe
        workarounds:
          - role: cloud
            flags: [--x]
        """,
    )
    with pytest.raises(LwdToolError, match="reason"):
        load_profile("probe", models)


def test_workaround_with_bad_role_is_rejected(tmp_path: Path):
    models = write_profile(
        tmp_path,
        """
        model: probe
        workarounds:
          - role: tenant
            flags: [--x]
            reason: r
        """,
    )
    with pytest.raises(LwdToolError, match="role"):
        load_profile("probe", models)


def test_unknown_profile_key_is_rejected(tmp_path: Path):
    models = write_profile(
        tmp_path,
        """
        model: probe
        max_context: 4096
        """,
    )
    with pytest.raises(LwdToolError, match="max_context"):
        load_profile("probe", models)


def test_compilation_capture_sizes_expand(tmp_path: Path):
    models = write_profile(
        tmp_path,
        """
        model: probe
        compilation:
          cloud:
            cudagraph_mode: FULL_DECODE_ONLY
            capture_sizes: "8..16:8"
        """,
    )
    profile = load_profile("probe", models)
    assert profile.compilation is not None
    assert profile.compilation["cloud"]["cudagraph_capture_sizes"] == [8, 16]
    assert "capture_sizes" not in profile.compilation["cloud"]


def test_env_overrides_load(tmp_path: Path):
    models = write_profile(
        tmp_path,
        """
        model: probe
        env_overrides:
          HCCL_BUFFSIZE: "2048"
        """,
    )
    profile = load_profile("probe", models)
    assert profile.env_overrides == {"HCCL_BUFFSIZE": "2048"}


def test_capture_sizes_passthrough_and_errors():
    assert expand_capture_sizes([1, 2, 3]) == [1, 2, 3]
    assert expand_capture_sizes("0..4:2") == [0, 2, 4]
    with pytest.raises(LwdToolError):
        expand_capture_sizes("8..800")
    with pytest.raises(LwdToolError):
        expand_capture_sizes("9..8:1")
    with pytest.raises(LwdToolError):
        expand_capture_sizes(3.5)
