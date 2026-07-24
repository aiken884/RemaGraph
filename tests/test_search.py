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
    learnings: list[str] | None = None,
    handoff_note: str = "",
    timestamp: str | None = None,
    project_id: str = "default",
) -> None:
    """插入一筆記憶記錄，自動觸發 FTS5 同步。"""
    tags_json = json.dumps(tags or [], ensure_ascii=False)
    learnings_json = json.dumps(learnings or [], ensure_ascii=False)
    ts = timestamp or _now_iso()
    conn.execute(
        "INSERT INTO memories (id, project_id, kind, task_id, agent_id, timestamp, summary, "
        "learnings, handoff_note, tags, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            id,
            project_id,
            kind,
            task_id,
            agent_id,
            ts,
            summary,
            learnings_json,
            handoff_note,
            tags_json,
            status,
            ts,
            ts,
        ),
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
        assert sanitize_fts5_query('**""()') == ""

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
            req = SearchRequest(query='**""')
            resp = search_memories(conn, req)
        assert len(resp.results) == 0

    # ================================================================
    # Regression: 空字串 query 應等同「列出最近記憶」，而非全文檢索 (#29)
    # ================================================================

    def test_empty_query_no_filters_lists_recent(self, conn):
        """空字串 query 且無任何過濾條件時，不應觸發 trigram 最短長度限制
        而靜默回傳空結果，而應視為「列出最近的記憶」。

        修復前：query="" 命中 `_trigram_char_len(sanitized) < 3` 短路徑，
        因無 task_id/agent_id/kind/tags 過濾條件，直接回傳空結果 + warning
        "FTS5 query too short for trigram tokenizer"——即使資料庫內有記錄。
        """
        _insert_memory(conn, id="mem-1", kind="task_handoff", summary="任意內容，不含特定查詢字串")
        req = SearchRequest(query="", top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) == 1
        assert resp.results[0]["id"] == "mem-1"

    def test_empty_query_with_project_id_only_lists_recent(self, conn):
        """空字串 query + 僅 project_id 過濾（無 task_id/agent_id/kind/tags）時，
        亦應列出最近記憶——對應 `remagraph search --all-projects` 或跨專案
        fleet 查詢等僅靠 project_id 篩選的呼叫路徑。
        """
        _insert_memory(
            conn, id="mem-1", kind="task_handoff", summary="專案內容", project_id="proj-a"
        )
        req = SearchRequest(query="", project_id="proj-a", top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) == 1
        assert resp.results[0]["id"] == "mem-1"

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
        _insert_memory(
            conn, id="mem-1", kind="task_handoff", summary="hello", tags=["auth", "login"]
        )
        _insert_memory(conn, id="mem-2", kind="task_handoff", summary="hello", tags=["auth", "api"])
        req = SearchRequest(query="hello", tags=["login"], top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) == 1
        assert resp.results[0]["id"] == "mem-1"

    def test_tags_filter_multiple(self, conn):
        _insert_memory(
            conn,
            id="mem-1",
            kind="task_handoff",
            summary="hello",
            tags=["auth", "login", "oauth"],
        )
        _insert_memory(
            conn, id="mem-2", kind="task_handoff", summary="hello", tags=["auth", "login"]
        )
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
            _insert_memory(
                conn,
                id=f"mem-{i}",
                kind="task_handoff",
                summary=f"hello world test {i}",
            )
        req = SearchRequest(query="hello world", top_k=5)
        resp = search_memories(conn, req)
        assert len(resp.results) == 5
        assert resp.has_more is True

    def test_has_more_false_when_exact(self, conn):
        for i in range(5):
            _insert_memory(
                conn,
                id=f"mem-{i}",
                kind="task_handoff",
                summary=f"hello world test {i}",
            )
        req = SearchRequest(query="hello world", top_k=5)
        resp = search_memories(conn, req)
        assert len(resp.results) == 5
        assert resp.has_more is False

    def test_has_more_false_when_less(self, conn):
        for i in range(3):
            _insert_memory(
                conn,
                id=f"mem-{i}",
                kind="task_handoff",
                summary=f"hello world test {i}",
            )
        req = SearchRequest(query="hello world", top_k=5)
        resp = search_memories(conn, req)
        assert len(resp.results) == 3
        assert resp.has_more is False

    # -- top_k 邊界（模型層級已在 test_models 驗證，此處測整合行為） --

    def test_top_k_default_20(self, conn):
        for i in range(25):
            _insert_memory(
                conn,
                id=f"mem-{i}",
                kind="task_handoff",
                summary=f"hello world test {i}",
            )
        req = SearchRequest(query="hello world")  # 預設 top_k=20
        resp = search_memories(conn, req)
        assert len(resp.results) == 20
        assert resp.has_more is True

    # -- 複合過濾 --

    def test_combined_filters(self, conn):
        _insert_memory(
            conn,
            id="mem-1",
            kind="task_handoff",
            summary="hello",
            status="active",
            tags=["auth"],
            agent_id="agent-a",
        )
        _insert_memory(
            conn,
            id="mem-2",
            kind="task_handoff",
            summary="hello",
            status="superseded",
            tags=["auth"],
            agent_id="agent-a",
        )
        _insert_memory(
            conn,
            id="mem-3",
            kind="status_update",
            summary="hello",
            status="active",
            tags=["auth"],
            agent_id="agent-a",
        )
        req = SearchRequest(
            query="hello",
            kind="task_handoff",
            status="active",
            tags=["auth"],
            agent_id="agent-a",
            top_k=10,
        )
        resp = search_memories(conn, req)
        assert len(resp.results) == 1
        assert resp.results[0]["id"] == "mem-1"

    # ================================================================
    # 中文查詢測試（至少 3 組）
    # ================================================================

    def test_cjk_search_three_chars(self, conn):
        """三字元中文查詢應正常匹配 trigram。"""
        _insert_memory(
            conn, id="mem-1", kind="task_handoff", summary="處理使用者登入錯誤，修正密碼驗證邏輯"
        )
        _insert_memory(
            conn, id="mem-2", kind="task_handoff", summary="修復資料庫連線池問題，增加重試機制"
        )
        req = SearchRequest(query="登入錯誤", top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) >= 1
        assert any("登入" in r["summary"] for r in resp.results)

    def test_cjk_search_long_query(self, conn):
        """長中文查詢（≥3 字）應正常匹配。"""
        _insert_memory(
            conn,
            id="mem-1",
            kind="task_handoff",
            summary="API 回應逾時可能與網路延遲有關，需檢查 CDN 設定",
        )
        _insert_memory(
            conn, id="mem-2", kind="task_handoff", summary="前端頁面重構為 React Server Components"
        )
        req = SearchRequest(query="API 回應逾時", top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) == 1
        assert resp.results[0]["id"] == "mem-1"

    def test_cjk_search_mixed_lang(self, conn):
        """中英混合查詢應正常匹配。"""
        _insert_memory(
            conn,
            id="mem-1",
            kind="task_handoff",
            summary="修復 SSR hydration mismatch 問題，改用 Next.js dynamic import",
        )
        _insert_memory(
            conn, id="mem-2", kind="task_handoff", summary="更新 CI pipeline，加入 pnpm cache"
        )
        req = SearchRequest(query="SSR hydration 問題", top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) >= 1
        assert any("SSR" in r["summary"] for r in resp.results)

    def test_cjk_multiple_results_ordered(self, conn):
        """多筆中文結果應依 BM25 分數排序。"""
        _insert_memory(
            conn, id="mem-1", kind="task_handoff", summary="資料庫遷移完成，使用者資料表結構已變更"
        )
        _insert_memory(
            conn, id="mem-2", kind="task_handoff", summary="資料庫連線池調整，提高並發處理能力"
        )
        _insert_memory(conn, id="mem-3", kind="task_handoff", summary="前端頁面重新設計配色")
        req = SearchRequest(query="資料庫", top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) == 2
        for r in resp.results:
            assert "資料庫" in r["summary"]

    def test_cjk_search_by_tags_with_chinese(self, conn):
        """中文摘要 + tag 過濾應正常運作。"""
        _insert_memory(
            conn,
            id="mem-1",
            kind="task_handoff",
            summary="修復使用者登入頁面錯誤，調整表單驗證邏輯",
            tags=["auth", "緊急"],
        )
        _insert_memory(
            conn,
            id="mem-2",
            kind="task_handoff",
            summary="修復資料庫連線逾時問題",
            tags=["db", "緊急"],
        )
        req = SearchRequest(query="使用者登入", tags=["auth"], top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) == 1
        assert resp.results[0]["id"] == "mem-1"

    def test_cjk_short_query_two_chars_warns(self, conn, caplog):
        """2 字元中文查詢（如「登入」）無法形成 trigram，應回傳空結果 + warning。"""
        _insert_memory(conn, id="mem-1", kind="task_handoff", summary="處理登入錯誤與密碼驗證")
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

    # ================================================================
    # 特殊字元 query（regression: 連字號被 FTS5 誤解為 column-filter 語法）
    # ================================================================

    def test_hyphenated_query_matches_literal_substring(self, conn):
        """Regression: 查詢字串含連字號應能匹配儲存記錄中逐字相同的子字串。

        修復前：FTS5 query 語法會將未加引號的 "deny-subagent" 解析成
        column-filter 表達式（"-subagent" 被視為欲排除的欄位名稱），
        因不存在名為 subagent 的欄位而拋出 sqlite3.OperationalError
        （no such column: subagent），search_memories 的例外處理會將其
        吞掉並回傳空結果 —— 即使該子字串在記錄中逐字存在。
        """
        _insert_memory(
            conn,
            id="mem-1",
            kind="task_handoff",
            task_id="deny-subagent-reconnect",
            summary="修正 deny-subagent-reconnect 導致的連線失敗問題",
        )
        req = SearchRequest(query="deny-subagent", top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) == 1
        assert resp.results[0]["id"] == "mem-1"

    def test_colon_query_matches_literal_substring(self, conn):
        """查詢字串含冒號時，FTS5 會將其誤解為 column-filter（col:term）語法。"""
        _insert_memory(
            conn,
            id="mem-1",
            kind="task_handoff",
            summary="狀態更新 task:done 已完成部署",
        )
        req = SearchRequest(query="task:done", top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) == 1
        assert resp.results[0]["id"] == "mem-1"

    def test_underscore_query_matches(self, conn):
        """底線並非 FTS5 特殊字元，查詢應正常匹配（確保修復未破壞既有行為）。"""
        _insert_memory(
            conn,
            id="mem-1",
            kind="task_handoff",
            summary="更新 build_pipeline_v2 設定檔",
        )
        req = SearchRequest(query="build_pipeline_v2", top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) == 1
        assert resp.results[0]["id"] == "mem-1"

    def test_reserved_keyword_as_literal_search_term_still_works(self, conn):
        """AND/OR/NOT/NEAR 作為一般搜尋詞時，應仍能正常匹配（既有行為不受影響）。"""
        _insert_memory(
            conn,
            id="mem-1",
            kind="task_handoff",
            summary="採購清單：biscuits AND gravy 兩項",
        )
        req = SearchRequest(query="AND", top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) == 1
        assert resp.results[0]["id"] == "mem-1"

    def test_cjk_query_still_matches_after_special_char_fix(self, conn):
        """中文查詢應不受連字號/特殊字元修復影響，繼續正常匹配。"""
        _insert_memory(
            conn, id="mem-1", kind="task_handoff", summary="測試中文查詢內容是否正常"
        )
        req = SearchRequest(query="測試中文查詢", top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) == 1
        assert resp.results[0]["id"] == "mem-1"

    def test_short_query_still_short_circuits_after_special_char_fix(self, conn, caplog):
        """<3 字元短查詢應繼續維持既有的 short-circuit 行為（不受本次修復影響）。"""
        _insert_memory(conn, id="mem-1", kind="task_handoff", summary="ab test data")
        with caplog.at_level(logging.WARNING):
            req = SearchRequest(query="ab")
            resp = search_memories(conn, req)
        assert len(resp.results) == 0
        assert resp.has_more is False
        assert "too short" in caplog.text.lower()

    # -- 結果結構驗證 --

    def test_result_includes_all_fields(self, conn):
        _insert_memory(
            conn,
            id="mem-1",
            kind="task_handoff",
            summary="hello world",
            agent_id="test-agent",
            task_id="task-x",
        )
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

    # ================================================================
    # Regression: handoff_note / learnings / tags 遺漏 (task #20)
    # ================================================================

    def test_result_includes_handoff_note(self, conn):
        """Regression: 搜尋結果須包含 handoff_note 真實內容，不得為 None 或缺漏。

        修復前：_row_to_result 從未複製 handoff_note 欄位，即使資料庫中
        該筆記錄的 handoff_note 有實際內容，回傳的 dict 中也完全沒有這個
        key（而非只是空字串），導致呼叫端讀不到交接備註。
        """
        distinctive_note = "交接備註：連線池上限已調整為 50，勿再調高，否則會撞到雲端 RDS 連線上限"
        _insert_memory(
            conn,
            id="mem-1",
            kind="task_handoff",
            summary="hello world handoff",
            handoff_note=distinctive_note,
        )
        req = SearchRequest(query="hello world handoff", top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) == 1
        assert resp.results[0]["handoff_note"] == distinctive_note

    def test_result_includes_learnings_and_tags_as_decoded_lists(self, conn):
        """Regression: learnings/tags 存為 JSON 字串，回傳時須 json.loads 還原為 list。"""
        distinctive_learnings = ["連線池上限設 50 會撞到 RDS 上限", "重試機制需搭配指數退避"]
        distinctive_tags = ["db", "緊急", "connection-pool"]
        _insert_memory(
            conn,
            id="mem-1",
            kind="task_handoff",
            summary="hello world learnings test",
            learnings=distinctive_learnings,
            tags=distinctive_tags,
        )
        req = SearchRequest(query="hello world learnings", top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) == 1
        r = resp.results[0]
        assert isinstance(r["learnings"], list)
        assert r["learnings"] == distinctive_learnings
        assert isinstance(r["tags"], list)
        assert r["tags"] == distinctive_tags

    def test_result_backward_compatible_fields_unchanged(self, conn):
        """Regression: 新增欄位須為 additive，既有 8 個欄位須維持原值不變。"""
        _insert_memory(
            conn,
            id="mem-1",
            kind="task_handoff",
            summary="hello world compat",
            agent_id="test-agent",
            task_id="task-x",
            project_id="default",
        )
        req = SearchRequest(query="hello world compat", top_k=10)
        resp = search_memories(conn, req)
        r = resp.results[0]
        assert r["id"] == "mem-1"
        assert r["project_id"] == "default"
        assert r["summary"] == "hello world compat"
        assert r["agent_id"] == "test-agent"
        assert r["kind"] == "task_handoff"
        assert r["task_id"] == "task-x"
        assert "timestamp" in r
        assert isinstance(r["score"], (int, float))

    def test_result_includes_status_field(self, conn):
        """Regression: status 欄位也應被回傳（先前完全未複製）。"""
        _insert_memory(
            conn,
            id="mem-1",
            kind="task_handoff",
            summary="hello world status field",
            status="active",
        )
        req = SearchRequest(query="hello world status", top_k=10)
        resp = search_memories(conn, req)
        assert resp.results[0]["status"] == "active"

    def test_list_by_filters_path_also_includes_new_fields(self, conn):
        """無全文查詢、走 _list_by_filters 過濾路徑時，新欄位同樣須存在。"""
        distinctive_note = "list-by-filters 路徑的交接備註"
        _insert_memory(
            conn,
            id="mem-1",
            kind="task_handoff",
            summary="無關查詢字串內容",
            handoff_note=distinctive_note,
            task_id="task-filters",
        )
        req = SearchRequest(query="", task_id="task-filters", top_k=10)
        resp = search_memories(conn, req)
        assert len(resp.results) == 1
        assert resp.results[0]["handoff_note"] == distinctive_note


