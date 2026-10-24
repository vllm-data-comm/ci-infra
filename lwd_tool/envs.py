# SPDX-License-Identifier: Apache-2.0
"""The static env baseline shared by every role, plus dynamic injection."""

from __future__ import annotations

from collections.abc import Mapping

CODEBASE_DIR = "/vllm-workspace"
JEMALLOC_PATH = "/usr/lib/aarch64-linux-gnu/libjemalloc.so.2"

# Kept verbatim from the hand-run serve scripts (issue #18): the ascend
# performance switches are inert for models that do not use them, so they
# stay global instead of being split per model.
BASE_ENV: dict[str, str] = {
    "PYTORCH_NPU_ALLOC_CONF": "expandable_segments:True",
    "HCCL_OP_EXPANSION_MODE": "AIV",
    "VLLM_WORKER_MULTIPROC_METHOD": "spawn",
    "HCCL_BUFFSIZE": "1024",
    "OMP_PROC_BIND": "false",
    "OMP_NUM_THREADS": "1",
    "TASK_QUEUE_ENABLE": "1",
    "VLLM_ASCEND_ENABLE_PREFETCH_MLP": "1",
    "VLLM_ASCEND_ENABLE_DENSE_OPTIMIZE": "1",
    "VLLM_ASCEND_ENABLE_NZ": "1",
    "VLLM_ASCEND_ENABLE_FUSED_MC2": "1",
    "VLLM_ASCEND_GDN_FAST_PATH": "1",
    "VLLM_ASCEND_GDN_MAX_PADDING_RATIO": "2.0",
    "VLLM_ASCEND_GDN_MAX_H_OVERALLOC_RATIO": "2.0",
}


def build_static_env(existing: Mapping[str, str] | None = None) -> dict[str, str]:
    """Baseline env; PYTHONPATH/LD_PRELOAD prepend to the current values."""
    env = dict(BASE_ENV)
    sources = f"{CODEBASE_DIR}/vllm-ascend:{CODEBASE_DIR}/vllm"
    prior_pythonpath = (existing or {}).get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{sources}:{prior_pythonpath}" if prior_pythonpath else sources
    prior_preload = (existing or {}).get("LD_PRELOAD", "")
    env["LD_PRELOAD"] = (
        f"{JEMALLOC_PATH}:{prior_preload}" if prior_preload else JEMALLOC_PATH
    )
    return env


def build_dynamic_env(
    *,
    host_ip: str,
    cards: tuple[int, ...],
    interface: str,
    master_ip: str | None,
    lwd_log_level: str,
    run_id: str,
    logconf_path: str,
) -> dict[str, str]:
    """Role-specific env; MASTER_ADDR only exists for the LWD roles."""
    env = {
        "VLLM_HOST_IP": host_ip,
        "GLOO_SOCKET_IFNAME": interface,
        "TP_SOCKET_IFNAME": interface,
        "HCCL_SOCKET_IFNAME": interface,
        "ASCEND_RT_VISIBLE_DEVICES": ",".join(str(card) for card in cards),
        "VLLM_LWD_LOG_LEVEL": lwd_log_level,
        "VLLM_LOGGING_CONFIG_PATH": logconf_path,
        "LWD_TOOL_RUN_ID": run_id,
    }
    if master_ip is not None:
        env["MASTER_ADDR"] = master_ip
    return env
