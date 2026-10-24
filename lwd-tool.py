#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run lwd-tool without installing: `python lwd-tool.py <command> ...`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lwd_tool.cli import main  # noqa: E402 - needs the sys.path tweak above

if __name__ == "__main__":
    sys.exit(main())
