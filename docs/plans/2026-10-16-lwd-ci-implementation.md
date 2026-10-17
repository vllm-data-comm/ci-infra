# LWD CI 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 按已批准的设计文档（ci-infra `docs/design/lwd_ci.md`，PR #1）实现 LWD CI：ci-infra 里的检查脚本 + 三个 reusable workflow，vllm / vllm-ascend 里的调用文件，停用继承 workflow，配置分支保护必须检查项。

**架构：** 每项检查有快/慢 × 必须通过/纯粹检查两个属性，定义在 ci-infra 的 catalog；快线慢线各由一个 gate job 汇总；结果 artifact 经各仓的 relay workflow（workflow_run 触发）汇成一条 checklist 评论；fork PR 只读 token 的限制由 relay 用 base 仓的写 token 解决。

**技术栈：** Python 3.12 标准库（检查脚本零依赖）、GitHub Actions（action 按 commit SHA 钉版）、uv、pytest。

**前置条件（需要用户做）：**
- 合并 ci-infra PR #1（设计文档）。实现分支从合完的 main 切出。
- vllm 仓最终接线的 PR 需要 1 个 committer approve（用户不能 self-approve；走 admin 例外或找同事）。

**约定（全任务适用）：**
- 代码里 LWD 前缀一刀切：类 `Lwd*`、函数/模块 `lwd_*`、常量 `LWD_*`/`_LWD_*`（团队规范 7.5 + 用户已确认私有名也要）。
- ci-infra 的 commit 一律 `git commit -s`，message 用 `feat(ci)/test(ci)/chore(ci): ...` 英文祈使句。
- 本地跑测试：`cd C:/Users/root/projects/lwd/ci-infra && uv run --python 3.12 pytest tests/ -q`；lint：`uv run --python 3.12 ruff check lwd_checks tests`。本机网络坑（PyPI 慢）按既有约定加 `UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple`。
- action SHA（已核实）：checkout `3d3c42e5aac5ba805825da76410c181273ba90b1`(v7.0.1)、setup-python `5fda3b95a4ea91299a34e894583c3862153e4b97`(v7.0.0)、upload-artifact `cf430e030ddbb5b0abf93d22962f4752f3646cd9`(v7.0.2)、setup-uv `1c37ad07a6a961277cf70c0d37d6f313000f5884`(v10.3.0)。
- 本机 git 是 2.51，`--filter=tree:0` promisor fetch 已实测可行。

---

## 文件结构

```text
ci-infra/  (分支 feat/lwd-ci)
├── .github/workflows/
│   ├── lwd_fast.yml          reusable：快线 5 个 job + gate
│   ├── lwd_slow.yml          reusable：慢线 3 个 job + gate
│   ├── lwd_checklist.yml     reusable：汇总结果、upsert PR 评论
│   └── test.yml              ci-infra 自己的 CI（ruff + pytest + actionlint）
├── lwd_checks/
│   ├── __init__.py
│   ├── catalog.py            检查目录：id → 轨道/是否必须（唯一改动点）
│   ├── results.py            结果 JSON 读写 + schema 校验 + report CLI
│   ├── scope.py              PR 自身 commit/文件计算（排除上游与 merge）
│   ├── title_check.py        PR 标题
│   ├── commits_check.py      commit 格式 + DCO signoff
│   ├── ast_diff.py           base/head AST 解析与同名匹配
│   ├── standards.py          入参数/函数长度/类名前缀（AST 规则）
│   ├── file_rules.py         新文件路径 / patch 使用
│   ├── pr_meta.py            RFC 关联 / bugfix 无测试
│   ├── junit.py              pytest junitxml → 结果
│   ├── mypy_report.py        mypy 输出 → 结果（advisory 不红）
│   ├── gate.py               轨道 gate（含 no-test 放行慢线）
│   ├── render.py             checklist markdown 渲染（转义/截断）
│   ├── comment.py            relay：API 拉结果、head_sha 绑定、upsert 评论
│   └── discover.py           tests/ 下 lwd 测试目录发现
├── scripts/disable_inherited_workflows.sh
├── tests/                    与模块一一对应的测试
├── pyproject.toml            仅 dev 依赖（pytest/ruff），包本体零依赖
└── docs/plans/2026-10-16-lwd-ci-implementation.md   本计划副本（任务 0 落盘）

vllm/.github/workflows/   (分支 feat/lwd-ci-callers)
├── lwd_fast.yml / lwd_slow.yml / lwd_checklist.yml   调用文件

vllm-ascend/.github/workflows/   (分支 ci/lwd-fast)
├── lwd_fast.yml / lwd_checklist.yml                 （无慢线）
```

---

## 任务 0：脚手架与计划落盘

**文件：** 创建 `pyproject.toml`、`.gitignore`、`README.md`、`docs/plans/2026-10-16-lwd-ci-implementation.md`（本文件的副本）

