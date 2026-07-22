"""Integration smoke tests for the full RemaGraph MCP server lifecycle.

每個 test 驗證一個端對端流程：write → search → status，使用 tmp_path 隔離。
"""

from __future__ import annotations

import pytest

import remagraph.server as server


def _reset_conn():
    server._conn = None


@pytest.fixture(autouse=True)
def setup_test_env(tmp_path, monkeypatch):
    """每個 test 使用獨立的 state 目錄並重置連線。"""
    state_dir = str(tmp_path / "state")
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", state_dir)
    _reset_conn()
    yield
    _reset_conn()


# ---------------------------------------------------------------------------
# 完整生命週期
# ---------------------------------------------------------------------------


def test_full_lifecycle_write_search_status():
    """端對端：寫入 → 查詢 → 狀態。"""
    # 1. 寫入一筆 task_handoff
    r1 = server.remagraph_store(
        project_id="default",
        task_id="task-smoke-001",
        agent_id="oc-smoke",
        kind="task_handoff",
        summary="這是一段關於 RemaGraph 整合測試的任務交接內容，記錄了整個生命週期的驗證過程",
        learnings=["整合測試應該使用獨立的 state 目錄", "每次測試結束後要清理 DB 連線"],
        handoff_note="接手者：這是一段給接手者的交接筆記，說明整合測試的注意事項與已知限制",
        tags=["smoke", "integration"],
    )
    assert r1["status"] == "stored"
    mem_id = r1["id"]

    # 2. 查詢確認能找到
    r2 = server.remagraph_search(query="RemaGraph 整合測試")
    assert len(r2["results"]) >= 1
    # 確認找到的是剛才寫入的
    ids = [m["id"] for m in r2["results"]]
    assert mem_id in ids

    # 3. 寫入 status_update
    r3 = server.remagraph_store(
        project_id="default",
        task_id="task-smoke-001",
        agent_id="oc-smoke",
        kind="status_update",
        summary="整合測試正在執行中，需要寫夠三十個中文字元才能通過仲裁規則的長度檢查",
        learnings=["status update 整合測試通過"],
        handoff_note="",
        tags=["smoke"],
    )
    assert r3["status"] == "stored"

    # 4. status 查詢
    r4 = server.remagraph_status(limit=10)
    assert len(r4["latest"]) >= 1
    task_ids = [s["task_id"] for s in r4["latest"]]
    assert "task-smoke-001" in task_ids


def test_status_update_supersede_chain():
    """同 task_id 連續三次 status_update，確認 supersede 行為。"""
    # 第一次
    r1 = server.remagraph_store(
        project_id="default",
        task_id="task-smoke-002",
        agent_id="oc-smoke",
        kind="status_update",
        summary="專案初始化完成，基礎架構已建立，包括資料庫連線管理模組及 MCP server 框架設定",
        learnings=["第一階段完成"],
        handoff_note="",
        tags=["smoke"],
    )
    assert r1["status"] == "stored"
    assert r1["superseded"] == []

    # 第二次（應 supersede 第一次）
    r2 = server.remagraph_store(
        project_id="default",
        task_id="task-smoke-002",
        agent_id="oc-smoke",
        kind="status_update",
        summary="核心功能開發已完成百分之五十，仲裁規則模組與去重機制皆已通過單元測試驗證",
        learnings=["第二階段完成"],
        handoff_note="",
        tags=["smoke"],
    )
    assert r2["status"] == "stored"
    assert len(r2["superseded"]) == 1

    # 第三次（應 supersede 前兩次）
    r3 = server.remagraph_store(
        project_id="default",
        task_id="task-smoke-002",
        agent_id="oc-smoke",
        kind="status_update",
        summary="全部功能已開發完成並通過整合測試，FTS5 全文檢索與記憶儲存流程皆驗證無誤",
        learnings=["第三階段完成"],
        handoff_note="",
        tags=["smoke"],
    )
    assert r3["status"] == "stored"
    assert len(r3["superseded"]) == 1  # 第二次已被 supersede，第三次只 supersede 第二次

    # status 查詢：同 task_id 只應有一筆（最新）
    status_result = server.remagraph_status(limit=10)
    matching = [s for s in status_result["latest"] if s["task_id"] == "task-smoke-002"]
    assert len(matching) == 1
    assert "全部功能已開發完成" in matching[0]["summary"]


def test_discovered_constraint_with_invalidates():
    """寫入一筆 discovered_constraint，再用另一筆 invalidate 它。"""
    # 寫入 constraint
    r1 = server.remagraph_store(
        project_id="default",
        task_id="task-smoke-003",
        agent_id="oc-smoke",
        kind="discovered_constraint",
        summary="發現一個重要的系統限制需要記錄下來，這是一段足夠長的 summary 來通過仲裁檢查",
        learnings=["系統有特定的記憶體上限"],
        handoff_note="",
        tags=["smoke", "constraint"],
    )
    assert r1["status"] == "stored"
    constraint_id = r1["id"]

    # 用 invalidates 標記為 invalidated
    r2 = server.remagraph_store(
        project_id="default",
        task_id="task-smoke-004",
        agent_id="oc-smoke",
        kind="discovered_constraint",
        summary="更新先前的限制描述，因為版本升級後記憶體上限已經變更，這是一段足夠長的 summary",
        learnings=["v2.0 記憶體上限已提高"],
        handoff_note="",
        tags=["smoke"],
        invalidates=[constraint_id],
    )
    assert r2["status"] == "stored"
    assert r2["invalidated_count"] == 1


def test_kind_filter():
    """驗證 kind 過濾：只查 task_handoff 時不回傳 status_update。"""
    # 寫入 task_handoff
    server.remagraph_store(
        project_id="default",
        task_id="task-smoke-005",
        agent_id="oc-smoke",
        kind="task_handoff",
        summary="這是一段關於 kind filter 測試的任務交接內容，記錄了 kind 過濾的驗證過程",
        learnings=["kind 過濾測試"],
        handoff_note="這是一段給接手者的交接筆記，至少需要二十個字以上才能通過仲裁規則的長度檢查",
        tags=["smoke"],
    )

    # 寫入 status_update（不同 task_id 避免 supersede）
    server.remagraph_store(
        project_id="default",
        task_id="task-smoke-006",
        agent_id="oc-smoke",
        kind="status_update",
        summary=(
            "這是一段 status update 用來測試 kind 過濾功能，需要寫夠三十個中文字元來通過長度檢查"
        ),
        learnings=["status 過濾測試"],
        handoff_note="",
        tags=["smoke"],
    )

    # 只查 task_handoff
    result = server.remagraph_search(query="smoke 測試", kind="task_handoff")
    assert len(result["results"]) >= 1
    kinds = {m["kind"] for m in result["results"]}
    assert kinds == {"task_handoff"}

    # 只查 status_update
    result2 = server.remagraph_search(query="smoke 測試", kind="status_update")
    assert len(result2["results"]) >= 1
    kinds2 = {m["kind"] for m in result2["results"]}
    assert kinds2 == {"status_update"}
