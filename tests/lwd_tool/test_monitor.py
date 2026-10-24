# SPDX-License-Identifier: Apache-2.0
"""Tests for the /metrics dashboard (golden: the issue #42 fixture)."""

import io
import math
from pathlib import Path

import pytest

from lwd_tool import LwdToolError
from lwd_tool.monitor import (
    Histogram,
    RateTracker,
    Sample,
    Snapshot,
    compute_stats,
    monitor,
    normalize_url,
    parse_prometheus_text,
    quantile,
    render_stats,
)

FIXTURE = (Path(__file__).parent / "metrics_issue42.txt").read_text(encoding="utf-8")
GOLDEN = (Path(__file__).parent / "monitor_panel_golden.txt").read_text(
    encoding="utf-8"
)


def fixture_snapshot() -> Snapshot:
    return Snapshot(parse_prometheus_text(FIXTURE))


# --- parsing ---


def test_parse_extracts_name_labels_value():
    samples = parse_prometheus_text(
        "# HELP vllm:fake help text\n"
        'vllm:fake_bucket{le="+Inf",model_name="m"} 42\n'
        'process_open_fds{escaped="a\\"b"} 1.5\n'
    )
    assert samples == [
        Sample("vllm:fake_bucket", {"le": "+Inf", "model_name": "m"}, 42.0),
        Sample("process_open_fds", {"escaped": 'a\\"b'}, 1.5),
    ]


def test_parse_rejects_a_malformed_sample_line():
    with pytest.raises(LwdToolError, match="unparsable metrics line: 'bad line'"):
        parse_prometheus_text("vllm:fake 12\nbad line\n")
    with pytest.raises(LwdToolError, match="vllm:fake"):
        parse_prometheus_text("vllm:fake not-a-number\n")


def test_parse_rejects_an_empty_payload():
    with pytest.raises(LwdToolError, match="no samples"):
        parse_prometheus_text("")
    with pytest.raises(LwdToolError, match="no samples"):
        parse_prometheus_text("# HELP only comments\n")


# --- snapshot aggregation ---


def test_total_sums_across_dp_series():
    assert fixture_snapshot().total("vllm:prompt_tokens") == 1234567


def test_request_success_sums_over_finished_reason_labels():
    assert fixture_snapshot().total("vllm:request_success") == 18000


def test_mean_averages_kv_series():
    assert fixture_snapshot().mean("vllm:kv_cache_usage_perc") == pytest.approx(0.732)


def test_histogram_merges_series_and_quantiles():
    hist = fixture_snapshot().histogram("vllm:time_to_first_token_seconds")
    assert hist is not None and hist.count == 1000
    # Hand-checked on the fixture's cumulative counts: the 500th and the
    # 990th observation both land in the buckets below.
    assert quantile(hist, 0.50) == 0.25
    assert quantile(hist, 0.99) == 5.0


def test_histogram_merges_bucket_series_per_le():
    merged = Snapshot(
        parse_prometheus_text(
            'h_bucket{le="1",data_parallel_rank="0"} 3\n'
            'h_bucket{le="1",data_parallel_rank="1"} 2\n'
            'h_bucket{le="+Inf",data_parallel_rank="0"} 5\n'
            'h_bucket{le="+Inf",data_parallel_rank="1"} 5\n'
            'h_count{data_parallel_rank="0"} 5\n'
            'h_count{data_parallel_rank="1"} 5\n'
        )
    ).histogram("h")
    assert merged == Histogram(buckets=[(1.0, 5.0), (math.inf, 10.0)], count=10.0)


def test_quantile_returns_inf_beyond_last_finite_bucket():
    sparse = Histogram(buckets=[(1.0, 5.0), (math.inf, 10.0)], count=10.0)
    assert quantile(sparse, 0.99) == math.inf  # renders as ">max"
    assert quantile(sparse, 0.10) == 1.0


def test_tpot_accepts_both_family_names():
    legacy = FIXTURE.replace(
        "vllm:request_time_per_output_token_seconds",
        "vllm:time_per_output_token_seconds",
    )
    for text in (FIXTURE, legacy):
        stats = compute_stats(Snapshot(parse_prometheus_text(text)), RateTracker(), 1.0)
        assert stats.tpot_p50 == 0.05
    neither = "\n".join(
        line for line in FIXTURE.splitlines() if "time_per_output_token" not in line
    )
    stats = compute_stats(Snapshot(parse_prometheus_text(neither)), RateTracker(), 1.0)
    assert stats.tpot_p50 is None and stats.tpot_p99 is None