- [ ] **步骤 1：** 用户确认 PR #1 已合并后：`git switch main && git pull`（SSH 推送方式按记忆 github-push-via-ssh，普通 pull 走 https 也行），`git switch -c feat/lwd-ci`。
- [ ] **步骤 2：** `pyproject.toml`：

```toml
[project]
name = "lwd-checks"
version = "0.1.0"
description = "CI checks shared by vllm-data-comm repositories"
requires-python = ">=3.12"
dependencies = []

[dependency-groups]
dev = ["pytest>=8", "ruff>=0.14"]

[tool.ruff]
line-length = 88
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "B", "I", "UP"]
```

`.gitignore`：`__pycache__/`、`.pytest_cache/`、`.venv/`、`*.pyc`。README 三段：仓库用途、设计文档链接、本地跑法。

- [ ] **步骤 3：** 把本计划文件复制到 `docs/plans/2026-10-16-lwd-ci-implementation.md`。
- [ ] **步骤 4：** `git add -A && git commit -s -m "chore(ci): scaffold the lwd-checks package"`。

## 任务 1：catalog + results

**文件：** 创建 `lwd_checks/__init__.py`（空）、`lwd_checks/catalog.py`、`lwd_checks/results.py`、`tests/test_catalog.py`、`tests/test_results.py`

- [ ] **步骤 1：先写失败测试** `tests/test_catalog.py`：

```python
from lwd_checks.catalog import LWD_CHECK_CATALOG, LwdCheckSpec


def test_catalog_defines_every_designed_check():
    ids = set(LWD_CHECK_CATALOG)
    assert ids == {
        "pr-title", "commits", "pre-commit",
        "param-count", "function-length", "class-prefix",
        "new-file-path", "patch-usage", "rfc-link", "bugfix-tests",
        "lwd-unit-tests", "mypy",
    }


def test_catalog_lanes_and_gating():
    fast_required = {c.check_id for c in LWD_CHECK_CATALOG.values()
                     if c.lane == "fast" and c.gating == "required"}
    assert fast_required == {"pr-title", "commits", "pre-commit"}
    slow = {c.check_id: c.gating for c in LWD_CHECK_CATALOG.values() if c.lane == "slow"}
    assert slow == {"lwd-unit-tests": "required", "mypy": "advisory"}
```

`tests/test_results.py` 覆盖：往返序列化、schema 版本不符抛 `ValueError`、未知 status 抛错、`lwd_exit_code`（required+fail→1，advisory+fail→0，error→1）。

- [ ] **步骤 2：跑测试确认失败**：`uv run --python 3.12 pytest tests/ -q` → ModuleNotFoundError。
- [ ] **步骤 3：实现**。`catalog.py`：

```python
"""Check catalog: the single place defining each check's lane and gating."""
from __future__ import annotations

from dataclasses import dataclass

LWD_LANE_FAST = "fast"
LWD_LANE_SLOW = "slow"
LWD_GATING_REQUIRED = "required"
LWD_GATING_ADVISORY = "advisory"


@dataclass(frozen=True)
class LwdCheckSpec:
    check_id: str
    title: str
    lane: str
    gating: str


LWD_CHECK_CATALOG: dict[str, LwdCheckSpec] = {
    spec.check_id: spec
    for spec in (
        LwdCheckSpec("pr-title", "PR title", LWD_LANE_FAST, LWD_GATING_REQUIRED),
        LwdCheckSpec("commits", "Commits", LWD_LANE_FAST, LWD_GATING_REQUIRED),
        LwdCheckSpec("pre-commit", "pre-commit", LWD_LANE_FAST, LWD_GATING_REQUIRED),
        LwdCheckSpec("param-count", "Parameter count", LWD_LANE_FAST, LWD_GATING_ADVISORY),
        LwdCheckSpec("function-length", "Function length", LWD_LANE_FAST, LWD_GATING_ADVISORY),
        LwdCheckSpec("class-prefix", "Class prefix", LWD_LANE_FAST, LWD_GATING_ADVISORY),
        LwdCheckSpec("new-file-path", "New file path", LWD_LANE_FAST, LWD_GATING_ADVISORY),
        LwdCheckSpec("patch-usage", "Patch usage", LWD_LANE_FAST, LWD_GATING_ADVISORY),
        LwdCheckSpec("rfc-link", "RFC link", LWD_LANE_FAST, LWD_GATING_ADVISORY),
        LwdCheckSpec("bugfix-tests", "Bugfix tests", LWD_LANE_FAST, LWD_GATING_ADVISORY),
        LwdCheckSpec("lwd-unit-tests", "LWD unit tests", LWD_LANE_SLOW, LWD_GATING_REQUIRED),
        LwdCheckSpec("mypy", "mypy", LWD_LANE_SLOW, LWD_GATING_ADVISORY),
    )
}
```

`results.py` 关键接口（实现完整，含 json 读写）：

