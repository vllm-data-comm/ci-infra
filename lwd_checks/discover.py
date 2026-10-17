"""Discover the LWD test directories under tests/.

A directory whose name contains `lwd` is picked at its topmost level;
LWD tests must not depend on the repository-root conftest, and each one
runs with its own --confcutdir (see docs/design/lwd_ci.md).
"""

from __future__ import annotations

import argparse
from pathlib import Path


def lwd_discover_test_dirs(tests_root: Path | str) -> list[Path]:
    tests_root = Path(tests_root)
    found: list[Path] = []

    def visit(directory: Path) -> None:
        for child in sorted(directory.iterdir()):
            if not child.is_dir():
                continue
            if "lwd" in child.name.lower():
                found.append(child)
            else:
                visit(child)

    if tests_root.is_dir():
        visit(tests_root)
    return sorted(found)


def main() -> None:
    parser = argparse.ArgumentParser(prog="lwd_checks.discover")
    parser.add_argument("tests_root")
    args = parser.parse_args()
    for directory in lwd_discover_test_dirs(args.tests_root):
        print(directory)


if __name__ == "__main__":
    main()
