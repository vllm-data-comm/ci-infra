# SPDX-License-Identifier: Apache-2.0
"""Parse bench result tables into the one-row markdown summary used in issues.

Input is the terminal output of the serving benchmark (two box-drawn tables:
"Performance Parameters" per-metric stats and "Common Metric" totals); output
is the summary table the team reports by hand (issue #37 style: actual request
rate, system ttft/tpot, throughput, concurrency, max concurrency, median ITL).
"""

import sys
from collections.abc import Iterator
from pathlib import Path

from . import LwdToolError

PERF_TABLE_TITLE = "Performance Parameters"
COMMON_TABLE_TITLE = "Common Metric"

# Latency metrics report the Median stat (the same choice as the hand-written
# reports, which pin the median on ITL).
LATENCY_STAT = "Median"

# (summary column, source) — source is ("common", key) or ("perf", metric).
SUMMARY_COLUMNS: list[tuple[str, tuple[str, ...]]] = [
    ("表显实际请求率", ("common", "Request Throughput")),
    ("表显系统ttft", ("perf", "TTFT")),
    ("表显系统tpot", ("perf", "TPOT")),
    ("表显吞吐", ("common", "Output Token Throughput")),
    ("表显并发", ("common", "Concurrency")),
    ("限制最大并发", ("common", "Max Concurrency")),
    ("表显ITL(中位数)", ("perf", "ITL")),
]


def _data_rows(text: str) -> Iterator[list[str]]:
    """Yield the stripped cells of every table row (lines containing │)."""
    for line in text.splitlines():
        if "│" not in line:
            continue
        cells = [cell.strip() for cell in line.split("│")[1:-1]]
        if any(cells):
            yield cells


def parse_bench_tables(
    text: str,
) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """Split the two bench tables into perf stats and common totals.

    Returns (perf, common): perf maps metric -> stat -> value, common maps
    key -> value.
    """
    perf: dict[str, dict[str, str]] = {}
    common: dict[str, str] = {}
    perf_stats: list[str] | None = None
    for cells in _data_rows(text):
        if cells[0] == PERF_TABLE_TITLE:
            perf_stats = cells[2:]  # drop the Stage column
        elif cells[0] == COMMON_TABLE_TITLE:
            perf_stats = None
        elif perf_stats is not None:
            perf[cells[0]] = dict(zip(perf_stats, cells[2:], strict=True))
        else:
            common[cells[0]] = cells[2]
    if not perf and not common:
        raise LwdToolError("no bench result tables found in input")
    return perf, common


def render_summary(perf: dict[str, dict[str, str]], common: dict[str, str]) -> str:
    """Render the summary markdown table; fail fast on any missing field."""
    values = []
    for _, source in SUMMARY_COLUMNS:
        if source[0] == "common":
            value = common.get(source[1])
        else:
            value = perf.get(source[1], {}).get(LATENCY_STAT)
        if value is None:
            raise LwdToolError(f"bench output is missing {source[1]!r}")
        values.append(value)
    headers = [header for header, _ in SUMMARY_COLUMNS]
    align = ["---"] * len(headers)
    return "\n".join("| " + " | ".join(row) + " |" for row in (headers, align, values))


def bench_parse(source: str) -> int:
    """CLI entry: read bench output from a file (or stdin with '-') and print."""
    if source == "-":
        data = sys.stdin.buffer.read()
    else:
        path = Path(source)
        if not path.is_file():
            raise LwdToolError(f"no such file: {source}")
        data = path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise LwdToolError(f"bench output is not valid UTF-8: {error}") from error
    print(render_summary(*parse_bench_tables(text)))
    return 0