```python
LWD_RESULTS_SCHEMA = 1
LWD_STATUS_PASS = "pass"; LWD_STATUS_FAIL = "fail"; LWD_STATUS_ERROR = "error"

@dataclass(frozen=True)
class LwdFinding:
    path: str
    line: int | None
    message: str

@dataclass(frozen=True)
class LwdCheckResult:
    check_id: str
    status: str
    summary: str = ""
    findings: tuple[LwdFinding, ...] = ()

def lwd_write_results(path, lane, pr_number, head_sha, results) -> None: ...
def lwd_load_results(path) -> tuple[str, list[LwdCheckResult]]:  # 校验 schema/枚举/catalog id
def lwd_exit_code(result, spec) -> int: ...
```

另加 CLI `python -m lwd_checks.results report --check-id X --status S --summary "..." --out f.json`（workflow 里 pre-commit / junit / mypy 汇报用），用 `argparse` + `if __name__ == "__main__"`。

- [ ] **步骤 4：跑测试确认通过**，`ruff check` 过。
- [ ] **步骤 5：** `git commit -s -m "feat(ci): add the check catalog and result records"`。

## 任务 2：scope（PR 自身范围）

**文件：** 创建 `lwd_checks/scope.py`、`tests/test_scope.py`

- [ ] **步骤 1：失败测试**。测试用 `tmp_path` 建真实 git 仓（`git init -q -b main` + 本地路径当 upstream remote），覆盖 5 个场景，每个一个测试函数：
  1. 自有 commit 被列出（sha、subject、author_email、signoff_emails）；
  2. upstream main 已有的 commit 被排除（`--not upstream/main`）；
  3. merge commit 被跳过；
  4. 纯同步 PR（只含上游 commit 的 merge）→ own_commits 为空、own_files 为空；
  5. 改名：commit 里 `git mv a.py b.py` → own_files 含 `b.py`（status=renamed, old_path=a.py），`a.py` 不在。

数据结构：

```python
@dataclass(frozen=True)
class LwdCommitInfo:
    sha: str; subject: str; author_email: str; signoff_emails: tuple[str, ...]

@dataclass(frozen=True)
class LwdOwnFile:
    path: str; status: str          # added | modified | deleted | renamed
    old_path: str | None = None

@dataclass(frozen=True)
class LwdScope:
    base_sha: str; head_sha: str
    own_commits: tuple[LwdCommitInfo, ...]
    own_files: tuple[LwdOwnFile, ...]
    py_added_lines: int             # 自有 commit 的 numstat 中 *.py 非 tests/ 的新增行和

def lwd_compute_scope(repo_dir, base_sha, head_sha, upstream_ref) -> LwdScope: ...
```

- [ ] **步骤 2：确认失败 → 步骤 3：实现**。核心 git 命令（`subprocess.run`，`check=True`，`-C repo_dir`）：
  - 自有 commit：`git rev-list --no-merges base..head --not upstream_ref`；每个 commit 用 `git show -s --format=%H%n%s%n%ae%n(trailers:key=Signed-off-by,valueonly)` 取字段（email 全部 lowercase 归一）。
  - 自有文件：对每个自有 commit `git diff-tree --no-commit-id --name-status -r -M --root <sha>`，按 path 归并出最终状态（head 处 `git cat-file -e head:path` 判存在 → added/modified/renamed，否则 deleted）。
  - `py_added_lines`：各自有 commit `git show --numstat --format= <sha>`，取 `*.py` 且不在 `tests/` 的 added 列求和。
  - CLI：`python -m lwd_checks.scope --base-sha X --head-sha Y --upstream upstream/main --out scope.json`，json 里含全部字段。
- [ ] **步骤 4：测试通过。步骤 5：** `git commit -s -m "feat(ci): compute the PR's own commits and files"`。

## 任务 3：PR 标题检查

**文件：** `lwd_checks/title_check.py`、`tests/test_title_check.py`

- [ ] **步骤 1：失败测试**（表驱动）：

| 输入 | 结果 |
| --- | --- |
| `[Bugfix][Lwd] fix xxx sync problem` | pass |
| `[CI/Build][Misc][Lwd] redo pipeline` | pass |
| `refactor(lwd): unify config error output`（PR #10 实例） | fail，提示必须是 `[Type]...` 开头 |
| `[Lwd][Bugfix] order is wrong` | fail，第一个标签必须是类型标签 |
| `[Bugfix] no lwd tag` | fail，缺 `[Lwd]` |
| `[Bugfix][Lwd]no-space` | fail，标签后需空格+描述 |

- [ ] **步骤 3：实现**：

```python
_LWD_TITLE_TYPE_TAGS = ("Bugfix", "Feature", "Perf", "Refactor",
                        "CI", "CI/Build", "Test", "Doc", "Docs", "Misc")
_LWD_TITLE_TAG_RE = re.compile(r"^\[[^][]+\]")

def lwd_parse_title_tags(title) -> list[str]: ...   # 剥出开头连续 [...]，无则 []
def lwd_check_title(title) -> LwdCheckResult: ...    # 规则见测试表；失败 summary 聚合原因
```

