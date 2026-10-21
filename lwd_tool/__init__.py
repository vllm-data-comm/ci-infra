# SPDX-License-Identifier: Apache-2.0
"""lwd-tool: launch LWD edge/cloud/centralized vllm serve from one command."""


class LwdToolError(Exception):
    """User-facing failure; the CLI prints it and exits non-zero."""