# --- rates ---


def test_rate_first_poll_none_then_positive():
    rates = RateTracker()
    assert rates.rate("prompt_tokens", 100.0, 1.0) is None
    assert rates.rate("prompt_tokens", 300.0, 3.0) == pytest.approx(100.0)


def test_rate_resets_on_counter_regression():
    rates = RateTracker()
    rates.rate("prompt_tokens", 100.0, 1.0)
    assert rates.rate("prompt_tokens", 50.0, 2.0) is None  # vllm restarted
    assert rates.rate("prompt_tokens", 250.0, 4.0) == pytest.approx(100.0)


def test_rate_family_disappearance_forgets_baseline():
    rates = RateTracker()
    rates.rate("prompt_tokens", 100.0, 1.0)
    assert rates.rate("prompt_tokens", None, 2.0) is None
    assert rates.rate("prompt_tokens", 100.0, 3.0) is None  # baseline was dropped


# --- rendering ---


def test_render_golden_first_frame():
    stats = compute_stats(fixture_snapshot(), RateTracker(), 100.0)
    frame = render_stats(
        stats, url="http://localhost:9724", interval_s=2.0, ts="2026-10-24 15:02:11"
    )
    assert frame + "\n" == GOLDEN


def test_render_second_frame_shows_rates():
    rates = RateTracker()
    compute_stats(fixture_snapshot(), rates, 100.0)
    grown = FIXTURE.replace("600000", "700000")
    stats = compute_stats(Snapshot(parse_prometheus_text(grown)), rates, 103.0)
    assert stats.prompt_rate == pytest.approx(100000 / 3)
    frame = render_stats(
        stats, url="http://localhost:9724", interval_s=2.0, ts="2026-10-24 15:02:11"
    )
    assert "33.3k/s" in frame


def test_render_without_spec_metrics_shows_not_enabled():
    stripped = "\n".join(
        line for line in FIXTURE.splitlines() if "spec_decode" not in line
    )
    stats = compute_stats(Snapshot(parse_prometheus_text(stripped)), RateTracker(), 1.0)
    assert stats.spec is None
    frame = render_stats(stats, url="u", interval_s=2.0, ts="2026-10-24 15:02:11")
    assert "--- 投机解码 ---\n  未开启" in frame


# --- the monitor loop ---


def test_monitor_once_unreachable_fails_fast():
    def unreachable(url):
        raise LwdToolError(f"cannot reach {url}: connection refused")

    with pytest.raises(LwdToolError, match="cannot reach"):
        monitor("http://localhost:1", once=True, fetch=unreachable)


def test_monitor_loop_shows_error_frame_then_recovers():
    polls = iter(("fail", "ok", "interrupt"))

    def fetch(url):
        state = next(polls)
        if state == "fail":
            raise LwdToolError(f"cannot reach {url}: connection refused")
        if state == "interrupt":
            raise KeyboardInterrupt
        return FIXTURE

    out = io.StringIO()
    code = monitor(
        "localhost:9724", out=out, fetch=fetch, clock=lambda: 1.0, sleep=lambda s: None
    )
    assert code == 0
    rendered = out.getvalue()
    assert "[ERROR] cannot reach http://localhost:9724" in rendered
    assert "--- 请求状态 ---" in rendered


def test_monitor_keyboard_interrupt_exits_zero():
    def interrupt(url):
        raise KeyboardInterrupt

    out = io.StringIO()
    code = monitor(
        "localhost:9724",
        out=out,
        fetch=interrupt,
        clock=lambda: 1.0,
        sleep=lambda s: None,
    )
    assert code == 0


def test_monitor_once_renders_the_panel():
    out = io.StringIO()
    code = monitor(
        "http://localhost:9724",
        once=True,
        out=out,
        fetch=lambda url: FIXTURE,
        clock=lambda: 1.0,
        sleep=lambda s: None,
    )
    assert code == 0
    assert "--- 请求状态 ---" in out.getvalue()


# --- cli helpers ---


def test_normalize_url_adds_scheme_and_strips_slash():
    assert normalize_url("76.76.26.180:9724") == "http://76.76.26.180:9724"
    assert normalize_url("http://localhost:9724/") == "http://localhost:9724"


def test_monitor_rejects_nonpositive_interval():
    with pytest.raises(LwdToolError, match="interval must be positive"):
        monitor("localhost:9724", interval_s=0, fetch=lambda url: FIXTURE)