CLI：`--title` 必填，`--out` 结果 JSON。
- [ ] **步骤 4/5：** 测试通过后 `git commit -s -m "feat(ci): check the PR title format"`。

## 任务 4：commit 格式 + DCO 检查

**文件：** `lwd_checks/commits_check.py`、`tests/test_commits_check.py`

- [ ] **步骤 1：失败测试**（直接构造 `LwdCommitInfo`，不碰 git）：subject 匹配 `feat(lwd): xxx` 8 种 type 全过；`feat(npu): x`、`feat: x`、空 subject fail；subject 含中文/全角标点 fail（覆盖 `：`、`，`）；signoff 缺失 fail 且 summary 给出修复命令 `git rebase --signoff <base>`；signoff email 与 author 大小写不同但相同 → pass；own_commits 为空 → pass。
- [ ] **步骤 3：实现**：

```python
_LWD_COMMIT_TYPES = ("feat", "fix", "perf", "refactor", "docs", "test", "ci", "chore")
_LWD_COMMIT_SUBJECT_RE = re.compile(
    r"^(feat|fix|perf|refactor|docs|test|ci|chore)\(lwd\): \S")
_LWD_CJK_RE = re.compile(r"[\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef\u3000-\u303f]")

def lwd_check_commits(own_commits, base_sha) -> LwdCheckResult: ...
```

每个不合格 commit 一条 finding（line=None，message=`<sha7>: <原因>`）。
- [ ] **步骤 4/5：** 通过后 `git commit -s -m "feat(ci): check commit subjects and DCO signoff"`。

## 任务 5：AST 规则（入参数 / 函数长度 / 类名前缀）

**文件：** `lwd_checks/ast_diff.py`、`lwd_checks/standards.py`、`tests/test_ast_diff.py`、`tests/test_standards.py`

