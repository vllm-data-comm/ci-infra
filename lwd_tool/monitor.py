# SPDX-License-Identifier: Apache-2.0
"""Poll a vllm /metrics endpoint and render the terminal dashboard (issue #42).

Replaces the hand-pasted vllm_monitor.sh: the four sections of the script
(request state, latency quantiles, token throughput, speculative decoding)
plus per-refresh rates for the counters and P99 alongside P50. Stdlib only.
"""

from __future__ import annotations

import math
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import IO

from . import LwdToolError

# Bare metric family names as created in vllm/v1/metrics/loggers.py; the
# exposition appends "_total" to counters, which Snapshot.resolve() tries.
METRIC_RUNNING = "vllm:num_requests_running"
METRIC_WAITING = "vllm:num_requests_waiting"
METRIC_KV_USAGE = "vllm:kv_cache_usage_perc"
METRIC_PROMPT_TOKENS = "vllm:prompt_tokens"
METRIC_GEN_TOKENS = "vllm:generation_tokens"
METRIC_FINISHED = "vllm:request_success"
METRIC_TTFT = "vllm:time_to_first_token_seconds"
METRIC_ITL = "vllm:inter_token_latency_seconds"
# Current vllm exposes request_time_per_output_token_seconds; deployments of
# the older fork still have the legacy name - take whichever the endpoint has.
METRIC_TPOT_FAMILIES = (
    "vllm:request_time_per_output_token_seconds",
    "vllm:time_per_output_token_seconds",
)
METRIC_SPEC_DRAFTS = "vllm:spec_decode_num_drafts"
METRIC_SPEC_DRAFT_TOKENS = "vllm:spec_decode_num_draft_tokens"
METRIC_SPEC_ACCEPTED = "vllm:spec_decode_num_accepted_tokens"
METRIC_SPEC_PER_POS = "vllm:spec_decode_num_accepted_tokens_per_pos"

DEFAULT_URL = "http://localhost:9724"

FetchFn = Callable[[str], str]

# The ANSI prefix moves the cursor home and clears below, so each refresh
# repaints the panel without scrolling the terminal (tty mode only).
REPAINT_PREFIX = "\x1b[H\x1b[J"


@dataclass(frozen=True)
class Sample:
    """One exposition sample: a name, its labels and the observed value."""

    name: str
    labels: dict[str, str]
    value: float


_SAMPLE_LINE = re.compile(
    r"^(?P<name>[^\s{]+)(?:\{(?P<labels>.*)\})?\s+(?P<value>\S+)$"
)
_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')


def parse_prometheus_text(text: str) -> list[Sample]:
    """Parse a /metrics text exposition, skipping # HELP/TYPE comment lines.

    Raises:
        LwdToolError: On an empty payload or a sample line with no parsable
            value; the offending line is named.
    """
    samples: list[Sample] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _SAMPLE_LINE.match(stripped)
        if match is None:
            raise LwdToolError(f"unparsable metrics line: {stripped!r}")
        try:
            value = float(match.group("value"))
        except ValueError:
            raise LwdToolError(f"unparsable metrics line: {stripped!r}") from None
        labels = dict(_LABEL.findall(match.group("labels") or ""))
        samples.append(Sample(match.group("name"), labels, value))
    if not samples:
        raise LwdToolError("metrics endpoint returned no samples")
    return samples


@dataclass(frozen=True)
class Histogram:
    """Cumulative buckets of one histogram family, merged over its series."""

    buckets: list[tuple[float, float]]  # (upper bound le, cumulative count)
    count: float


