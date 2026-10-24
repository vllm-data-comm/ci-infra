# SPDX-License-Identifier: Apache-2.0
"""Tests for bench result table parsing (golden: issue #37 run output)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lwd_tool import LwdToolError
from lwd_tool.benchparse import parse_bench_tables, render_summary

# Verbatim output of the issue #37 benchmark run (nightly-20261022-a3).
BENCH_OUTPUT = (Path(__file__).parent / "bench_output_issue37.txt").read_text(
    encoding="utf-8"
)

# The hand-reported summary for that run (issue #37 comment).
SUMMARY_OUTPUT = (Path(__file__).parent / "bench_summary_issue37.md").read_text(
    encoding="utf-8"
)


def test_render_summary_matches_hand_report():
    perf, common = parse_bench_tables(BENCH_OUTPUT)
    assert render_summary(perf, common) == SUMMARY_OUTPUT


def test_parse_bench_tables_extracts_all_fields():
    perf, common = parse_bench_tables(BENCH_OUTPUT)
    assert perf["TTFT"]["Median"] == "1093.3074 ms"
    assert perf["TTFT"]["Average"] == "5891.6256 ms"
    assert common["Request Throughput"] == "1.0015 req/s"
    assert common["Max Concurrency"] == "120"


def test_no_tables_fails_fast():
    with pytest.raises(LwdToolError, match="no bench result tables"):
        parse_bench_tables("unrelated log output, no tables here")


def test_missing_summary_field_fails_fast():
    # Drop the Concurrency row: the summary must name the missing field.
    trimmed = "\n".join(
        line for line in BENCH_OUTPUT.splitlines() if "92.7959" not in line
    )
    perf, common = parse_bench_tables(trimmed)
    with pytest.raises(LwdToolError, match="Concurrency"):
        render_summary(perf, common)
