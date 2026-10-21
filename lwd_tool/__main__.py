# SPDX-License-Identifier: Apache-2.0
"""python -m lwd_tool entry."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
