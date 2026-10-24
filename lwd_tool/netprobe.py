# SPDX-License-Identifier: Apache-2.0
"""Find which network interface holds a given local IP (read-only probe)."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping

from . import LwdToolError

_IP_ADDR_LINE = re.compile(r"^\d+:\s+(\S+)\s+inet\s+(\d{1,3}(?:\.\d{1,3}){3})/\d+")
_IFCONFIG_HEADER = re.compile(r"^(\S+?):?\s+(?:flags=|Link|ether|inet)")
_IFCONFIG_INET = re.compile(r"^\s+inet (?:addr:)?(\d{1,3}(?:\.\d{1,3}){3})\s")


def parse_ip_addr_output(text: str) -> dict[str, str]:
    """Parse `ip -o -4 addr show` lines into {ipv4: interface}."""
    interfaces: dict[str, str] = {}
    for line in text.splitlines():
        match = _IP_ADDR_LINE.match(line)
        if match:
            interfaces.setdefault(match.group(2), match.group(1))
    return interfaces


def parse_ifconfig_output(text: str) -> dict[str, str]:
    """Parse `ifconfig -a` blocks into {ipv4: interface}."""
    interfaces: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        header = _IFCONFIG_HEADER.match(line)
        if header and " inet" not in line.split("  ")[0]:
            current = header.group(1).rstrip(":")
            continue
        inet = _IFCONFIG_INET.match(line)
        if inet and current is not None:
            interfaces.setdefault(inet.group(1), current)
    return interfaces


def _run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise LwdToolError(
            f"{' '.join(command)} failed: {result.stderr.strip() or result.returncode}"
        )
    return result.stdout


def probe_interfaces() -> dict[str, str]:
    """List local IPv4 addresses via `ip` or `ifconfig`, whichever exists."""
    try:
        return parse_ip_addr_output(_run(["ip", "-o", "-4", "addr", "show"]))
    except (FileNotFoundError, LwdToolError):
        pass
    try:
        return parse_ifconfig_output(_run(["ifconfig", "-a"]))
    except FileNotFoundError as exc:
        raise LwdToolError(
            "neither `ip` nor `ifconfig` is available; "
            "lwd-tool runs inside the target machine's container"
        ) from exc


def interface_for_ip(ip: str, interfaces: Mapping[str, str]) -> str:
    try:
        return interfaces[ip]
    except KeyError:
        table = (
            "\n".join(
                f"  {addr:<15} {name}" for addr, name in sorted(interfaces.items())
            )
            or "  (none found)"
        )
        raise LwdToolError(
            f"IP {ip} is not on any local interface - wrong machine or role?\n"
            f"local interfaces:\n{table}"
        ) from None
