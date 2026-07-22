"""Unit tests for MCP server tool handlers.

每個 test 直接呼叫 server.py 的 tool handler 函式（remagraph_store / remagraph_search /
remagraph_status），使用 tmp_path 作為 state 目錄以隔離測試。
"""

from __future__ import annotations

import pytest

import remagraph.server as server


def _reset_conn():
    """Reset module-level DB singleton between tests."""
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
# remagraph_store
# ---------------------------------------------------------------------------


def test_store_task_handoff_success():
    """寫入 task_handoff 成功回傳 stored。"""
    result = server.remagraph_store(
        project_id="testproj",
        task_id="task-test-001",
        agent_id="oc-test",
        kind="task_handoff",
        summary="這是一段足夠長的 summary 來通過仲裁規則檢查，至少需要三十個中文字元才能過關",
        learnings=["學到了重要的事情", "第二個學習點"],
        handoff_note="這是一段給接手者的交接筆記，至少要二十個字以上才算夠長",
        tags=["test", "server"],
    )

    assert result["status"] == "stored"
    assert result["id"] is not None
    assert result["id"].startswith("mem-")
    assert result["superseded"] == []
    assert result["invalidated_count"] == 0


def test_store_status_update_supersede():
    """連續兩次寫入同 task_id 的 status_update，第二次應 supersede 第一次。"""
    # 第一次
    r1 = server.remagraph_store(
        project_id="testproj",
        task_id="task-test-002",
        agent_id="oc-test",
        kind="status_update",
        summary=(
            "這是一段足夠長的 status update summary 來通過仲裁規則檢查，"
            "至少需要三十個中文字元才能過關"
        ),
        learnings=["第一次狀態"],
        handoff_note="",
        tags=["test"],
    )
    assert r1["status"] == "stored"

    # 第二次（同 task_id）
    r2 = server.remagraph_store(
        project_id="testproj",
        task_id="task-test-002",
        agent_id="oc-test",
        kind="status_update",
        summary="第二次狀態更新，同樣要寫夠三十個中文字元才能通過仲裁規則的長度門檻檢查",
        learnings=["狀態已變更"],
        handoff_note="",
        tags=["test"],
    )
    assert r2["status"] == "stored"
    assert len(r2["superseded"]) == 1  # 第一次的被 supersede


def test_store_rejected_summary_too_short():
    """summary 太短應被拒絕。"""
    result = server.remagraph_store(
        project_id="testproj",
        task_id="task-test-003",
        agent_id="oc-test",
        kind="task_handoff",
        summary="太短",
        learnings=["學到了"],
        handoff_note="這是一段夠長的交接筆記，至少要二十個字以上才算夠長",
        tags=["test"],
    )

    assert result["status"] == "rejected"
    assert result["reason"] == "summary_too_short"


def test_store_rejected_learnings_empty():
    """learnings 為空應被拒絕。"""
    result = server.remagraph_store(
        project_id="testproj",
        task_id="task-test-004",
        agent_id="oc-test",
        kind="status_update",
        summary="這是一段足夠長的 summary 來通過仲裁規則檢查，至少需要三十個中文字元才能過關",
        learnings=[],
        handoff_note="",
        tags=["test"],
    )

    assert result["status"] == "rejected"
    assert result["reason"] == "learnings_empty"


# ---------------------------------------------------------------------------
# remagraph_search
# ---------------------------------------------------------------------------


def test_search_empty_db():
    """空資料庫查詢回傳空結果。"""
    result = server.remagraph_search(query="測試查詢")

    assert result["results"] == []
    assert result["has_more"] is False


def test_search_with_data():
    """先寫入一筆，再查詢確認能找到。"""
    # 寫入
    store_result = server.remagraph_store(
        project_id="testproj",
        task_id="task-test-005",
        agent_id="oc-test",
        kind="task_handoff",
        summary="這是一段關於 Python 非同步程式設計的任務交接內容，記錄了重要的學習經驗",
        learnings=["asyncio 的 event loop 管理很重要"],
        handoff_note="接手者請注意 asyncio 的 event loop 要在 main thread 建立",
        tags=["python", "async"],
    )
    assert store_result["status"] == "stored"

    # 查詢
    result = server.remagraph_search(query="Python 非同步")

    assert len(result["results"]) >= 1
    assert result["has_more"] is False
    found = result["results"][0]
    assert found["summary"] == (
        "這是一段關於 Python 非同步程式設計的任務交接內容，記錄了重要的學習經驗"
    )


def test_search_short_query():
    """短查詢（≤2 字元）回傳空結果不拋錯。"""
    result = server.remagraph_search(query="AB")

    assert result["results"] == []
    assert result["has_more"] is False


# ---------------------------------------------------------------------------
# remagraph_status
# ---------------------------------------------------------------------------


def test_status_empty_db():
    """空資料庫回傳空 latest。"""
    result = server.remagraph_status(limit=10)

    assert result["latest"] == []


def test_status_with_updates():
    """寫入 status_update 後，status 查詢能回傳。"""
    server.remagraph_store(
        project_id="testproj",
        task_id="task-test-006",
        agent_id="oc-test",
        kind="status_update",
        summary="任務進行中的狀態更新，需要寫夠三十個中文字元才能通過仲裁規則的長度檢查",
        learnings=["進度 50%"],
        handoff_note="",
        tags=["test"],
    )

    result = server.remagraph_status(limit=10)

    assert len(result["latest"]) >= 1
    found = result["latest"][0]
    assert found["task_id"] == "task-test-006"
    assert found["kind"] == "status_update"