- [ ] **步骤 1：失败测试** `test_ast_diff.py`（纯内存，不碰 git）：
  - `lwd_count_params`：`(self, a, b=1, *args, c, **kw)` → 5（self 不计，*args/**kw 各计 1，posonly/kwonly 逐个计）；模块级函数首参叫 self 不剥离。
  - `lwd_parse_symbols(source)` 返回 `{qualname: LwdFunctionInfo}` / class 集合：嵌套类方法 `A.B.method`、decorator 不计入行数、`end_lineno` 边界。
- [ ] **步骤 2：失败测试** `test_standards.py`（tmp git 仓，base/head 两版文件）：
  - 新函数 6 参 → flag；base 4 参 head 6 参 → flag；base 6 参 head 7 参 → **不** flag（本来就超）；
  - 新函数 120 行 → flag；base 120 行 head 121 行 → 不 flag；
  - 新类 `class Dispatcher` → flag；`class LwdX` / `class _LwdY` → 不 flag；
  - `tests/` 下与 source_roots 外的文件不查；文件在 base 不存在（新增）→ 全部按新算；改名文件跟 qualname 走。
- [ ] **步骤 3：实现**。`ast_diff.py`：

```python
@dataclass(frozen=True)
class LwdFunctionInfo:
    qualname: str; params: int; start_line: int; end_line: int

def lwd_parse_functions(source: str) -> dict[str, LwdFunctionInfo]: ...
def lwd_parse_classes(source: str) -> set[str]: ...
```

`standards.py`：读 scope.json，对 own_files 中 status∈{added,modified,renamed} 且 path 匹配 source_roots（前缀 `vllm/` 或 `vllm_ascend/`）且非 `tests/` 的 `*.py`：head 版 = 工作区文件；base 版 = `git show <该文件最早自有commit>^:<path>`（`git rev-list --reverse base..head --not upstream -- path` 取第一个；新增文件无 base）。三条规则各产一个 `LwdCheckResult`（一条规则一个 check_id，即使全 pass 也产出结果，gate/渲染需要）。CLI：`--scope scope.json --source-root vllm/ --repo-dir . --out results.json`。
- [ ] **步骤 4/5：** 通过后 `git commit -s -m "feat(ci): add AST standards checks for params, length, and class prefix"`。

## 任务 6：文件级规则（新文件路径 / patch 使用）

**文件：** `lwd_checks/file_rules.py`、`tests/test_file_rules.py`

- [ ] **步骤 1：失败测试**：
  - new-file-path：新增 `vllm/v1/lwd_control/x.py`✓、`tools/gen_lwd_topology.py`✓（文件名含 lwd）、`tests/config/lwd/`✓、新增 `vllm/foo.py` → flag（路径无任何一段含 lwd，大小写不敏感，按路径段匹配：目录名或文件名含子串 "lwd" 即算）；deleted 文件不查；renamed 且新路径含 lwd → 不 flag，不含 → flag。
  - patch-usage：own_files 有 `vllm_ascend/patch/foo.py` → flag；source_roots 内自有新增行出现 `torch.npu.is_available = ...`（根名在 import 名单里且目标带点）→ flag；`setattr(vllm.config, ...)` → flag；`mock.patch(` / `patch.object(` → flag；私有局部变量赋值 `x.y = 1`（x 非 import）→ 不 flag；只在 base 有、head 未新增的行 → 不 flag。
- [ ] **步骤 3：实现**：new-file-path 纯路径判断。patch-usage：对该文件 head 版 parse imports（`import a.b`→`a`；`from a import b`→`b`），AST 找 `Assign/AnnAssign` 的 target 为 Attribute 链且根名∈imports、`Call(func=Name('setattr'))` 首参根名∈imports；行号限定在该文件自有 commit 的新增行集合（`git show <commit> --unified=0` 解析 `@@` 与 `+` 行）。mock.patch 用 `ast.Call/Attribute` 的 dotted name 匹配 `mock.patch`、`patch.object`、`unittest.mock`。产 2 个 check 的结果。CLI 并入 `--scope --source-root --repo-dir --out`。
- [ ] **步骤 4/5：** `git commit -s -m "feat(ci): check new file paths and patch usage"`。

## 任务 7：PR 元数据规则（RFC 关联 / bugfix 无测试）

**文件：** `lwd_checks/pr_meta.py`、`tests/test_pr_meta.py`

- [ ] **步骤 1：失败测试**：
  - rfc-link：`py_added_lines=600`、body 无 `#N`/issue 链接 → flag（summary 附"本 PR 是否改了 docs/: 是/否"，来自 own_files）；body 有 `#8` 且 issue 标题以 `[RFC]` 开头 → 不 flag（issue 标题获取函数注入 fake）；`#8` 标题非 RFC → flag；`https://github.com/vllm-data-comm/vllm/issues/8` 同 `#8`；`py_added_lines=499` → 不 flag 且结果 pass。
  - bugfix-tests：title 首类型标签 `Bugfix` 且 own_files 无 `tests/` 前缀 → flag；own_files 有 → pass；title 非 Bugfix → pass。
- [ ] **步骤 3：实现**：

```python
def lwd_fetch_issue_title(repo: str, number: int, token: str | None) -> str | None:
    # urllib.request 打 {api}/repos/{repo}/issues/{n}，404/403 返回 None（不抛）
def lwd_referenced_issues(body: str) -> list[tuple[str, int]]:  # (#N) 与跨仓 URL 两类
def lwd_check_rfc_link(scope, title, body, fetch) -> LwdCheckResult
def lwd_check_bugfix_tests(scope, title) -> LwdCheckResult
```

CLI：`--scope --title-file --body-file --repo vllm-data-comm/vllm --token-env GITHUB_TOKEN --out`。issue 标题判断大小写不敏感、允许 `[RFC]`/`[rfc]`。
- [ ] **步骤 4/5：** `git commit -s -m "feat(ci): check RFC links and bugfix test coverage"`。

## 任务 8：渲染 checklist 评论

**文件：** `lwd_checks/render.py`、`tests/test_render.py`

- [ ] **步骤 1：失败测试**：golden 输出（fast 12 项中 fast 的 10 项按 catalog 顺序、slow 段）；finding 渲染成两空格缩进 bullet `` `path:line` message``；转义：`` ` ``→`'`、`|`→`\|`、`<`/`>`/`&`→HTML 实体、`@`→`` `@` ``；单 check findings 超 50 截断加 `… (+N more)`；总长超 60000 截断；某轨道无结果 → `**Slow lane** · <sha>` 下显示 `- 慢线未运行`（pending）；slow 被 no-test 放行 → 显示 `no-test 标签放行`。
- [ ] **步骤 3：实现**：

```python
LWD_COMMENT_MARKER = "<!-- lwd-ci-checklist -->"

def lwd_render_checklist(fast_results, slow_results, head_sha, slow_bypassed) -> str:
    # 首行 marker；fast/slow 两段；required/advisory 后缀；
    # 缺的 check（catalog 有、结果无）显示 "did not run" 且不勾选
```

- [ ] **步骤 4/5：** `git commit -s -m "feat(ci): render the checklist comment"`。

## 任务 9：gate

**文件：** `lwd_checks/gate.py`、`tests/test_gate.py`

- [ ] **步骤 1：失败测试**：fast 三个 required 全 pass → 0；缺 `pre-commit` 结果 → 1；required fail → 1；advisory fail → 0；advisory 缺 → 0；结果里出现 catalog 没有 的 check_id → 1（防漂移）；`lane="slow"` + no_test=True → 0 且写出 bypass 结果；slow required fail + no_test=False → 1。
- [ ] **步骤 3：实现**：`def lwd_gate(lane, results, no_test=False) -> tuple[int, LwdCheckResult]`；CLI `--lane --results-dir <dir> --no-test --out`（写出 gate 自身的结果 JSON 供渲染）。
- [ ] **步骤 4/5：** `git commit -s -m "feat(ci): add lane gates with the no-test bypass"`。

## 任务 10：junit / mypy 汇报 + 测试目录发现

**文件：** `lwd_checks/junit.py`、`lwd_checks/mypy_report.py`、`lwd_checks/discover.py`、对应 tests

- [ ] **步骤 1：失败测试**：junit——fixture xml（stdlib `xml.etree` 解析）→ passed/failed/skipped 计数与 summary 字符串 `42 passed, 0 failed, 3 skipped`，失败用例进 findings（name+message 截断）；mypy——样例输出行 `f.py:57: error: incompatible ... [return-value]` → findings，无 error 行 → pass；discover——tmp 目录树 `{tests/config/lwd, tests/v1/lwd_control, tests/models}` → 前两个（最顶层匹配优先），空/不存在 → []。
- [ ] **步骤 3：实现** 三个小模块 + CLI（`junit --xml f --check-id lwd-unit-tests --out`；`mypy_report --log-file f --out`；`discover tests`）。
- [ ] **步骤 4/5：** `git commit -s -m "feat(ci): report unit tests, mypy, and discover lwd test dirs"`。

## 任务 11：relay（comment.py）

**文件：** `lwd_checks/comment.py`、`tests/test_comment.py`

- [ ] **步骤 1：失败测试**（API 层注入 fake transport，函数签名 `lwd_run_relay(api, env)`）：
  1. run 对应 PR head 已前进（PR.head.sha ≠ run.head_sha）→ 直接返回，不调评论接口；
  2. PR 找不到（`/commits/{sha}/pulls` 里没有 base 为本仓的 open PR）→ 返回；
  3. 两轨结果齐 → 渲染后 POST `/issues/{n}/comments`（已有带 marker 评论 → PATCH `/issues/comments/{id}`）；
  4. 慢线 run 不存在（vllm-ascend 场景只配 fast）→ 只渲染 fast；
  5. artifact JSON 损坏 → 该轨道显示 "did not run"，不崩；
  6. PR 带 `no-test` 标签 → slow 段显示放行；
  7. 渲染文本中出现 finding 的转义（复用 render）。
- [ ] **步骤 3：实现** 流程（全部 urllib + env `GITHUB_REPOSITORY`/`GITHUB_API_URL`/`LWD_GITHUB_TOKEN`/`LWD_WORKFLOW_RUN_ID`）：
  1. `GET /repos/{repo}/actions/runs/{id}` → head_sha、head_branch、head_repository；
  2. `GET /repos/{repo}/commits/{head_sha}/pulls` → 取 `state=="open"` 且 `base.repo.full_name=={repo}` 的第一条；其 `head.sha != head_sha` → 过期退出；
  3. 对 `lwd_fast.yml` / `lwd_slow.yml` 各 `GET /repos/{repo}/actions/workflows/{file}/runs?head_sha={sha}&event=pull_request` 取最新一条；`GET .../artifacts` 过滤 `name.startswith("lwd-results-")`，逐个下载 zip（内存 `zipfile`）读 results JSON，`lwd_load_results` 校验；
  4. `GET /repos/{repo}/pulls/{n}` 取 labels（no-test 判断）；
  5. `GET /repos/{repo}/issues/{n}/comments?per_page=100` 找 marker → POST/PATCH。
- [ ] **步骤 4/5：** `git commit -s -m "feat(ci): relay lane results into the PR checklist comment"`。

## 任务 12：ci-infra 的 reusable workflow + 自身 CI

**文件：** `.github/workflows/lwd_fast.yml`、`lwd_slow.yml`、`lwd_checklist.yml`、`test.yml`

- [ ] **步骤 1：** 写 `test.yml`（先有自身门禁）：

```yaml
name: test
on:
  pull_request: {branches: [main]}
  push: {branches: [main]}
permissions: {contents: read}
jobs:
  tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with: {python-version: "3.12"}
      - run: pip install uv && uv venv && uv pip install -e . --group dev
      - run: .venv/bin/ruff check . && .venv/bin/ruff format --check .
      - run: .venv/bin/pytest tests/ -q
      - name: actionlint
        run: |
          curl -sSfL https://raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash | bash
          ./actionlint -color
```

- [ ] **步骤 2：** 写 `lwd_fast.yml`（要点，写全）：

```yaml
name: LWD Fast
on:
  workflow_call:
    inputs:
      upstream_repo: {type: string, required: true}
      source_roots: {type: string, required: true}   # "vllm/" 或 "vllm_ascend/"
      pre_commit_skip: {type: string, default: ""}
permissions: {contents: read}
jobs:
  pr-title:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@<sha> with: {repository: vllm-data-comm/ci-infra, ref: main, sparse-checkout: lwd_checks}
      - uses: actions/setup-python@<sha> with: {python-version: "3.12"}
      - run: python -m lwd_checks.title_check --title "${{ github.event.pull_request.title }}" --out r.json
        env: {PYTHONPATH: ${{ github.workspace }}}
      # exit code 由 lwd_exit_code 决定(required)；upload-artifact name: lwd-results-fast-pr-title, path: r.json
  commits: ...   # checkout 本仓 fetch-depth: 0 → fetch upstream(--filter=tree:0) → scope → commits_check → artifact lwd-results-fast-commits
  pre-commit: ...  # 同上取 scope；cache PRE_COMMIT_HOME(key=hashFiles('.pre-commit-config.yaml'))；uv 装 pre-commit；SKIP=${{ inputs.pre_commit_skip }} pre-commit run --files <own files> --show-diff-on-failure；结果用 `python -m lwd_checks.results report --check-id pre-commit --status pass|fail` 写出
  standards: ...   # checkout + scope → standards.py + file_rules.py + pr_meta.py(传 PR title/body、GITHUB_TOKEN) 合并写出 → artifact lwd-results-fast-standards（一个 artifact 含多个 check 的 JSON，文件名=check_id.json）
  lwd-fast-gate:
    needs: [pr-title, commits, pre-commit, standards]
    if: always()
    steps: download-artifact(pattern: lwd-results-fast-*) → python -m lwd_checks.gate --lane fast --results-dir . → upload lwd-results-fast-lwd-fast-gate
```

多 check 的 artifact 统一约定：一个 job 一个 artifact `lwd-results-<lane>-<job>`，zip 内每 check 一个 `<check_id>.json`；`lwd_load_results` 兼容单文件/多文件（实现时把 `results.py` 的载入入口做成目录式即可，测试补一个多文件目录用例）。
- [ ] **步骤 3：** 写 `lwd_slow.yml`：`lwd-unit-tests`（checkout fetch-depth: 1；`uv pip install torch --index-url https://download.pytorch.org/whl/cpu` → `uv pip install -r requirements/common.txt pytest`；`for d in $(python -m lwd_checks.discover tests)` 循环 `pytest "$d" --confcutdir="$d" -q --junitxml junit-$(basename $d).xml`；junit.py 汇总）+ `mypy`（fetch-depth: 0 + scope；pre-commit 缓存；`SKIP= pre-commit run --hook-stage manual mypy-3.12 --files <own .py>`，`set +e` 收集输出到文件，`mypy_report` 汇总，**exit 0**，advisory 不红）+ `lwd-slow-gate`（labels 步骤：`gh pr view --json labels -q '[.labels[].name] | index("no-test") != null'` 决定 `--no-test`）。
- [ ] **步骤 4：** 写 `lwd_checklist.yml`（reusable，`permissions: {pull-requests: write, actions: read}`；checkout ci-infra sparse + setup-python；env 传 `LWD_WORKFLOW_RUN_ID: ${{ github.event.workflow_run.id }}` 等运行 `python -m lwd_checks.comment`）。
- [ ] **步骤 5：** 本地 `actionlint` 跑不了（go.dev 被墙），推分支让 test.yml 的 actionlint 步骤验证；`uv run pytest` 全绿。
- [ ] **步骤 6：** `git commit -s -m "feat(ci): add reusable fast, slow, and checklist workflows"`。

## 任务 13：停用脚本 + 标签

**文件：** `scripts/disable_inherited_workflows.sh`

- [ ] **步骤 1：**

```bash
#!/usr/bin/env bash
# Disable every workflow not named lwd_*. Run after each upstream sync.
set -euo pipefail
repo=${1:?usage: disable_inherited_workflows.sh <owner/repo>}
gh api "repos/$repo/actions/workflows" --paginate --jq \
  '.[] | select(.state == "active") | "\(.id) \(.path)"' |
while read -r id path; do
  case "${path##*/}" in lwd_*) continue ;; esac
  echo "disabling $repo $path"
  gh api -X PUT "repos/$repo/actions/workflows/$id/disable" >/dev/null
