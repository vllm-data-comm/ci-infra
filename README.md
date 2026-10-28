# vllm-data-comm CI infrastructure

CI workflows and checks shared by the `vllm-data-comm` forks of vLLM
(`vllm`, `vllm-ascend`). Each product repository holds thin caller
workflows that reference the reusable workflows in `.github/workflows/`.

Design and rules: [docs/design/lwd_ci.md](docs/design/lwd_ci.md).

## Layout

- `lwd_checks/` — the check scripts. Python standard library only, no
  dependencies, runnable locally.
- `lwd_tool/` — `lwd-tool`, the LWD launch CLI (edge / cloud / centralized),
  shipped inside the lwd docker image. Design and usage:
  [docs/design/lwd_tool.md](docs/design/lwd_tool.md).
- `.github/workflows/lwd_fast.yml`, `lwd_checklist.yml` —
  reusable workflows called by the product repositories.
- `.github/workflows/lwd_image.yml` — nightly lwd image build (includes
  `lwd-tool`), called by the product repositories.
- `scripts/lwd_build_image.sh`, `scripts/lwd_registry_gc.sh`,
  `docker/registry/` — the internal image pipeline: build on the
  intranet builder, serve from an intranet registry. Design and deploy
  manual: [docs/design/lwd_image_internal.md](docs/design/lwd_image_internal.md).
- `scripts/disable_inherited_workflows.sh` — disable upstream-inherited
  workflows in a product repository; rerun after every upstream sync.

## Running locally

```bash
uv sync
uv run pytest tests/ -q
uv run python -m lwd_checks.title_check --title "[Bugfix][Lwd] fix xxx" --out /tmp/r.json
```

`lwd-tool` itself runs inside the target machines' containers (it probes
local NICs and searches weight roots); its derivation is exercised on any
machine by `tests/lwd_tool/test_golden.py`.
