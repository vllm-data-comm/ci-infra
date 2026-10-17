# vllm-data-comm CI infrastructure

CI workflows and checks shared by the `vllm-data-comm` forks of vLLM
(`vllm`, `vllm-ascend`). Each product repository holds thin caller
workflows that reference the reusable workflows in `.github/workflows/`.

Design and rules: [docs/design/lwd_ci.md](docs/design/lwd_ci.md).

## Layout

- `lwd_checks/` — the check scripts. Python standard library only, no
  dependencies, runnable locally.
- `.github/workflows/lwd_fast.yml`, `lwd_slow.yml`, `lwd_checklist.yml` —
  reusable workflows called by the product repositories.
- `scripts/disable_inherited_workflows.sh` — disable upstream-inherited
  workflows in a product repository; rerun after every upstream sync.

## Running locally

```bash
uv sync
uv run pytest tests/ -q
uv run python -m lwd_checks.title_check --title "[Bugfix][Lwd] fix xxx" --out /tmp/r.json
```
