# LWD Tool

`lwd-tool` is the launch CLI for LWD: one command per role that derives the whole
`vllm serve` invocation (topology, env, ports, parallel sizes, model flags) and
runs it. It replaces the hand-edited `serve_edge.sh` / `serve_cloud.sh` scripts
that were pasted and re-pasted into vllm issue #18 — every revision of those
fixed a manual mistake (a port collision, a forgotten `--no-enable-prefix-caching`,
a per-host model path), and issue #1 recorded a launch failure caused by workers
left over from a crashed run that nobody could clean up reliably.

## Design decisions (aligned with the team, 2026-10-21)

1. **Runs inside the container; users start each role by hand.** No cross-machine
   orchestration, no code sync: the tool ships in the lwd image, and `run` is
   executed on each machine in its own terminal (cloud first, then edge — the
   order stays human). Foreground only; stdout stays clean.
2. **No inventory files.** Machine facts are dynamic: IPs are CLI arguments, the
   NIC holding the local IP is probed via `ifconfig`/`ip addr` (a miss is a
   fail-fast "wrong machine?" report), and weights are searched under fixed roots
   (`/weight`, `/home/weight`, `/vllm-workspace/weight`) with `--weights` as the
   explicit escape. The only static data shipped besides code is the model
   profiles.
3. **Model profiles are per-model diffs over a built-in baseline.** The baseline
   carries the flags shared by every model (from the issue #18 scripts); a
   profile adds what differs (weights dir, max lens, MTP spec, compilation,
   extra flags) plus `workarounds` — temporary flags that must carry a `reason`
   and are dropped once the reason is fixed.
4. **Precedence: `--` passthrough > explicit CLI > model profile > baseline.**
   Everything after a bare `--` reaches `vllm serve` verbatim, so the tool never
   needs to mirror vllm's argparse.
5. **Topology generation has a single source of truth**: vllm's
   `tools/gen_lwd_topology.py`, loaded via `runpy` and called as a function.
   Both machines derive byte-identical YAML from the same command line; the tool
   cross-checks the result against the card spec and prints a digest for
   eyeball comparison. Rank allocation is never reimplemented here.
6. **Logs go to a persistent host directory, file only.** `run` writes a
   dictConfig JSON (`VLLM_LOGGING_CONFIG_PATH`, the mechanism in
   `vllm/logger.py`) with a single file handler and no console handler, so
   vllm's Python logs land in `{log_dir}/<ts>_<role>_<runid>.log` and stdout
   only shows the plan summary, the log path and the exit code. Native stderr
   (HCCL/CANN) still reaches the terminal as an immediate error channel. Each
   log file starts with the plan summary and the full argv — the "how was this
   run" record.
7. **`stop`/`logs` never auto-pick among running deployments.** `run` tags the
   serve process with `LWD_TOOL_RUN_ID` (inherited by every spawned
   EngineCore/worker); `stop` scans `/proc/*/environ`, groups by run id, lists
   every deployment and requires a manual choice when there is more than one.
   `stop --any-vllm` is the explicit hammer for stray processes.
8. **v1 scope: one edge + one cloud (`single_instance`) and the centralized
   arm.** `--role server` reuses the cloud command with only the role changed:
   it picks whichever of `--edge/--cloud` is local (both local → cloud, with a
   printed note), drops topology/`lwd_config`/master/ctrl, keeps the MTP spec
   and the cloud compilation profile, and applies workarounds only on an exact
   role match. Multi-instance scenes are rejected at the argument level until
   they are verified on real machines.

## Derivation rules

| Item | Rule |
| --- | --- |
| `--tensor-parallel-size` | card count of this instance |
| `--pipeline-parallel-size` | always 1 |
| `ASCEND_RT_VISIBLE_DEVICES` | this instance's card list; card *i* ↔ local rank *i* ↔ `dp[].ranks[i]` |
| `MASTER_ADDR` / `--master-addr` | the edge host (global rank 0 lives there); identical on both sides; absent for `server` |
| `--master-port` | one value per deployment (default 29600), shared by every process |
| ctrl ports | `--ctrl-port-base` (default 5550) passed through to `gen_lwd_topology` |
| serve `--port` | per HTTP process on a host, counting from `--port-base` (default 9724), edge before cloud; `--serve-port` overrides one process |
| port preflight | `run` refuses to start when a port it must bind is taken, and lists the other deployments it sees |

## Runbook

```bash
# cloud (on 76.76.26.187):
lwd-tool run --role cloud --model qwen3.6-27b \
    --edge 76.76.26.180:4 --cloud 76.76.26.187:0-3
# edge (on 76.76.26.180; same --edge/--cloud, only --role differs):
lwd-tool run --role edge  --model qwen3.6-27b \
    --edge 76.76.26.180:4 --cloud 76.76.26.187:0-3

# centralized comparison: flip one word of the cloud command
lwd-tool run --role server --model qwen3.6-27b \
    --edge 76.76.26.180:4 --cloud 76.76.26.187:0-3

lwd-tool plan ...    # same args; print everything without starting
lwd-tool stop        # list deployments on this machine, choose manually
lwd-tool logs --role cloud
lwd-tool health --role edge --model qwen3.6-27b \
    --edge 76.76.26.180:4 --cloud 76.76.26.187:0-3
```

On a machine still running the hand-installed `a2-transfer` container (no
lwd image yet), install the tool once and keep updating it with git:

```bash
git clone https://github.com/vllm-data-comm/ci-infra /vllm-workspace/ci-infra
python3 -m pip install --no-deps -e /vllm-workspace/ci-infra
```

The image build (`docker/Dockerfile.lwd`, `lwd_image.yml`) copies the tool and
its profiles from the ci-infra checkout and installs them with `--no-deps`, so
tool and vllm code always ship together under the same image tag.

## Verification

- `uv run pytest tests/ -q` — includes argv golden snapshots for the
  two-machine, single-machine and centralized scenarios; the goldens were
  diffed line by line against the issue #18 scripts, and any future diff means
  the generator drifted (test `test_golden.py` header explains this).
- Topology tests reuse the real `gen_lwd_topology.py` from a vllm checkout;
  CI checks one out next to the repo (`test.yml`), elsewhere those tests skip.
- Real-machine bring-up (cloud-first on 187, edge on 180, then `health`) is
  tracked in vllm issue #18.

## Metrics dashboard (`monitor`)

vllm issue #42 (RFC) replaces the hand-pasted `vllm_monitor.sh` with a
subcommand: the four sections of the script, plus per-refresh rates for the
counters and P99 alongside P50.

```bash
lwd-tool monitor                          # http://localhost:9724, 2s refresh
lwd-tool monitor 76.76.26.180:9724 --interval 1
lwd-tool monitor --once | tee panel.txt   # single snapshot, plain text
```

- Sections: 请求状态 (running / waiting / KV cache %), 延迟 (TTFT / TPOT / ITL
  P50 and P99), 吞吐 (prompt / generation tokens, finished requests — totals
  plus rates), 投机解码 (drafts, draft tokens, accepted, accept rate, mean
  accept length including the bonus token, per-position accepted counts).
  Anything the endpoint does not expose renders as `N/A`; a missing spec
  section collapses to `未开启`.
- Quantiles come from the histogram buckets: the first bucket whose cumulative
  count reaches `count*q` (the script's rule); past the last finite bucket
  renders `>max`. TPOT resolves `vllm:request_time_per_output_token_seconds`
  or the legacy `vllm:time_per_output_token_seconds`, whichever the endpoint
  has.
- Aggregation ignores labels: counters and gauges sum over the series of a
  family (data-parallel ranks, `finished_reason`), KV usage is the mean across
  series. The default URL is the edge serve port (`--port-base`, 9724) — the
  edge is the client entry, so running/waiting/TTFT live there.
- Rates divide the counter delta by the actual elapsed time between polls; a
  counter that went backwards (vllm restarted) reads `N/A` for one poll while
  the baseline re-establishes.
- Stdlib only. On a tty the frame repaints in place (ANSI home + clear-below);
  piped output appends one frame per poll. `Ctrl+C` exits cleanly with code 0.
  Loop mode keeps retrying with an `[ERROR]` frame when the endpoint is
  unreachable (the script's behavior); `--once` fails fast instead.