class Snapshot:
    """One parsed scrape, grouped into families by sample name."""

    def __init__(self, samples: Sequence[Sample]) -> None:
        self._families: dict[str, list[Sample]] = {}
        for sample in samples:
            self._families.setdefault(sample.name, []).append(sample)

    def series(self, family: str) -> list[Sample]:
        """All samples of one family; counters' "_total" suffix is tried too."""
        for name in (family, f"{family}_total"):
            found = self._families.get(name)
            if found:
                return found
        return []

    def total(self, family: str) -> float | None:
        """Sum over every series of the family (data-parallel ranks, ...)."""
        found = self.series(family)
        return sum(sample.value for sample in found) if found else None

    def mean(self, family: str) -> float | None:
        """Mean over the series of the family (kv usage is a share, not a sum)."""
        found = self.series(family)
        return sum(s.value for s in found) / len(found) if found else None

    def histogram(self, base: str) -> Histogram | None:
        """Merge the `_bucket` series of one family per `le`, summing counts."""
        cumulative: dict[float, float] = {}
        for sample in self._families.get(f"{base}_bucket", []):
            bound = sample.labels.get("le")
            if bound is None:
                continue
            le = math.inf if bound == "+Inf" else float(bound)
            cumulative[le] = cumulative.get(le, 0.0) + sample.value
        if not cumulative:
            return None
        # _count is the family total; the +Inf bucket is the fallback when a
        # (nonstandard) exposition omits the _count series.
        counts = self._families.get(f"{base}_count", [])
        count = (
            sum(sample.value for sample in counts)
            if counts
            else cumulative.get(math.inf, 0.0)
        )
        return Histogram(sorted(cumulative.items()), count)


def quantile(hist: Histogram, q: float) -> float | None:
    """First bucket whose cumulative count reaches count*q (the script's rule).

    Returns None when nothing was observed; +Inf counts as >max in rendering.
    """
    if hist.count <= 0:
        return None
    target = hist.count * q
    for le, cumulative in hist.buckets:
        if cumulative >= target:
            return le
    return math.inf


class RateTracker:
    """Per-poll counter deltas over the actual elapsed time between polls.

    A counter that went backwards (vllm restarted) drops the baseline, so the
    rate reads N/A for one poll and recovers on the next.
    """

    def __init__(self) -> None:
        self._baseline: dict[str, tuple[float, float]] = {}

    def rate(self, key: str, value: float | None, now: float) -> float | None:
        """Delta per second since the last poll; None while unmeasurable."""
        if value is None:
            self._baseline.pop(key, None)
            return None
        previous = self._baseline.get(key)
        self._baseline[key] = (value, now)
        if previous is None:
            return None
        last_value, last_now = previous
        if value < last_value or now <= last_now:
            return None
        return (value - last_value) / (now - last_now)

    def forget(self, key: str) -> None:
        """Drop one baseline, e.g. when its metric family disappeared."""
        self._baseline.pop(key, None)


@dataclass(frozen=True)
class SpecStats:
    """Speculative decoding section; absent when the endpoint has none."""

    drafts: float | None
    draft_tokens: float | None
    accepted: float | None
    accept_rate: float | None  # accepted / draft_tokens
    mean_accept_len: float | None  # 1 + accepted / drafts, bonus token included
    per_pos: dict[int, float] = field(default_factory=dict)


@dataclass(frozen=True)
class MonitorStats:
    """Every number the panel shows; None renders as N/A."""

    running: float | None
    waiting: float | None
    kv_usage: float | None  # 0..1 share
    ttft_p50: float | None
    ttft_p99: float | None
    tpot_p50: float | None
    tpot_p99: float | None
    itl_p50: float | None
    itl_p99: float | None
    prompt_tokens: float | None
    prompt_rate: float | None
    gen_tokens: float | None
    gen_rate: float | None
    finished: float | None
    finished_rate: float | None
    spec: SpecStats | None


