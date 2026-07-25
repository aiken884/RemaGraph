# SPDX-License-Identifier: Apache-2.0
"""Regression tests for BUG 2 (P1): 跨專案 fan-out cap 可設定化 + 「已截斷」的
明確訊號（PPLX 架構審查共識）。

背景：search._cross_project_fanout() 原本用寫死的 _CROSS_PROJECT_FANOUT_CAP=20
上限，超過時只在 SearchResponse.cross_project_fanout_capped 標記 True，但
CLI exit code 恆為 0、回應本身沒有任何『總共有多少候選、實際查了幾個、跳過
幾個』的量化資訊 —— 只看 exit code 或只看 results 陣列的呼叫端會誤判為
「沒有記憶」而非「搜尋不完整」。

本檔驗證 PPLX 共識的完整落地：
1. cap 預設值由 20 提高為 50（search._CROSS_PROJECT_FANOUT_CAP）。
2. cap 可由呼叫端明確覆寫（explicit，對應 CLI --fanout-cap）、或由
   REMAGRAPH_FANOUT_CAP 環境變數覆寫，explicit 優先於環境變數。
3. 硬性上限 200（可由 REMAGRAPH_FANOUT_HARD_CAP 環境變數明確 opt-in 提高，
   非隨意調整的逃生艙口），任何請求值一律 clamp 到硬上限。
4. 不允許 0/負數作為「不限」的逃生艙口 —— 一律 fallback 回預設值。
5. SearchResponse 新增 candidates_total / candidates_searched /
   candidates_skipped 三個欄位，且 candidates_searched + candidates_skipped
   == candidates_total。
6. CLI `remagraph search`：capped 時 exit code 2（而非 0），--fanout-cap /
   REMAGRAPH_FANOUT_CAP 皆可生效。
7. MCP `remagraph_search`：回應 dict 同樣附上新欄位，isError 語意仍為
   non-error（沿用既有 status 慣例，不新增 status 欄位）。
"""

from __future__ import annotations

import json

import pytest

import remagraph.cli as cli_mod
from remagraph import db as db_mod
from remagraph import search as search_mod
from remagraph.models import SearchRequest


@pytest.fixture(autouse=True)
def isolated_default_state_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)
    monkeypatch.delenv("REMAGRAPH_PROJECT", raising=False)
    monkeypatch.delenv("REMAGRAPH_FANOUT_CAP", raising=False)
    monkeypatch.delenv("REMAGRAPH_FANOUT_HARD_CAP", raising=False)
    fake_default = tmp_path / "fake-default-state"
    monkeypatch.setattr(db_mod, "DEFAULT_STATE_DIR", fake_default)
    return fake_default


def _make_project_with_labeled_memory(
    tmp_path,
    monkeypatch,
    project_id: str,
    *,
    mem_id: str,
    label: str,
    summary: str,
):
    state_dir = tmp_path / f"{project_id}-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", project_id)

    conn = db_mod.connect(project_id=project_id)
    now = "2026-07-24T00:00:00Z"
    conn.execute(
        """
        INSERT INTO memories (
            id, project_id, kind, task_id, agent_id, timestamp,
            summary, learnings, handoff_note, tags, status, created_at, updated_at
        ) VALUES (?, ?, 'task_handoff', 'task-fixture', 'agent-fixture', ?,
                  ?, '[]', '', '[]', 'active', ?, ?)
        """,
        (mem_id, project_id, now, summary, now, now),
    )
    conn.execute(
        "INSERT INTO memory_labels (memory_id, label) VALUES (?, ?)",
        (mem_id, label),
    )
    conn.close()
    return state_dir


# ---------------------------------------------------------------------------
# 1. 預設值 / 覆寫優先序 / 硬上限 clamp（純函式單元測試，不涉及真實 DB）
# ---------------------------------------------------------------------------


def test_default_fanout_cap_is_50_not_20():
    assert search_mod._CROSS_PROJECT_FANOUT_CAP == 50


def test_resolve_fanout_cap_default_no_override():
    assert search_mod.resolve_fanout_cap() == 50


def test_resolve_fanout_cap_env_var_overrides_default(monkeypatch):
    monkeypatch.setenv("REMAGRAPH_FANOUT_CAP", "10")
    assert search_mod.resolve_fanout_cap() == 10


def test_resolve_fanout_cap_explicit_overrides_env(monkeypatch):
    monkeypatch.setenv("REMAGRAPH_FANOUT_CAP", "10")
    assert search_mod.resolve_fanout_cap(explicit=5) == 5


def test_resolve_fanout_cap_hard_ceiling_clamps_explicit_value():
    assert search_mod.resolve_fanout_cap(explicit=500) == 200


def test_resolve_fanout_cap_hard_ceiling_clamps_env_value(monkeypatch):
    monkeypatch.setenv("REMAGRAPH_FANOUT_CAP", "500")
    assert search_mod.resolve_fanout_cap() == 200


