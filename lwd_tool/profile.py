# SPDX-License-Identifier: Apache-2.0
"""Model profiles: the static per-model diff over the shared baseline."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from . import LwdToolError
from .args import ROLES

MODELS_DIR = Path(__file__).resolve().parent / "models"

PROFILE_KEYS = (
    "model",
    "weights_dir",
    "served_name",
    "max_model_len",
    "max_num_seqs",
    "max_num_batched_tokens",
    "speculative",
    "compilation",
    "extra_flags",
    "workarounds",
    "env_overrides",
)


@dataclass(frozen=True)
class Workaround:
    """A temporary serve flag, dropped once its reason is fixed."""

    role: str
    flags: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class ModelProfile:
    name: str
    weights_dir: str | None = None
    served_name: str | None = None
    max_model_len: int | None = None
    max_num_seqs: int | None = None
    max_num_batched_tokens: int | None = None
    speculative: dict[str, Any] | None = None
    compilation: dict[str, dict[str, Any]] | None = None
    extra_flags: tuple[str, ...] = ()
    workarounds: tuple[Workaround, ...] = ()
    env_overrides: dict[str, str] | None = None


def expand_capture_sizes(value: Any) -> list[int]:
    """Expand "8..800:8" (or pass through an explicit int list)."""
    if isinstance(value, list):
        if not all(
            isinstance(item, int) and not isinstance(item, bool) for item in value
        ):
            raise LwdToolError(f"capture_sizes list must be ints, got {value!r}")
        return list(value)
    if not isinstance(value, str):
        raise LwdToolError(
            f"capture_sizes must be a list or 'lo..hi:step', got {value!r}"
        )
    span, sep, step_text = value.partition(":")
    low, range_sep, high = span.partition("..")
    if not (
        sep and range_sep and low.isdigit() and high.isdigit() and step_text.isdigit()
    ):
        raise LwdToolError(f"capture_sizes must look like '8..800:8', got {value!r}")
    start, end, step = int(low), int(high), int(step_text)
    if step < 1 or end < start:
        raise LwdToolError(f"capture_sizes range is invalid: {value!r}")
    return list(range(start, end + 1, step))


def _parse_compilation(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        raise LwdToolError("compilation must map roles to config dicts")
    result: dict[str, dict[str, Any]] = {}
    for role, config in raw.items():
        if not isinstance(config, dict):
            raise LwdToolError(f"compilation[{role!r}] must be a dict")
        entry = dict(config)
        if "capture_sizes" in entry:
            entry["cudagraph_capture_sizes"] = expand_capture_sizes(
                entry.pop("capture_sizes")
            )
        result[str(role)] = entry
    return result


def _parse_workarounds(raw: Any) -> tuple[Workaround, ...]:
    if not isinstance(raw, list):
        raise LwdToolError("workarounds must be a list")
    items = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise LwdToolError(f"each workaround must be a dict, got {entry!r}")
        unknown = set(entry) - {"role", "flags", "reason"}
        if unknown:
            raise LwdToolError(f"unknown workaround keys: {sorted(unknown)}")
        missing = {"role", "flags", "reason"} - set(entry)
        if missing:
            raise LwdToolError(f"workaround missing required keys: {sorted(missing)}")
        role = entry["role"]
        flags = entry["flags"]
        reason = entry["reason"]
        if role not in ROLES:
            raise LwdToolError(f"workaround role must be one of {ROLES}, got {role!r}")
        if (
            not isinstance(flags, list)
            or not flags
            or not all(isinstance(flag, str) for flag in flags)
        ):
            raise LwdToolError("workaround flags must be a non-empty string list")
        if not isinstance(reason, str) or not reason.strip():
            raise LwdToolError(
                "workaround reason is required (drop the entry once fixed)"
            )
        items.append(Workaround(role, tuple(flags), reason))
    return tuple(items)


def load_profile(name: str, models_dir: Path = MODELS_DIR) -> ModelProfile:
    path = models_dir / f"{name}.yaml"
    if not path.is_file():
        available = sorted(p.stem for p in models_dir.glob("*.yaml"))
        raise LwdToolError(f"unknown model {name!r}; available profiles: {available}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise LwdToolError(f"{path} must contain a mapping")
    unknown = set(raw) - set(PROFILE_KEYS)
    if unknown:
        raise LwdToolError(f"unknown profile keys in {path.name}: {sorted(unknown)}")
    return ModelProfile(
        name=name,
        weights_dir=raw.get("weights_dir"),
        served_name=raw.get("served_name"),
        max_model_len=raw.get("max_model_len"),
        max_num_seqs=raw.get("max_num_seqs"),
        max_num_batched_tokens=raw.get("max_num_batched_tokens"),
        speculative=raw.get("speculative"),
        compilation=(
            _parse_compilation(raw["compilation"])
            if raw.get("compilation") is not None
            else None
        ),
        extra_flags=tuple(raw.get("extra_flags") or ()),
        workarounds=_parse_workarounds(raw.get("workarounds") or []),
        env_overrides=raw.get("env_overrides"),
    )