def compute_stats(snapshot: Snapshot, rates: RateTracker, now: float) -> MonitorStats:
    """Derive the panel numbers from one snapshot; `now` is a monotonic stamp."""
    latency: dict[str, tuple[float | None, float | None]] = {}
    for label, bases in (
        ("ttft", (METRIC_TTFT,)),
        ("tpot", METRIC_TPOT_FAMILIES),
        ("itl", (METRIC_ITL,)),
    ):
        for base in bases:
            hist = snapshot.histogram(base)
            if hist is not None:
                latency[label] = (quantile(hist, 0.50), quantile(hist, 0.99))
                break
        else:
            latency[label] = (None, None)
    ttft, tpot, itl = latency["ttft"], latency["tpot"], latency["itl"]

    prompt_tokens = snapshot.total(METRIC_PROMPT_TOKENS)
    gen_tokens = snapshot.total(METRIC_GEN_TOKENS)
    finished = snapshot.total(METRIC_FINISHED)
    spec = _spec_stats(snapshot)
    return MonitorStats(
        running=snapshot.total(METRIC_RUNNING),
        waiting=snapshot.total(METRIC_WAITING),
        kv_usage=snapshot.mean(METRIC_KV_USAGE),
        ttft_p50=ttft[0],
        ttft_p99=ttft[1],
        tpot_p50=tpot[0],
        tpot_p99=tpot[1],
        itl_p50=itl[0],
        itl_p99=itl[1],
        prompt_tokens=prompt_tokens,
        prompt_rate=rates.rate("prompt_tokens", prompt_tokens, now),
        gen_tokens=gen_tokens,
        gen_rate=rates.rate("gen_tokens", gen_tokens, now),
        finished=finished,
        finished_rate=rates.rate("finished", finished, now),
        spec=spec,
    )


def _spec_stats(snapshot: Snapshot) -> SpecStats | None:
    drafts = snapshot.total(METRIC_SPEC_DRAFTS)
    draft_tokens = snapshot.total(METRIC_SPEC_DRAFT_TOKENS)
    accepted = snapshot.total(METRIC_SPEC_ACCEPTED)
    if drafts is None and draft_tokens is None and accepted is None:
        return None
    accept_rate = (
        accepted / draft_tokens if accepted is not None and draft_tokens else None
    )
    mean_accept_len = 1 + accepted / drafts if accepted is not None and drafts else None
    per_pos: dict[int, float] = {}
    for sample in snapshot.series(METRIC_SPEC_PER_POS):
        position = sample.labels.get("position")
        if position is not None:
            per_pos[int(position)] = per_pos.get(int(position), 0.0) + sample.value
    return SpecStats(
        drafts=drafts,
        draft_tokens=draft_tokens,
        accepted=accepted,
        accept_rate=accept_rate,
        mean_accept_len=mean_accept_len,
        per_pos=dict(sorted(per_pos.items())),
    )


def format_count(value: float | None) -> str:
    """Compact count: plain below 10k, then 12.3k / 1.23m (team convention)."""
    if value is None:
        return "N/A"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}m"
    if value >= 10_000:
        return f"{value / 1_000:.1f}k"
    return f"{value:.0f}"


def format_rate(value: float | None) -> str:
    """Count per second, e.g. 12.4k/s; N/A while no delta is measurable."""
    return "N/A" if value is None else f"{format_count(value)}/s"


def format_latency(value: float | None) -> str:
    """Sub-second latencies in ms; +Inf (past the last finite bucket) as >max."""
    if value is None:
        return "N/A"
    if math.isinf(value):
        return ">max"
    if value < 1:
        return f"{value * 1000:.1f} ms"
    return f"{value:.2f} s"


def _format_percent(share: float | None) -> str:
    return "N/A" if share is None else f"{share * 100:.1f}%"