def test_resolve_fanout_cap_hard_cap_env_var_raises_ceiling(monkeypatch):
    monkeypatch.setenv("REMAGRAPH_FANOUT_HARD_CAP", "500")
    assert search_mod.resolve_fanout_cap(explicit=300) == 300


def test_resolve_fanout_cap_zero_is_not_unlimited():
    """0 不得被當成『不限上限』的逃生艙口 —— fallback 回預設值。"""
    assert search_mod.resolve_fanout_cap(explicit=0) == 50


def test_resolve_fanout_cap_negative_is_not_unlimited():
    assert search_mod.resolve_fanout_cap(explicit=-1) == 50


# ---------------------------------------------------------------------------
# 2. 整合：cross_project_label fan-out 套用 explicit fanout_cap，並回傳
#    candidates_total/candidates_searched/candidates_skipped
# ---------------------------------------------------------------------------


def test_cross_project_label_search_uses_explicit_fanout_cap_and_reports_counts(
    tmp_path, monkeypatch
):
    small_cap = 3
    total_others = small_cap + 2  # 5 個「其他」已知專案，皆掛同一個 label
    for i in range(total_others):
        _make_project_with_labeled_memory(
            tmp_path,
            monkeypatch,
            f"proj-cap-{i:02d}",
            mem_id=f"mem-cap-{i:02d}",
            label="topic:cap-test",
            summary=f"project number {i} fixture summary for explicit cap test",
        )

    caller_state_dir = tmp_path / "proj-caller-cap-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(caller_state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "proj-caller-cap")
    conn = db_mod.connect(project_id="proj-caller-cap")

    request = SearchRequest(
        cross_project_label="topic:cap-test",
        project_id="proj-caller-cap",
        top_k=100,
        fanout_cap=small_cap,
    )
    response = search_mod.search_memories(conn, request)

    # candidate_project_ids 來自 db.list_known_projects()，呼叫端自己的
    # project_id（proj-caller-cap）也會因 db.connect() 的 best-effort 自動
    # 登記副作用而出現在候選清單裡；_cross_project_fanout() 在計算
    # candidates_total 之前，會先把邏輯上等於 own_project_id 的候選過濾掉
    # （見該函式 other_candidate_ids 的說明，獨立對抗式審查發現並修復的
    # off-by-one：修復前 candidates_total 誤把呼叫端自己也算進去，導致
    # candidates_skipped 恆多算 1），因此 candidates_total 就是
    # total_others 本身，不含呼叫端自己。
    assert response.cross_project_fanout_capped is True
    assert len(response.results) == small_cap
    assert response.candidates_total == total_others
    assert response.candidates_searched == small_cap
    assert response.candidates_skipped == total_others - small_cap
    assert response.candidates_searched + response.candidates_skipped == response.candidates_total
    conn.close()


def test_cross_project_label_search_not_capped_reports_full_counts(tmp_path, monkeypatch):
    total_others = 2
    for i in range(total_others):
        _make_project_with_labeled_memory(
            tmp_path,
            monkeypatch,
            f"proj-nocap-{i:02d}",
            mem_id=f"mem-nocap-{i:02d}",
            label="topic:nocap-test",
            summary=f"project number {i} fixture summary for no-cap test",
        )

    caller_state_dir = tmp_path / "proj-caller-nocap-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(caller_state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "proj-caller-nocap")
    conn = db_mod.connect(project_id="proj-caller-nocap")

    request = SearchRequest(
        cross_project_label="topic:nocap-test",
        project_id="proj-caller-nocap",
        top_k=100,
        fanout_cap=10,
    )
    response = search_mod.search_memories(conn, request)

    # 呼叫端自己（proj-caller-nocap）也在候選清單裡（見上一個測試的註解），
    # 但在計算 candidates_total 之前就已被過濾掉（獨立對抗式審查發現的
    # off-by-one 修復），因此完全沒有撞到 cap 時，三個欄位必須完全一致：
    # candidates_total == candidates_searched，candidates_skipped == 0。
    assert response.cross_project_fanout_capped is False
    assert response.candidates_total == total_others
    assert response.candidates_searched == total_others
    assert response.candidates_skipped == 0
    assert response.candidates_searched + response.candidates_skipped == response.candidates_total
    conn.close()


def test_cross_project_label_search_reviewer_repro_non_capped_counts_are_consistent(
    tmp_path, monkeypatch
):
    """獨立對抗式審查者的原始重現：12 個真實的『其他』候選專案 + 呼叫端自己
    的登記，cap=100（不可能撞到 cap）。修復前：response 顯示
    candidates_total=13, candidates_searched=12, candidates_skipped=1 ——
    明明搜尋完整卻誤報「不完整」。修復後：candidates_total 必須等於 12
    （不含呼叫端自己），candidates_searched 必須等於 12，candidates_skipped
    必須等於 0。"""
    total_others = 12
    for i in range(total_others):
        _make_project_with_labeled_memory(
            tmp_path,
            monkeypatch,
            f"proj-repro-{i:02d}",
            mem_id=f"mem-repro-{i:02d}",
            label="topic:repro-test",
            summary=f"project number {i} fixture summary for reviewer repro test",
        )

    caller_state_dir = tmp_path / "proj-caller-repro-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(caller_state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "proj-caller-repro")
    conn = db_mod.connect(project_id="proj-caller-repro")

    request = SearchRequest(
        cross_project_label="topic:repro-test",
        project_id="proj-caller-repro",
        top_k=100,
        fanout_cap=100,
    )
    response = search_mod.search_memories(conn, request)

    assert response.cross_project_fanout_capped is False
    assert response.candidates_total == total_others
    assert response.candidates_searched == total_others
    assert response.candidates_skipped == 0
    assert response.candidates_searched + response.candidates_skipped == response.candidates_total
    conn.close()