done
```

- [ ] **步骤 2：** 本地干跑验证 jq 输出正确（不实际 disable）。
- [ ] **步骤 3：** `git commit -s -m "feat(ci): add the inherited-workflow disable script"`。

## 任务 14：两仓接线 PR

**文件：** vllm 分支 `feat/lwd-ci-callers`：`.github/workflows/lwd_fast.yml`、`lwd_slow.yml`、`lwd_checklist.yml`；vllm-ascend 分支 `ci/lwd-fast`：`lwd_fast.yml`、`lwd_checklist.yml`

- [ ] **步骤 1：** vllm 的 `lwd_fast.yml`（其余两个同构）：

```yaml
name: LWD Fast
on:
  pull_request:
    branches: [main]
    types: [opened, synchronize, reopened, edited]
concurrency:
  group: lwd-fast-${{ github.event.pull_request.number }}
  cancel-in-progress: true
permissions: {contents: read}
jobs:
  lwd-fast:
    uses: vllm-data-comm/ci-infra/.github/workflows/lwd_fast.yml@main
    with:
      upstream_repo: vllm-project/vllm
      source_roots: vllm/
      pre_commit_skip: mypy-3.10
```

`lwd_slow.yml`：types 无 edited，group `lwd-slow-<n>`，uses `lwd_slow.yml@main`。`lwd_checklist.yml`：

```yaml
name: LWD Checklist
on:
  workflow_run:
    workflows: [LWD Fast, LWD Slow]
    types: [completed]
