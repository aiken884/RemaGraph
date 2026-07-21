"""WU-6: search + status 測試。

所有測試使用 tmp_path + REMAGRAPH_STATE_DIR 隔離 state，
不得汙染 ~/.local/state/remagraph/。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

import pytest

from remagraph.db import _init_schema
from remagraph.models import SearchRequest, StatusRequest
from remagraph.search import get_status, sanitize_fts5_query, search_memories


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _insert_memory(
    conn: sqlite3.Connection,
    *,
    id: str,
    kind: str,
    task_id: str = "task-1",
    agent_id: str = "agent-1",
    summary: str,
    status: str = "active",
    tags: list[str] | None = None,
    timestamp: str | None = None,
) -> None:
    """插入一筆記憶記錄，自動觸發 FTS5 同步。"""
    tags_json = json.dumps(tags or [], ensure_ascii=False)
    ts = timestamp or _now_iso()
    conn.execute(
        "INSERT INTO memories (id, kind, task_id, agent_id, timestamp, summary, "
        "learnings, handoff_note, tags, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, '[]', '', ?, ?, ?, ?)",
        (id, kind, task_id, agent_id, ts, summary, tags_json, status, ts, ts),
    )


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """建立測試用 :memory: SQLite，隔離 REMAGRAPH_STATE_DIR。"""
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path))
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _init_schema(conn)
    yield conn
    conn.close()


# ===================================================================
# sanitize_fts5_query
# ===================================================================


class TestSanitizeFts5Query:
    """sanitize_fts5_query 單元測試 — 確保 FTS5 特殊字元被正確處理。"""

    def test_plain_text_passes_through(self):
        assert sanitize_fts5_query("hello world") == "hello world"

    def test_asterisk_removed(self):
        assert sanitize_fts5_query("hello* world") == "hello world"

    def test_double_quotes_removed(self):
        assert sanitize_fts5_query('hello "world"') == "hello world"

    def test_parentheses_removed(self):
        assert sanitize_fts5_query("(hello world)") == "hello world"

    def test_caret_removed(self):
        assert sanitize_fts5_query("^hello world") == "hello world"

    def test_tilde_removed(self):
        assert sanitize_fts5_query("hello~ world") == "hello world"

    def test_keyword_and_quoted(self):
        assert sanitize_fts5_query("hello AND world") == 'hello "AND" world'

    def test_keyword_or_quoted(self):
        assert sanitize_fts5_query("this OR that") == 'this "OR" that'

    def test_keyword_not_quoted(self):
        assert sanitize_fts5_query("NOT found") == '"NOT" found'

    def test_keyword_near_quoted(self):
        assert sanitize_fts5_query("NEAR miss") == '"NEAR" miss'

    def test_keyword_case_insensitive(self):
        assert sanitize_fts5_query("hello and world") == 'hello "and" world'

    def test_empty_string(self):
        assert sanitize_fts5_query("") == ""

    def test_only_special_chars(self):
        assert sanitize_fts5_query("**\"\"()") == ""

    def test_cjk_passes_through(self):
        assert sanitize_fts5_query("測試中文查詢") == "測試中文查詢"

    def test_cjk_with_special_chars(self):
        assert sanitize_fts5_query('測試*"查詢"') == "測試 查詢"

    def test_mixed_cjk_and_ascii(self):
        assert sanitize_fts5_query("API 回應逾時 *修復") == "API 回應逾時 修復"


# ===================================================================
# search_memories
# ===================================================================


class TestSearchMemories:
    """search_memories 整合測試 — 涵蓋基本搜尋、過濾、has_more、中文。"""

    # -- 基本搜尋 --

    def test_basic_search_single_result(self, conn):
        _insert_memory(conn, id="mem-1", kind="task_handoff", summary="hello world test")
        req = SearchRequest(query="hello world", top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) == 1
        assert resp.results[0]["id"] == "mem-1"
        assert isinstance(resp.results[0]["score"], (int, float))
        assert resp.has_more is False

    def test_search_no_match(self, conn):
        _insert_memory(conn, id="mem-1", kind="task_handoff", summary="foo bar")
        req = SearchRequest(query="nonexistent")
        resp = search_memories(conn, req)
        assert len(resp.results) == 0
        assert resp.has_more is False

    def test_search_multiple_results(self, conn):
        _insert_memory(conn, id="mem-1", kind="task_handoff", summary="hello alpha")
        _insert_memory(conn, id="mem-2", kind="task_handoff", summary="hello beta")
        req = SearchRequest(query="hello", top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) == 2

    # -- 短查詢處理 --

    def test_short_ascii_query_returns_empty(self, conn, caplog):
        """≤2 字元的 ASCII 查詢無法形成 trigram，應回傳空結果 + warning。"""
        _insert_memory(conn, id="mem-1", kind="task_handoff", summary="test data")
        with caplog.at_level(logging.WARNING):
            req = SearchRequest(query="ab")
            resp = search_memories(conn, req)
        assert len(resp.results) == 0
        assert resp.has_more is False
        assert "too short" in caplog.text.lower()

    def test_empty_after_sanitize_returns_empty(self, conn, caplog):
        """sanitize 後為空字串時，應回傳空結果。"""
        _insert_memory(conn, id="mem-1", kind="task_handoff", summary="test data")
        with caplog.at_level(logging.WARNING):
            req = SearchRequest(query="**\"\"")
            resp = search_memories(conn, req)
        assert len(resp.results) == 0

    # -- kind 過濾 --

    def test_kind_filter(self, conn):
        _insert_memory(conn, id="mem-1", kind="task_handoff", summary="hello world")
        _insert_memory(conn, id="mem-2", kind="status_update", summary="hello world")
        req = SearchRequest(query="hello world", kind="task_handoff", top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) == 1
        assert resp.results[0]["id"] == "mem-1"

    # -- status 過濾 --

    def test_status_filter_active(self, conn):
        _insert_memory(conn, id="mem-1", kind="task_handoff", summary="hello", status="active")
        _insert_memory(conn, id="mem-2", kind="task_handoff", summary="hello", status="superseded")
        req = SearchRequest(query="hello", status="active", top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) == 1
        assert resp.results[0]["id"] == "mem-1"

    # -- tags 過濾（AND 語意） --

    def test_tags_filter_single(self, conn):
        _insert_memory(conn, id="mem-1", kind="task_handoff", summary="hello", tags=["auth", "login"])
        _insert_memory(conn, id="mem-2", kind="task_handoff", summary="hello", tags=["auth", "api"])
        req = SearchRequest(query="hello", tags=["login"], top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) == 1
        assert resp.results[0]["id"] == "mem-1"

    def test_tags_filter_multiple(self, conn):
        _insert_memory(conn, id="mem-1", kind="task_handoff", summary="hello", tags=["auth", "login", "oauth"])
        _insert_memory(conn, id="mem-2", kind="task_handoff", summary="hello", tags=["auth", "login"])
        req = SearchRequest(query="hello", tags=["auth", "oauth"], top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) == 1
        assert resp.results[0]["id"] == "mem-1"

    # -- agent_id / task_id 過濾 --

    def test_agent_id_filter(self, conn):
        _insert_memory(conn, id="mem-1", kind="task_handoff", summary="hello", agent_id="agent-a")
        _insert_memory(conn, id="mem-2", kind="task_handoff", summary="hello", agent_id="agent-b")
        req = SearchRequest(query="hello", agent_id="agent-a", top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) == 1
        assert resp.results[0]["id"] == "mem-1"

    def test_task_id_filter(self, conn):
        _insert_memory(conn, id="mem-1", kind="task_handoff", summary="hello", task_id="task-a")
        _insert_memory(conn, id="mem-2", kind="task_handoff", summary="hello", task_id="task-b")
        req = SearchRequest(query="hello", task_id="task-a", top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) == 1
        assert resp.results[0]["id"] == "mem-1"

    # -- has_more --

    def test_has_more_true(self, conn):
        for i in range(10):
            _insert_memory(conn, id=f"mem-{i}", kind="task_handoff", summary=f"hello world test {i}")
        req = SearchRequest(query="hello world", top_k=5)
        resp = search_memories(conn, req)
        assert len(resp.results) == 5
        assert resp.has_more is True

    def test_has_more_false_when_exact(self, conn):
        for i in range(5):
            _insert_memory(conn, id=f"mem-{i}", kind="task_handoff", summary=f"hello world test {i}")
        req = SearchRequest(query="hello world", top_k=5)
        resp = search_memories(conn, req)
        assert len(resp.results) == 5
        assert resp.has_more is False

    def test_has_more_false_when_less(self, conn):
        for i in range(3):
            _insert_memory(conn, id=f"mem-{i}", kind="task_handoff", summary=f"hello world test {i}")
        req = SearchRequest(query="hello world", top_k=5)
        resp = search_memories(conn, req)
        assert len(resp.results) == 3
        assert resp.has_more is False

    # -- top_k 邊界（模型層級已在 test_models 驗證，此處測整合行為） --

    def test_top_k_default_20(self, conn):
        for i in range(25):
            _insert_memory(conn, id=f"mem-{i}", kind="task_handoff", summary=f"hello world test {i}")
        req = SearchRequest(query="hello world")  # 預設 top_k=20
        resp = search_memories(conn, req)
        assert len(resp.results) == 20
        assert resp.has_more is True

    # -- 複合過濾 --

    def test_combined_filters(self, conn):
        _insert_memory(conn, id="mem-1", kind="task_handoff", summary="hello", status="active",
                       tags=["auth"], agent_id="agent-a")
        _insert_memory(conn, id="mem-2", kind="task_handoff", summary="hello", status="superseded",
                       tags=["auth"], agent_id="agent-a")
        _insert_memory(conn, id="mem-3", kind="status_update", summary="hello", status="active",
                       tags=["auth"], agent_id="agent-a")
        req = SearchRequest(
            query="hello", kind="task_handoff", status="active",
            tags=["auth"], agent_id="agent-a", top_k=10,
        )
        resp = search_memories(conn, req)
        assert len(resp.results) == 1
        assert resp.results[0]["id"] == "mem-1"

    # ================================================================
    # 中文查詢測試（至少 3 組）
    # ================================================================

    def test_cjk_search_three_chars(self, conn):
        """三字元中文查詢應正常匹配 trigram。"""
        _insert_memory(conn, id="mem-1", kind="task_handoff",
                       summary="處理使用者登入錯誤，修正密碼驗證邏輯")
        _insert_memory(conn, id="mem-2", kind="task_handoff",
                       summary="修復資料庫連線池問題，增加重試機制")
        req = SearchRequest(query="登入錯誤", top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) >= 1
        assert any("登入" in r["summary"] for r in resp.results)

    def test_cjk_search_long_query(self, conn):
        """長中文查詢（≥3 字）應正常匹配。"""
        _insert_memory(conn, id="mem-1", kind="task_handoff",
                       summary="API 回應逾時可能與網路延遲有關，需檢查 CDN 設定")
        _insert_memory(conn, id="mem-2", kind="task_handoff",
                       summary="前端頁面重構為 React Server Components")
        req = SearchRequest(query="API 回應逾時", top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) == 1
        assert resp.results[0]["id"] == "mem-1"

    def test_cjk_search_mixed_lang(self, conn):
        """中英混合查詢應正常匹配。"""
        _insert_memory(conn, id="mem-1", kind="task_handoff",
                       summary="修復 SSR hydration mismatch 問題，改用 Next.js dynamic import")
        _insert_memory(conn, id="mem-2", kind="task_handoff",
                       summary="更新 CI pipeline，加入 pnpm cache")
        req = SearchRequest(query="SSR hydration 問題", top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) >= 1
        assert any("SSR" in r["summary"] for r in resp.results)

    def test_cjk_multiple_results_ordered(self, conn):
        """多筆中文結果應依 BM25 分數排序。"""
        _insert_memory(conn, id="mem-1", kind="task_handoff",
                       summary="資料庫遷移完成，使用者資料表結構已變更")
        _insert_memory(conn, id="mem-2", kind="task_handoff",
                       summary="資料庫連線池調整，提高並發處理能力")
        _insert_memory(conn, id="mem-3", kind="task_handoff",
                       summary="前端頁面重新設計配色")
        req = SearchRequest(query="資料庫", top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) == 2
        for r in resp.results:
            assert "資料庫" in r["summary"]

    def test_cjk_search_by_tags_with_chinese(self, conn):
        """中文摘要 + tag 過濾應正常運作。"""
        _insert_memory(conn, id="mem-1", kind="task_handoff",
                       summary="修復使用者登入頁面錯誤，調整表單驗證邏輯", tags=["auth", "緊急"])
        _insert_memory(conn, id="mem-2", kind="task_handoff",
                       summary="修復資料庫連線逾時問題", tags=["db", "緊急"])
        req = SearchRequest(query="使用者登入", tags=["auth"], top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) == 1
        assert resp.results[0]["id"] == "mem-1"

    def test_cjk_short_query_two_chars_warns(self, conn, caplog):
        """2 字元中文查詢（如「登入」）無法形成 trigram，應回傳空結果 + warning。"""
        _insert_memory(conn, id="mem-1", kind="task_handoff",
                       summary="處理登入錯誤與密碼驗證")
        with caplog.at_level(logging.WARNING):
            req = SearchRequest(query="登入", top_k=10)
            resp = search_memories(conn, req)
        assert len(resp.results) == 0
        assert "too short" in caplog.text.lower()

    def test_cjk_short_query_one_char_warns(self, conn, caplog):
        """1 字元中文查詢無法形成 trigram，應回傳空結果 + warning。"""
        _insert_memory(conn, id="mem-1", kind="task_handoff", summary="修復錯誤")
        with caplog.at_level(logging.WARNING):
            req = SearchRequest(query="錯", top_k=10)
            resp = search_memories(conn, req)
        assert len(resp.results) == 0
        assert "too short" in caplog.text.lower()

    # -- 結果結構驗證 --

    def test_result_includes_all_fields(self, conn):
        _insert_memory(conn, id="mem-1", kind="task_handoff", summary="hello world",
                       agent_id="test-agent", task_id="task-x")
        req = SearchRequest(query="hello world", top_k=10)
        resp = search_memories(conn, req)
        r = resp.results[0]
        assert r["id"] == "mem-1"
        assert r["summary"] == "hello world"
        assert r["agent_id"] == "test-agent"
        assert r["kind"] == "task_handoff"
        assert r["task_id"] == "task-x"
        assert "timestamp" in r
        assert "score" in r


# ===================================================================
# get_status
# ===================================================================


class TestGetStatus:
    """get_status 整合測試 — 涵蓋去重、active 過濾、limit。"""

    def test_basic_status_query(self, conn):
        _insert_memory(conn, id="mem-1", kind="status_update",
                       task_id="task-a", summary="進度 50%", status="active")
        resp = get_status(conn, StatusRequest(limit=10))
        assert len(resp.latest) == 1
        assert resp.latest[0]["id"] == "mem-1"
        assert resp.latest[0]["task_id"] == "task-a"
        assert resp.latest[0]["kind"] == "status_update"

    def test_dedup_by_task_id_latest_wins(self, conn):
        """同一 task_id 多筆 status_update 時只取最新。"""
        _insert_memory(conn, id="mem-1", kind="status_update",
                       task_id="task-a", summary="第一筆狀態",
                       timestamp="2024-01-01T00:00:00.000000Z")
        _insert_memory(conn, id="mem-2", kind="status_update",
                       task_id="task-a", summary="第二筆（最新）",
                       timestamp="2024-01-02T00:00:00.000000Z")
        resp = get_status(conn, StatusRequest(limit=10))
        assert len(resp.latest) == 1
        assert resp.latest[0]["id"] == "mem-2"
        assert resp.latest[0]["summary"] == "第二筆（最新）"

    def test_only_active_status(self, conn):
        """只回傳 status='active' 的記錄。"""
        _insert_memory(conn, id="mem-1", kind="status_update",
                       task_id="task-a", summary="active", status="active")
        _insert_memory(conn, id="mem-2", kind="status_update",
                       task_id="task-b", summary="superseded", status="superseded")
        resp = get_status(conn, StatusRequest(limit=10))
        assert len(resp.latest) == 1
        assert resp.latest[0]["summary"] == "active"

    def test_only_status_update_kind(self, conn):
        """只回傳 kind='status_update' 的記錄。"""
        _insert_memory(conn, id="mem-1", kind="status_update",
                       task_id="task-a", summary="狀態更新", status="active")
        _insert_memory(conn, id="mem-2", kind="task_handoff",
                       task_id="task-b", summary="交接記錄", status="active")
        resp = get_status(conn, StatusRequest(limit=10))
        assert len(resp.latest) == 1
        assert resp.latest[0]["kind"] == "status_update"

    def test_multiple_tasks_all_returned(self, conn):
        """多個不同 task 的 active status 應全部回傳。"""
        _insert_memory(conn, id="mem-1", kind="status_update",
                       task_id="task-a", summary="Task A 進行中", status="active")
        _insert_memory(conn, id="mem-2", kind="status_update",
                       task_id="task-b", summary="Task B 進行中", status="active")
        _insert_memory(conn, id="mem-3", kind="status_update",
                       task_id="task-c", summary="Task C 進行中", status="active")
        resp = get_status(conn, StatusRequest(limit=10))
        assert len(resp.latest) == 3

    def test_default_limit_20(self):
        req = StatusRequest()
        assert req.limit == 20

    def test_max_limit_100(self):
        req = StatusRequest(limit=100)
        assert req.limit == 100

    def test_limit_respected(self, conn):
        """確認 limit 參數被實際套用。"""
        for i in range(5):
            _insert_memory(conn, id=f"mem-{i}", kind="status_update",
                           task_id=f"task-{i}", summary=f"狀態 {i}", status="active")
        resp = get_status(conn, StatusRequest(limit=3))
        assert len(resp.latest) == 3

    def test_no_active_status(self, conn):
        """沒有任何 active status_update 時回傳空陣列。"""
        resp = get_status(conn, StatusRequest(limit=10))
        assert resp.latest == []

    def test_response_field_completeness(self, conn):
        _insert_memory(conn, id="mem-1", kind="status_update",
                       task_id="task-a", summary="完整欄位測試",
                       status="active", agent_id="agent-x",
                       tags=["urgent"])
        resp = get_status(conn, StatusRequest(limit=10))
        r = resp.latest[0]
        assert r["id"] == "mem-1"
        assert r["task_id"] == "task-a"
        assert r["agent_id"] == "agent-x"
        assert r["kind"] == "status_update"
        assert r["summary"] == "完整欄位測試"
        assert r["status"] == "active"
        assert "timestamp" in r