def test_normal_search_without_fanout_has_zero_candidate_counts(tmp_path, monkeypatch):
    """非跨專案搜尋（既有主流程）不受影響：新欄位維持預設 0，向後相容。"""
    state_dir = tmp_path / "plain-search-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "plain-search")
    conn = db_mod.connect(project_id="plain-search")

    response = search_mod.search_memories(conn, SearchRequest(query="", project_id="plain-search"))

    assert response.cross_project_fanout_capped is False
    assert response.candidates_total == 0
    assert response.candidates_searched == 0
    assert response.candidates_skipped == 0
    conn.close()


# ---------------------------------------------------------------------------
# 3. CLI：--fanout-cap / REMAGRAPH_FANOUT_CAP + exit code 2 when capped
# ---------------------------------------------------------------------------


def test_cli_search_exits_2_when_fanout_capped(tmp_path, monkeypatch, capsys):
    small_cap = 2
    total_others = small_cap + 2
    for i in range(total_others):
        _make_project_with_labeled_memory(
            tmp_path,
            monkeypatch,
            f"proj-cli-cap-{i:02d}",
            mem_id=f"mem-cli-cap-{i:02d}",
            label="topic:cli-cap-test",
            summary=f"project number {i} fixture summary for cli cap test",
        )

    caller_state_dir = tmp_path / "proj-cli-caller-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(caller_state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "proj-cli-caller")

    with pytest.raises(SystemExit) as ei:
        cli_mod.main(
            [
                "search",
                "--cross-project-label",
                "topic:cli-cap-test",
                "--project",
                "proj-cli-caller",
                "--top-k",
                "100",
                "--fanout-cap",
                str(small_cap),
            ]
        )
    assert ei.value.code == 2

    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    # 呼叫端自己（proj-cli-caller）也會因 connect() 的自動登記副作用出現在
    # 候選清單裡，但在計算 candidates_total 之前就已被過濾掉（見上方單元
    # 測試同一項註解，獨立對抗式審查發現的 off-by-one 修復），故 total 就是
    # total_others 本身。
    assert payload["cross_project_fanout_capped"] is True
    assert payload["candidates_total"] == total_others
    assert payload["candidates_searched"] == small_cap
    assert payload["candidates_skipped"] == total_others - small_cap


def test_cli_search_exits_0_when_not_capped(tmp_path, monkeypatch, capsys):
    state_dir = tmp_path / "cli-nocap-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "cli-nocap")

    # 一般（無 cross_project_label）搜尋：不應觸發 fan-out，也不應 exit(2)。
    cli_mod.main(["search", "--task-id", "no-such-task", "--project", "cli-nocap"])

    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["cross_project_fanout_capped"] is False


def test_cli_search_env_var_fanout_cap_applies_when_flag_absent(tmp_path, monkeypatch, capsys):
    small_cap = 1
    total_others = small_cap + 2
    for i in range(total_others):
        _make_project_with_labeled_memory(
            tmp_path,
            monkeypatch,
            f"proj-env-cap-{i:02d}",
            mem_id=f"mem-env-cap-{i:02d}",
            label="topic:env-cap-test",
            summary=f"project number {i} fixture summary for env cap test",
        )

    caller_state_dir = tmp_path / "proj-env-caller-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(caller_state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "proj-env-caller")
    monkeypatch.setenv("REMAGRAPH_FANOUT_CAP", str(small_cap))

    with pytest.raises(SystemExit) as ei:
        cli_mod.main(
            [
                "search",
                "--cross-project-label",
                "topic:env-cap-test",
                "--project",
                "proj-env-caller",
                "--top-k",
                "100",
            ]
        )
    assert ei.value.code == 2

    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["candidates_searched"] == small_cap


# ---------------------------------------------------------------------------
# 4. MCP tool：remagraph_search 回應附上新欄位
# ---------------------------------------------------------------------------


def test_mcp_search_result_includes_candidate_count_fields(tmp_path, monkeypatch):
    import remagraph.server as server_mod

    state_dir = tmp_path / "mcp-nocap-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    server_mod._conn = None
    server_mod._bound_project_id = None

    result = server_mod.remagraph_search(query="AB")

    assert result["cross_project_fanout_capped"] is False
    assert result["candidates_total"] == 0
    assert result["candidates_searched"] == 0
    assert result["candidates_skipped"] == 0
    server_mod._conn = None
