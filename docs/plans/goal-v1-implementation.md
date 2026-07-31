# Goal 長任務：RemaGraph v1 全量實作

> **用途**：複製下方「Goal 目標句」與「Plan 本體」啟動 Grok Goal（或等價 `/goal`），讓 agent **以協調者角色**協調完成 v1 開發。  
> **SOT**：[`implementation-plan-v1.md`](./implementation-plan-v1.md)、[`DESIGN.md`](../../DESIGN.md)、[`docs/design/`](../design/)、[`docs/governance/checklist.md`](../governance/checklist.md)  
> **現況**（2026-07-21 收斂）：**v1 Goal 已完成**；證據見 [`docs/reviews/v1-closeout-status.md`](../reviews/v1-closeout-status.md)。下文 Goal／Plan 本體保留為歷史可重跑紀錄；task checklist 已勾選。

---

## 一、Goal 目標句（貼進 Goal 的 Objective）

```text
以協調者角色完成 RemaGraph v1 實作（stdio MCP 三 tool + SQLite/FTS5/trigram + 仲裁/去重/audit + 測試/CI/治理 P0），嚴格依 docs/plans/implementation-plan-v1.md 的 WU-0～WU-10 與 docs/governance/checklist.md 執行。

硬性角色（orchestrator 派工紀律）：協調者本人禁止親自撰寫/修改功能程式碼與設計文件正文；禁止用子代理當作規避此限制的手段；禁止手挑模型。實作/改檔一律派工給執行代理；派工前必須依專案路由政策選定 tier／執行對象，記錄選擇理由。每完成一個功能 WU 必須再派一次異質審查（審查代理的 (model_family, tier) 至少一維不同於實作者；不可則 escalation 不得標完成）。派工預設背景執行，不搶佔協調者自身視窗。

技術凍結：stdio 優先；potion-multilingual-128M；cosine≥0.90；FTS5 tokenize=trigram；handoff_note 僅 task_handoff；top_k/limit 預設20最大100；has_more=top_k+1 演算法；EMBEDDING_DIM 實測 assert；冒煙必須 REMAGRAPH_STATE_DIR/tmp_path，禁止寫入 ~/.local/state/remagraph/。

驗收：pytest 全綠；coverage≥80；冒煙 §11.3 全綠且進 CI（smoke→lint→test）；gitleaks 乾淨；pip-audit 無未處理 HIGH/CRITICAL；mutmut 對 arbitration+dedup 目標≥70% 或 PR 說明；docs/governance/checklist.md 的 P0 無未解釋的 [ ]；三 tool 可 stdio 使用；Audit Contract 可驗證。成功判定必須驗實際產出內容，不可只看 exit 0。

交付：定期 commit；push 到 origin/main（private aiken884/RemaGraph）；排除 .omo/ 與 secrets。禁止自動 PyPI publish。禁止耦合任何具體外部指揮／治理系統。

全程台灣繁體中文溝通。完成後在 {SCRATCH}/ 留下 git/pytest/smoke/coverage 證據，並回報各 WU 狀態與 route() 派工紀錄摘要。
```

---

## 二、Plan 本體（Goal harness 用；可整段貼上或讓 Goal 以此生成 plan.md）

