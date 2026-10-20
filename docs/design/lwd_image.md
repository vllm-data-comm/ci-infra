# LWD Image

Nightly docker images of the LWD stack (the `vllm` and `vllm-ascend` forks), published to `ghcr.io/vllm-data-comm/lwd`. A real machine gets a runnable environment with one `docker pull`, instead of the manual source install in AGENTS.md.

## Background

The official `vllm-ascend` project builds its images (`quay.io/ascend/vllm-ascend`) with GitHub Actions on self-hosted aarch64 runners: `FROM` a CANN base image, then a full source build of vllm plus vllm-ascend, pushed to an internal SWR registry and mirrored to quay.io. That pipeline answers the same need, but we have no self-hosted runners and no SWR account, and a full build takes 2-4 hours on the free 4-core arm runners.

The key observation: the official image installs vllm and vllm-ascend in editable mode from `/vllm-workspace`, so its expensive parts (CANN, torch, torch-npu, triton-ascend, mooncake, flash-linear-attention) can be inherited as-is. Layering our forks' sources on top of a pinned official image and re-running the editable installs rebuilds only what our forks change.

## Scope

This round:

- One layered `Dockerfile.lwd` in ci-infra, one reusable build workflow in ci-infra, one thin caller in `vllm`.
- Two SoC variants: `a2` (910B) and `a3`, both arm64 only — Atlas hosts are aarch64, and the official release images are arm64 only too.
- Triggers: nightly schedule plus manual dispatch.

Not this round:

- A `v*`-tagged release image flow.
- A symmetric caller in `vllm-ascend` (the `vllm` caller can build any combination of refs).
- Nightly smoke tests on a real NPU (needs a runner or a machine hook; tracked with the other deferred lanes).
- An SWR mirror for fast pulls inside China.

## Architecture

```mermaid
flowchart LR
    Cron[nightly cron 03:37 Beijing] --> Caller
    Dispatch[workflow_dispatch] --> Caller

    subgraph product[vllm fork]
        Caller[lwd_image.yml]
    end

    subgraph ci[ci-infra]
        Build[lwd_image.yml<br/>reusable]
        Dockerfile[docker/Dockerfile.lwd]
    end

    subgraph official[official vllm-ascend image]
        Base[quay.io/ascend/vllm-ascend:v0.30.0rc1[-a3]]
    end

    Caller --> Build
    Build -->|checkout| ForkV[vllm fork @ref]
    Build -->|checkout| ForkA[vllm-ascend fork @ref]
    Build --> Dockerfile
    Build -->|docker buildx| Base
    Build -->|push| Ghcr[ghcr.io/vllm-data-comm/lwd]
```

Decisions:

- **Layer on a pinned official release tag, not `nightly-main`.** `nightly-main` changes daily, and the day upstream moves its torch pin, our fork's `torch == 2.13.0` pin would make pip reinstall torch against the base's torch-npu and the build would break for a non-obvious reason.
- **The pinned tag is `v0.30.0rc1` (2026-10-12), not the newer `rc2`.** The base must satisfy both forks at once, and no other public tag does: `v0.30.0rc1` pairs vllm v0.30.0 (torch 2.13, matching the vllm fork) with CANN 9.1.0 (matching the vllm-ascend fork's 2026-09-30 baseline). `rc2` switched the image train to CANN 9.2.0 (2026-10-13), whose opc tool rejects the fork's 9.1.0-era op definitions — first build attempt failed on `CausalConv1d` with exactly that. Older release families are CANN 9.1.0 but pair an older vllm/torch, which breaks the vllm fork's pin the other way.
- **The base tag tracks both forks' pins.** Re-pin it in the same ci-infra PR that changes either the vllm fork's torch pin or the vllm-ascend fork's CANN baseline (e.g. once the vllm-ascend fork syncs to rc2-era upstream, bump the base to `rc2` and re-enable the custom kernels check). The tag lives in two places on purpose: as the `BASE_TAG` default in `docker/Dockerfile.lwd` (for local builds) and in the workflow's `Resolve the build parameters` step (the effective default); both move together.
- **Builds run on `ubuntu-24.04-arm`.** Free for public repositories, native arm64, no QEMU. Docker's data root is moved to the 65 GB `/mnt` disk: the base image alone is ~7 GB and the 14 GB system disk is not enough.
- **No third-party docker actions.** Plain `docker buildx build` / `docker login` steps keep the workflow free of SHA pins to maintain; the only third-party action is `actions/checkout`, pinned as elsewhere in ci-infra.
- **Tags answer two questions: "what is newest" and "what did I run".** See the table below. The rolling tag moves only when the caller sets `nightly: true`, so an ad-hoc dispatch for debugging never silently overwrites `nightly`.

| Tag | Meaning |
| --- | --- |
| `nightly` / `nightly-a3` | rolling, overwritten by each nightly run |
| `nightly-YYYYMMDD[-a3]` | one per day, kept |
| `sha-<vllm short sha>[-a3]` | the exact vllm commit in the image, for reproducing a build |

Expected duration on the 4-core arm runner: roughly 30 minutes to 1.5 hours, dominated by the vllm editable install and the Rust frontend rebuild. The first build also downloads build dependencies that pip's isolation does not reuse.

- **vllm-ascend is overlaid, not replaced, and installed as pure Python.** The base image's prebuilt csrc kernels (`csrc/build`, loaded at runtime) survive the source swap — a full-tree `COPY` would delete them, and the fork's csrc does not build standalone anyway (its CausalConv1d op definition and compile info disagree; identical failure under CANN 9.1.0 and 9.2.0). `COMPILE_CUSTOM_KERNELS=0` keeps the official prebuilt kernels, mirroring how the real-machine bring-up swaps Python sources on top of a working install. Revisit once the fork's csrc builds cleanly (issue #18).

## Runbook

First publish (one org-admin step):

```bash
gh api --method PATCH /orgs/vllm-data-comm/packages/container/lwd -f visibility=public
```

Pull from a real machine (mirror prefixes if ghcr.io is slow):

```bash
docker pull ghcr.io/vllm-data-comm/lwd:nightly
# or: docker pull m.daocloud.io/ghcr.io/vllm-data-comm/lwd:nightly
docker run --rm ghcr.io/vllm-data-comm/lwd:nightly \
  python3 -c "import torch, torch_npu, vllm, vllm_ascend; print('ok')"
```

Build an image for a specific pair of commits while debugging:

1. Actions → LWD Image (`vllm` fork) → Run workflow, fill `vllm_ref` / `vllm_ascend_ref`, keep push on.
2. The run summary records both shas and the base tag; the image lands on `sha-<vllm sha>`.

## Verification

- The first dispatch build's log must show pip not reinstalling torch — that is the direct evidence the base/fork pairing holds.
- `docker buildx imagetools inspect` in the workflow prints the pushed manifest (arm64).
- Import smoke test on a real machine as in the runbook; deeper bring-up validation stays with the bring-up workstream, not the image pipeline.