concurrency:
  group: lwd-checklist-${{ github.event.workflow_run.head_repository.full_name }}/${{ github.event.workflow_run.head_branch }}
  cancel-in-progress: true
permissions: {pull-requests: write, actions: read}
jobs:
  comment:
    uses: vllm-data-comm/ci-infra/.github/workflows/lwd_checklist.yml@main
    secrets: inherit   # 无自定义 secret，仅默认 GITHUB_TOKEN 生效所需
```

vllm-ascend：`upstream_repo: vllm-project/vllm-ascend`、`source_roots: vllm_ascend/`、`pre_commit_skip: ""`，无 lwd_slow.yml，checklist 只监听 `[LWD Fast]`。
- [ ] **步骤 2：** commit message：vllm `ci(lwd): call the shared fast, slow, and checklist workflows`；ascend 同式。注意两仓 pre-commit 本机跑法（SKIP=actionlint,update-dockerfile-graph + 清华镜像）。
- [ ] **步骤 3：** 开 PR（标题 `[CI][Lwd] ...`，commit 带 signoff），自跑自证：PR 上应出现 LWD Fast / LWD Slow 两条 check；人工核对 checklist 评论要等合入后才出现（workflow_run 限制，设计文档已写）。
- [ ] **步骤 4：** 合入（vllm 需 committer approve 或用户走 admin 例外：`enforce_admins` DELETE → merge → POST 恢复，见记忆 org-governance-committers）。

## 任务 15：上线收尾

- [ ] **步骤 1：** 跑 `scripts/disable_inherited_workflows.sh vllm-data-comm/vllm` 与 `.../vllm-ascend`；两仓 `gh label create no-test --color B60205 --description "Skip the slow lane (unit tests, mypy)"`。
- [ ] **步骤 2：** 等一个真实 PR 或重跑接线 PR 的 check，用 `gh api repos/<repo>/commits/<sha>/check-runs --jq '.check_runs[].name'` 读准确 context 名（预期形如 `lwd-fast / lwd-fast-gate`、`lwd-slow / lwd-slow-gate`）。
- [ ] **步骤 3：** 配置必须检查（PUT 会整体替换保护配置，带上现有 review 块——与当前 main 保护完全一致，仅加 required_status_checks）：

```bash
gh api -X PUT repos/vllm-data-comm/vllm/branches/main/protection --input - <<'EOF'
{"required_status_checks": {"strict": false, "contexts": ["<fast-gate-name>", "<slow-gate-name>"]},
 "enforce_admins": true,
 "required_pull_request_reviews": {"dismiss_stale_reviews": true, "require_code_owner_reviews": true, "required_approving_review_count": 1},
 "restrictions": null, "allow_force_pushes": false, "allow_deletions": false}