```markdown
# Plan: RemaGraph v1 full implementation (orchestrated multi-agent build-out)

## Goal kind
code-change

## Acceptance criteria
1. All work units WU-0 through WU-10 in `docs/plans/implementation-plan-v1.md` are implemented and verified; frozen decisions in plan §2 and `DESIGN.md` are not violated without an ADR under `docs/decisions/`.
2. Three MCP tools work over **stdio**: `remagraph_store`, `remagraph_search`, `remagraph_status`, per DESIGN + D03 contracts (including `has_more`, supersede/invalidate, arbitration reason codes).
3. Quality gates pass: ruff; pytest all green; `pytest --cov=src/remagraph --cov-fail-under=80`; smoke suite (§11.3) green with **temp state only** (`REMAGRAPH_STATE_DIR` / pytest `tmp_path`); gitleaks clean; pip-audit no unfixed HIGH/CRITICAL; SQLite ≥3.38 + FTS5 trigram gate green; mutmut on arbitration+dedup target ≥70% or documented PR exception.
4. Governance: `docs/governance/checklist.md` P0 items have no unexplained `[ ]`; every feature WU has recorded implementer + adversarial reviewer with distinct `(model_family, tier)` (or escalated); adversarial findings tracked to close or explicit defer.
5. Git: changes committed with meaningful messages (DCO/`Orchestrated-by` style as project practice); pushed to `origin/main` of private `aiken884/RemaGraph`; no secrets, no `.omo/`.
6. **Orchestration integrity**: no functional code authored by the orchestrating session itself; dispatches followed the project's routing policy; no model hand-picking; parallel-by-default where dependencies allow.

## Verification plan
1. `gating`: `git fetch` + `git rev-parse HEAD` == `origin/main` (or document unpushed only if push blocked); `git status` clean except allowed junk; log shows implementation commits.
2. `gating`: `rg NotImplementedError src/remagraph` — core modules must NOT still be bare NotImplementedError stubs for store/search/server/arbitration/dedup/audit/db (models may remain pure schema).
3. `gating`: Run smoke tests with `REMAGRAPH_STATE_DIR` under tmp; assert store→db readable, search Chinese hit, status dedupe, audit line exists under temp path only (not writing production `~/.local/state/remagraph` as test default).
4. `gating`: `pytest` full suite + coverage ≥80; capture under `{SCRATCH}/pytest.txt` and `{SCRATCH}/coverage.txt`.
5. `gating`: Confirm CI config runs smoke→lint→test (and frozen lock sync if uv); trigram gate present; capture CI green or local equivalent proof under `{SCRATCH}/`.
6. `gating`: Read `docs/governance/checklist.md` P0 section — no bare open blockers for v1.
7. `gating`: Spot-check freeze: README/DESIGN say stdio; no `potion-base-8M` as selected model; cosine 0.90; handoff_note only task_handoff.
8. `evidence`: `{SCRATCH}/dispatch-log.md` summarizing each route() decision (task_id, tier, why) and adversarial pairs; `{SCRATCH}/wu-status.md` checklist WU-0..10.

## Non-goals
- PyPI/npm publish, public repo flip, production deploy
- Unix socket daemon as default transport (roadmap only)
- sqlite-vec semantic search, audit rotation, multi-process shared DB
- Cross-task_id supersede, bidirectional invalidate
- Calibrating dedup threshold with production Chinese corpus beyond 0.90 baseline
- Implementing integration with, or naming, any specific external orchestration/governance system
- Orchestrator personally editing `src/**` feature code or rewriting DESIGN.md architecture
- Using subagents as a workaround to bypass the dispatch-to-fleet requirement

## Assumed scope
- Repo: `/Users/aikenlin/Projects/RemaGraph` (or workspace root RemaGraph)
- Plan: `docs/plans/implementation-plan-v1.md` (latest on main, includes governance §11 and review fixes)
- Design: `DESIGN.md`, `docs/design/00`–`05`, audit contract `docs/audit.md`
- Fleet: background-dispatched agent sessions; model/tier selected via the project's routing policy + `config/fleet.yaml`
- State tests: always injectable state dir; production path only for optional manual smoke outside CI

## Implementation approach
Act as **orchestrator** only: decompose, route work via the project's dispatch policy, dispatch fleet in parallel (worktree if file conflicts), monitor, gate, adversarial re-dispatch, integrate reports, commit/push.

Dependency order (critical path):
WU-0 → WU-1 → WU-2 → WU-4 → WU-5 → WU-8 → WU-9 → WU-10
Parallel after WU-1: WU-3 ∥ (WU-2→WU-4); after WU-5: WU-6 ∥ WU-7 then join at WU-8.

Per feature WU loop:
1. route(implement) → fleet implements + tests for that WU
2. Orchestrator verifies content assertions (not exit code only)
3. route(review/audit) with heterogeneous (model_family, tier) → fleet adversarial review
4. Fix loop max 3 then escalate to human
5. Update governance checklist + dispatch log; commit

Do not skip gates to “save time”. Prefer multiple fleet instances over orchestrator coding.

## Task checklist
- [x] Bootstrap: re-read plan §0–§2 + governance checklist; confirm git clean baseline; capture `{SCRATCH}/baseline.txt`
- [x] WU-0 工程基線: pin deps, entrypoint, ruff, pre-commit, .env.example, uv.lock frozen CI, SQLite≥3.38 + trigram gate tests; route()+fleet; adversarial optional for pure config
- [x] WU-1 models TDD: Memory/Store/Search schemas; test_models; route()+fleet; adversarial
- [x] WU-2 db: schema/triggers/trigram/WAL/permissions/idempotent init; route()+fleet; adversarial
- [x] WU-3 arbitration cheap rules + uppercase agent_id reject; route()+fleet; adversarial
- [x] WU-4 dedup model2vec + EMBEDDING_DIM assert + 0.90 + 2000 cap + fail-fast; route()+fleet; adversarial (high risk)
- [x] WU-5 store + supersede + invalidate + transaction; route()+fleet; adversarial
- [x] WU-6 search/status has_more + Chinese baseline + short query behavior; route()+fleet; adversarial
- [x] WU-7 audit after commit only + temp-safe tests; route()+fleet; adversarial
- [x] WU-8 MCP stdio server three tools; route()+fleet; adversarial
- [x] WU-9 CI smoke→lint→test+cov+mutmut track; close P0-3; route()+fleet as needed
- [x] WU-10 README stdio config, CHANGELOG Unreleased, checklist P0 closeout; route()+fleet
- [x] Final: full verification plan; push main; write dispatch/closeout 紀錄（`docs/reviews/v1-adversarial-dispatch-summary.md`、`docs/reviews/v1-closeout-status.md`）；report to user

## Risks / Contradictions
- Orchestrating agent may try to code directly — **hard stop**; re-dispatch fleet
- route() may return no_candidate / same family only — escalate or fail_up; never fake heterogeneity
- model2vec download / offline CI — use mocks + HF_HUB_OFFLINE strategy per plan §7; fail-fast tool errors not server crash
- FTS5 trigram CJK recall — record ≥3 Chinese queries baseline; short queries explicit behavior
- File conflicts under parallel fleet — use git worktree
- Push/auth failure — capture error; do not claim remote updated
- Do not treat plan APPROVE as license to skip human release for PyPI
```

---

## 三、建議啟動方式

1. 工作目錄切到 `~/Projects/RemaGraph`。  
2. 確認已 pull 最新 `main`（含計畫與 `uv.lock`）。  
3. 開 **Goal**，Objective 貼 **§一** 全文。  
4. 若 Goal 要求 plan 檔：把 **§二** 的 markdown 本體存成 session plan，或附言「Plan SOT 見 `docs/plans/goal-v1-implementation.md` §二」。  
5. 可選加一句授權（你已決定開工時）：  
   `本 Goal 即為人類對 v1 實作的明確同意；仍禁止 PyPI publish。`

---

## 四、協調者執行備忘（給跑 Goal 的 agent）

| 可以 | 不可以 |
|------|--------|
| 依路由政策派工給背景 fleet agent | 自己改 `src/**` 功能 |
| 拆 WU、並行、重派、驗收 | `spawn_subagent` 當實作主力 |
| 跑測試／讀 diff 做 Gate | 手寫「我覺得用 claude」 |
| commit／push（依 Goal 授權） | 跳過對抗審查標完成 |
| 更新 checklist 狀態（或派艦隊更新） | 污染生產 state 做冒煙 |

**route() 提示（非手挑，僅 TaskSpec 形狀）**：

- implement 清晰：`task_type=implement`, sensitivity `S0`–`S1`, caps 含 `code`  
- 核心／安全：`S1`, caps `code`+`review` 視需要  
- 對抗審查：`task_type=review` 或 `audit`, sensitivity `S1`+, 記錄 implementer_family 供相關風險  

實際 tier 以 `route()` 回傳為準。

---

## 五、完成時你應看到的結果

- 本地／CI：smoke + pytest + cov≥80  
- 三 tool stdio 可用  
- `docs/governance/checklist.md` P0 收斂  
- `origin/main` 含實作 commit  
- 無自動 PyPI  
- 協調者可交出 dispatch／WU 狀態摘要  

---

## 修訂

| 日期 | 說明 |
|------|------|
| 2026-07-21 | 初版：對齊 implementation-plan v1.2 + 審查修正 + orchestrator 派工紀律 |