# ===================================================================
# get_status
# ===================================================================


class TestGetStatus:
    """get_status 整合測試 — 涵蓋去重、active 過濾、limit。"""

    def test_basic_status_query(self, conn):
        _insert_memory(
            conn,
            id="mem-1",
            kind="status_update",
            task_id="task-a",
            summary="進度 50%",
            status="active",
        )
        resp = get_status(conn, StatusRequest(limit=10))
        assert len(resp.latest) == 1
        assert resp.latest[0]["id"] == "mem-1"
        assert resp.latest[0]["task_id"] == "task-a"
        assert resp.latest[0]["kind"] == "status_update"

    def test_dedup_by_task_id_latest_wins(self, conn):
        """同一 task_id 多筆 status_update 時只取最新。"""
        _insert_memory(
            conn,
            id="mem-1",
            kind="status_update",
            task_id="task-a",
            summary="第一筆狀態",
            timestamp="2024-01-01T00:00:00.000000Z",
        )
        _insert_memory(
            conn,
            id="mem-2",
            kind="status_update",
            task_id="task-a",
            summary="第二筆（最新）",
            timestamp="2024-01-02T00:00:00.000000Z",
        )
        resp = get_status(conn, StatusRequest(limit=10))
        assert len(resp.latest) == 1
        assert resp.latest[0]["id"] == "mem-2"
        assert resp.latest[0]["summary"] == "第二筆（最新）"

    def test_only_active_status(self, conn):
        """只回傳 status='active' 的記錄。"""
        _insert_memory(
            conn,
            id="mem-1",
            kind="status_update",
            task_id="task-a",
            summary="active",
            status="active",
        )
        _insert_memory(
            conn,
            id="mem-2",
            kind="status_update",
            task_id="task-b",
            summary="superseded",
            status="superseded",
        )
        resp = get_status(conn, StatusRequest(limit=10))
        assert len(resp.latest) == 1
        assert resp.latest[0]["summary"] == "active"

    def test_only_status_update_kind(self, conn):
        """只回傳 kind='status_update' 的記錄。"""
        _insert_memory(
            conn,
            id="mem-1",
            kind="status_update",
            task_id="task-a",
            summary="狀態更新",
            status="active",
        )
        _insert_memory(
            conn,
            id="mem-2",
            kind="task_handoff",
            task_id="task-b",
            summary="交接記錄",
            status="active",
        )
        resp = get_status(conn, StatusRequest(limit=10))
        assert len(resp.latest) == 1
        assert resp.latest[0]["kind"] == "status_update"

    def test_multiple_tasks_all_returned(self, conn):
        """多個不同 task 的 active status 應全部回傳。"""
        _insert_memory(
            conn,
            id="mem-1",
            kind="status_update",
            task_id="task-a",
            summary="Task A 進行中",
            status="active",
        )
        _insert_memory(
            conn,
            id="mem-2",
            kind="status_update",
            task_id="task-b",
            summary="Task B 進行中",
            status="active",
        )
        _insert_memory(
            conn,
            id="mem-3",
            kind="status_update",
            task_id="task-c",
            summary="Task C 進行中",
            status="active",
        )
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
            _insert_memory(
                conn,
                id=f"mem-{i}",
                kind="status_update",
                task_id=f"task-{i}",
                summary=f"狀態 {i}",
                status="active",
            )
        resp = get_status(conn, StatusRequest(limit=3))
        assert len(resp.latest) == 3

    def test_no_active_status(self, conn):
        """沒有任何 active status_update 時回傳空陣列。"""
        resp = get_status(conn, StatusRequest(limit=10))
        assert resp.latest == []

    def test_response_field_completeness(self, conn):
        _insert_memory(
            conn,
            id="mem-1",
            kind="status_update",
            task_id="task-a",
            summary="完整欄位測試",
            status="active",
            agent_id="agent-x",
            tags=["urgent"],
        )
        resp = get_status(conn, StatusRequest(limit=10))
        r = resp.latest[0]
        assert r["id"] == "mem-1"
        assert r["task_id"] == "task-a"
        assert r["agent_id"] == "agent-x"
        assert r["kind"] == "status_update"
        assert r["summary"] == "完整欄位測試"
        assert r["status"] == "active"
        assert "timestamp" in r

    # ================================================================
    # Regression: handoff_note / learnings / tags 遺漏 (task #20)
    # ================================================================

    def test_status_result_includes_handoff_note_learnings_tags(self, conn):
        """Regression: get_status 內建 dict 從未複製 handoff_note/learnings/tags，
        與 search_memories 的 _row_to_result 有相同的欄位遺漏問題。
        """
        distinctive_note = "status 交接備註：部署已卡在 canary 階段，勿自動 rollback"
        distinctive_learnings = ["canary 階段需人工確認才能 promote"]
        distinctive_tags = ["deploy", "canary"]
        _insert_memory(
            conn,
            id="mem-1",
            kind="status_update",
            task_id="task-a",
            summary="canary 部署中",
            status="active",
            handoff_note=distinctive_note,
            learnings=distinctive_learnings,
            tags=distinctive_tags,
        )
        resp = get_status(conn, StatusRequest(limit=10))
        assert len(resp.latest) == 1
        r = resp.latest[0]
        assert r["handoff_note"] == distinctive_note
        assert isinstance(r["learnings"], list)
        assert r["learnings"] == distinctive_learnings
        assert isinstance(r["tags"], list)
        assert r["tags"] == distinctive_tags

    def test_status_result_backward_compatible_fields_unchanged(self, conn):
        """Regression: get_status 新增欄位須為 additive，既有欄位維持原值不變。"""
        _insert_memory(
            conn,
            id="mem-1",
            kind="status_update",
            task_id="task-a",
            agent_id="agent-x",
            summary="既有欄位相容性測試",
            status="active",
        )
        resp = get_status(conn, StatusRequest(limit=10))
        r = resp.latest[0]
        assert r["id"] == "mem-1"
        assert r["project_id"] == "default"
        assert r["task_id"] == "task-a"
        assert r["agent_id"] == "agent-x"
        assert r["kind"] == "status_update"
        assert r["summary"] == "既有欄位相容性測試"
        assert r["status"] == "active"
        assert "timestamp" in r