def render_stats(stats: MonitorStats, *, url: str, interval_s: float, ts: str) -> str:
    """Render one dashboard frame (pure; the golden test pins the layout)."""
    bar = "=" * 46
    every = f"每 {interval_s:g}s 刷新"
    lines = [
        bar,
        f"  vLLM 实时监控  {ts}",
        f"  Target: {url}   {every}",
        bar,
        "",
        "--- 请求状态 ---",
        f"  并发运行 (running):  {format_count(stats.running)}",
        f"  等待队列 (waiting):  {format_count(stats.waiting)}",
        f"  KV Cache 使用率:  {_format_percent(stats.kv_usage)}",
        "",
        "--- 延迟 (P50 / P99) ---",
        f"  表显系统 TTFT:  {format_latency(stats.ttft_p50)}"
        f" / {format_latency(stats.ttft_p99)}",
        f"  表显系统 TPOT:  {format_latency(stats.tpot_p50)}"
        f" / {format_latency(stats.tpot_p99)}",
        f"  表显 ITL (中位数):  {format_latency(stats.itl_p50)}"
        f" / {format_latency(stats.itl_p99)}",
        "",
        "--- 吞吐 (累计 / 速率) ---",
        f"  Prompt Tokens:  {format_count(stats.prompt_tokens)}"
        f" ({format_rate(stats.prompt_rate)})",
        f"  Generation Tokens:  {format_count(stats.gen_tokens)}"
        f" ({format_rate(stats.gen_rate)})",
        f"  完成请求数:  {format_count(stats.finished)}"
        f" ({format_rate(stats.finished_rate)})",
        "",
    ]
    if stats.spec is None:
        lines += ["--- 投机解码 ---", "  未开启", ""]
    else:
        spec = stats.spec
        mean_len = (
            "N/A" if spec.mean_accept_len is None else f"{spec.mean_accept_len:.2f}"
        )
        lines += [
            "--- 投机解码 ---",
            f"  Draft 次数:  {format_count(spec.drafts)}",
            f"  Draft Tokens:  {format_count(spec.draft_tokens)}",
            f"  Accepted Tokens:  {format_count(spec.accepted)}",
            f"  接受率:  {_format_percent(spec.accept_rate)}",
            f"  平均接受长度:  {mean_len} (含bonus token)",
        ]
        if spec.per_pos:
            lines.append("  Per-Position 接受数:")
            lines += [
                f"    Position {position}:  {format_count(accepted)}"
                for position, accepted in spec.per_pos.items()
            ]
        lines.append("")
    lines += [bar, f"  Ctrl+C 退出 | {every}", bar]
    return "\n".join(lines)


def fetch_metrics(base_url: str, timeout_s: float = 5.0) -> str:
    """GET {base_url}/metrics and return the exposition text."""
    url = f"{base_url}/metrics"
    request = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            data = response.read()
    except urllib.error.HTTPError as error:
        raise LwdToolError(f"{url} returned {error.code}") from error
    except urllib.error.URLError as error:
        raise LwdToolError(f"cannot reach {url}: {error.reason}") from error
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise LwdToolError(f"{url} is not valid UTF-8: {error}") from error


def normalize_url(url: str) -> str:
    """Prepend http:// when no scheme is given; strip a trailing slash."""
    if "://" not in url:
        url = f"http://{url}"
    return url.rstrip("/")


def monitor(
    url: str,
    *,
    interval_s: float = 2.0,
    once: bool = False,
    out: IO[str] = sys.stdout,
    fetch: FetchFn = fetch_metrics,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Poll and render until Ctrl+C; `--once` prints one frame and returns.

    Fetch/clock/sleep are injectable so the tests need no network and no
    real time. On a tty the frame repaints in place; piped output appends.
    """
    if interval_s <= 0:
        raise LwdToolError(f"interval must be positive, got {interval_s:g}s")
    base_url = normalize_url(url)
    rates = RateTracker()
    in_place = not once and out.isatty()
    first_frame = True
    try:
        while True:
            try:
                frame = _poll_frame(
                    base_url, interval_s, rates=rates, fetch=fetch, clock=clock
                )
            except LwdToolError as error:
                if once:
                    raise
                frame = _error_frame(base_url, interval_s, error)
            if in_place and not first_frame:
                frame = REPAINT_PREFIX + frame
            first_frame = False
            print(frame, file=out, flush=True)
            if once:
                return 0
            sleep(interval_s)
    except KeyboardInterrupt:
        print(file=out)
        return 0


def _poll_frame(
    base_url: str,
    interval_s: float,
    *,
    rates: RateTracker,
    fetch: FetchFn,
    clock: Callable[[], float],
) -> str:
    """Fetch once and render the dashboard frame for it."""
    text = fetch(base_url)
    stats = compute_stats(Snapshot(parse_prometheus_text(text)), rates, clock())
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    return render_stats(stats, url=base_url, interval_s=interval_s, ts=ts)


def _error_frame(base_url: str, interval_s: float, error: LwdToolError) -> str:
    """The frame shown while the endpoint is unreachable (loop keeps trying)."""
    bar = "=" * 46
    return "\n".join(
        (
            bar,
            f"  vLLM 实时监控  {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"  Target: {base_url}   每 {interval_s:g}s 重试",
            bar,
            "",
            f"  [ERROR] {error}",
            "",
        )
    )