EOF
```

vllm-ascend 只填 fast gate；ci-infra 填 test.yml 的 job 名（首个 run 后读）。改完三仓对照验证（vllm/vllm-ascend 其余项仍一致）。
- [ ] **步骤 4：** AGENTS.md 加指针（两仓，插在 `TEAM STANDARDS (END)` 标记之后、开发工作流章节之前，**绝不改规范区内**）：4-6 行中文——CI 检查在 `vllm-data-comm/ci-infra`（设计文档 `docs/design/lwd_ci.md`）；快/慢线与必须/纯粹两属性；`no-test` 只放行慢线；同步上游后重跑 disable 脚本；本地跑法 `cd ci-infra && uv run --python 3.12 python -m lwd_checks.<module> --help`。
- [ ] **步骤 5：** 按 AGENTS.md §9 列出全部 AI 编写的测试文件清单交用户审查。

## 验证（端到端）

1. **单元**：`uv run --python 3.12 pytest tests/ -q` 全绿；`ruff check` 干净。
2. **ci-infra PR**：test.yml 三步（ruff/pytest/actionlint）绿后合入。
3. **接线 PR 自证**：vllm PR 上 LWD Fast 的 pr-title/commits/pre-commit/standards job 都出现；故意推一个无 signoff 的 commit 应红 commits job；`gh pr edit --title "bad title"` 应红 pr-title。
4. **合入后**：新开测试 PR → 出现 checklist 评论；推新 commit → 评论原地更新；`gh pr edit --body` 加 `#N` → rfc-link 项变化；打 `no-test` → 重跑 slow gate 变绿、评论 slow 段显示放行。
5. **首次运行待确认清单**（设计文档"To verify"节）：单测真 torch 下是否全过、ascend 缺工具的 hook、mypy 噪声量、快线时长、context 名。

## 已知风险（执行时按此处理）

- 设计文档是唯一规格来源；本计划与其冲突时以文档为准并回报用户。
- vllm 接线 PR 无法 self-approve，提前告知用户安排。
- GH 运行环境与本地差异（`/bin/bash` 存在、gitleaks 脚本可下载）不适用本机，别在 Windows 本机预判这些 hook 的行为。
