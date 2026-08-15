# SPDX-License-Identifier: Apache-2.0
"""全專案診斷（批次 2）的 search 回歸測試。

涵蓋四個診斷發現：
1. 多 token 短詞查詢（每個 token < 3 字元，總長 ≥ 3）繞過短查詢閘門後，
   FTS5 trigram 對短 phrase 產生零 token → 永遠回空，帶過濾條件時也不會
   落入列表模式 fallback（CJK 空白分隔兩字詞是常見輸入）。
2. FTS 主路徑在 status=None 時完全不過濾 status，superseded/invalidated
   混入結果——與列表模式（預設只回 active）語意不一致。
3. _cross_project_fanout 合併結果未重新排序就截斷 top_k，自己專案的低相關
   命中會系統性擠掉其他專案更相關/更新的命中。
4. fan-out 韌性缺口：(a) 候選 DB 損毀拋 sqlite3.DatabaseError（非
   OperationalError）會炸掉整個搜尋；(b) own 連線的 rows 一律標
   own_project_id，共用 DB 時別的專案記憶被錯誤標記來源；(c) 不可達候選
   已先計入 candidates_searched，統計虛報。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import remagraph.db as db_mod
from remagraph.db import _init_schema
from remagraph.models import SearchRequest
from remagraph.search import _cross_project_fanout, search_memories


def _iso(offset_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _insert_memory(
    conn: sqlite3.Connection,
    *,
    id: str,
    summary: str,
    kind: str = "status_update",
    task_id: str = "task-1",
    agent_id: str = "agent-1",
    status: str = "active",
    project_id: str = "default",
    created_at: str | None = None,
) -> None:
    ts = created_at or _iso()
    conn.execute(
        "INSERT INTO memories (id, project_id, kind, task_id, agent_id, timestamp, summary, "
        "learnings, handoff_note, tags, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (id, project_id, kind, task_id, agent_id, ts, summary,
         json.dumps([]), "", json.dumps([]), status, ts, ts),
    )


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path))
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# 1. 多 token 短詞查詢
# ---------------------------------------------------------------------------


class TestMultiShortTokenQuery:
    def test_cjk_two_short_tokens_with_filter_falls_back_to_list_mode(self, conn):
        """「資料 搜尋」（兩個 2 字詞）＋ task_id 過濾 → 必須走列表模式
        回傳該 task 的記憶，不得因 trigram 零命中而回空。"""
        _insert_memory(conn, id="mem-1", summary="這筆記憶同時含有資料與搜尋兩個詞")
        resp = search_memories(
            conn, SearchRequest(query="資料 搜尋", task_id="task-1", top_k=10)
        )
        assert [r["id"] for r in resp.results] == ["mem-1"]

    def test_cjk_two_short_tokens_without_filter_returns_empty_with_warning(
        self, conn, caplog
    ):
        _insert_memory(conn, id="mem-1", summary="這筆記憶同時含有資料與搜尋兩個詞")
        import logging

        with caplog.at_level(logging.WARNING, logger="remagraph.search"):
            resp = search_memories(conn, SearchRequest(query="資料 搜尋", top_k=10))
        assert resp.results == []
        assert any("short" in r.message.lower() for r in caplog.records)

    def test_mixed_query_still_matches_on_long_token(self, conn):
        """「search ab」：短 token 'ab' 不能讓長 token 'search' 的檢索失效。"""
        _insert_memory(conn, id="mem-1", summary="the search subsystem works")
        resp = search_memories(conn, SearchRequest(query="search ab", top_k=10))
        assert [r["id"] for r in resp.results] == ["mem-1"]


# ---------------------------------------------------------------------------
# 2. FTS 路徑 status 預設過濾
# ---------------------------------------------------------------------------


class TestFtsStatusDefault:
    def test_fts_defaults_to_active_only(self, conn):
        _insert_memory(conn, id="mem-a", summary="deployment pipeline note", status="active")
        _insert_memory(
            conn, id="mem-s", summary="deployment pipeline note old", status="superseded"
        )
        resp = search_memories(conn, SearchRequest(query="deployment", top_k=10))
        assert [r["id"] for r in resp.results] == ["mem-a"]

    def test_fts_explicit_status_still_works(self, conn):
        _insert_memory(conn, id="mem-a", summary="deployment pipeline note", status="active")
        _insert_memory(
            conn, id="mem-s", summary="deployment pipeline note old", status="superseded"
        )
        resp = search_memories(
            conn, SearchRequest(query="deployment", status="superseded", top_k=10)
        )
        assert [r["id"] for r in resp.results] == ["mem-s"]


# ---------------------------------------------------------------------------
# 3/4. _cross_project_fanout 排序、韌性、來源標記、統計
# ---------------------------------------------------------------------------


def _make_db(rows: list[dict]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    for r in rows:
        _insert_memory(conn, **r)
    return conn


def _list_all(c: sqlite3.Connection) -> list[sqlite3.Row]:
    return c.execute(
        "SELECT * FROM memories WHERE status='active' ORDER BY created_at DESC"
    ).fetchall()


class TestFanoutMergeAndResilience:
    def test_merged_results_are_resorted_before_truncation(self, monkeypatch):
        """own 專案 2 筆較舊、foreign 專案 1 筆最新，top_k=2 →
        foreign 的最新記憶必須擠進結果，不得因 own-first 拼接被截掉。"""
        own = _make_db(
            [
                {"id": "own-1", "summary": "old one", "created_at": _iso(-300)},
                {"id": "own-2", "summary": "old two", "created_at": _iso(-200)},
            ]
        )
        foreign = _make_db(
            [{"id": "for-1", "summary": "newest", "project_id": "other",
              "created_at": _iso(-10)}]
        )
        monkeypatch.setattr(
            db_mod, "connect_foreign_project_readonly", lambda pid: foreign
        )
        monkeypatch.setattr(db_mod, "get_registered_state_dir", lambda pid: None)
        resp = _cross_project_fanout(
            own,
            SearchRequest(query="", top_k=2),
            own_project_id="me",
            candidate_project_ids=["other"],
            query_fn=_list_all,
            cap=10,
            log_label="test",
        )
        ids = [r["id"] for r in resp.results]
        assert "for-1" in ids, f"foreign 最新記憶被截掉了: {ids}"

    def test_database_error_from_candidate_is_skipped(self, monkeypatch):
        own = _make_db([{"id": "own-1", "summary": "hello"}])
        foreign = _make_db([])

        def broken_query(c: sqlite3.Connection) -> list[sqlite3.Row]:
            if c is not own:
                raise sqlite3.DatabaseError("database disk image is malformed")
            return _list_all(c)

        monkeypatch.setattr(
            db_mod, "connect_foreign_project_readonly", lambda pid: foreign
        )
        monkeypatch.setattr(db_mod, "get_registered_state_dir", lambda pid: None)
        resp = _cross_project_fanout(
            own,
            SearchRequest(query="", top_k=10),
            own_project_id="me",
            candidate_project_ids=["corrupt"],
            query_fn=broken_query,
            cap=10,
            log_label="test",
        )
        assert [r["id"] for r in resp.results] == ["own-1"]

    def test_own_rows_source_project_uses_row_project_id(self, monkeypatch):
        """own 連線（共用 DB）撈到別的專案的 row 時，source_project_id
        必須反映 row 自己的 project_id，不得一律標成 own_project_id。"""
        own = _make_db(
            [
                {"id": "mine", "summary": "mine", "project_id": "me"},
                {"id": "theirs", "summary": "theirs", "project_id": "other-proj"},
            ]
        )
        monkeypatch.setattr(db_mod, "get_registered_state_dir", lambda pid: None)
        resp = _cross_project_fanout(
            own,
            SearchRequest(query="", top_k=10),
            own_project_id="me",
            candidate_project_ids=[],
            query_fn=_list_all,
            cap=10,
            log_label="test",
        )
        by_id = {r["id"]: r for r in resp.results}
        assert by_id["theirs"]["source_project_id"] == "other-proj"
        assert by_id["mine"]["source_project_id"] == "me"

    def test_unreachable_candidate_not_counted_as_searched(self, monkeypatch):
        own = _make_db([{"id": "own-1", "summary": "hello"}])
        monkeypatch.setattr(
            db_mod, "connect_foreign_project_readonly", lambda pid: None
        )
        monkeypatch.setattr(db_mod, "get_registered_state_dir", lambda pid: None)
        resp = _cross_project_fanout(
            own,
            SearchRequest(query="", top_k=10),
            own_project_id="me",
            candidate_project_ids=["gone-1", "gone-2"],
            query_fn=_list_all,
            cap=10,
            log_label="test",
        )
        assert resp.candidates_searched == 0
        assert resp.candidates_total == 2
