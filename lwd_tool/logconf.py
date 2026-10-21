# SPDX-License-Identifier: Apache-2.0
"""The vllm logging dictConfig that writes Python logs to file only."""

import json
from typing import Any

# Mirrors vllm/logger.py so file lines match the native format; no console
# handler is added, keeping stdout quiet while the file keeps everything.
_FORMAT = "%(levelname)s %(asctime)s [%(fileinfo)s:%(lineno)d] %(message)s"
_DATE_FORMAT = "%m-%d %H:%M:%S"
FILE_HANDLER = "lwd_file"


def build_logging_config(log_path: str, level: str) -> dict[str, Any]:
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "vllm": {
                "class": "vllm.logging_utils.NewLineFormatter",
                "datefmt": _DATE_FORMAT,
                "format": _FORMAT,
            }
        },
        "handlers": {
            FILE_HANDLER: {
                "class": "logging.FileHandler",
                "level": level,
                "formatter": "vllm",
                "filename": log_path,
                "mode": "a",
                "encoding": "utf-8",
            }
        },
        "loggers": {
            "vllm": {
                "handlers": [FILE_HANDLER],
                "level": level,
                "propagate": False,
            }
        },
    }


def render_config_json(log_path: str, level: str) -> str:
    return json.dumps(build_logging_config(log_path, level), indent=2)
