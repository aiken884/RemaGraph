# SPDX-License-Identifier: Apache-2.0
"""對抗式審查（第二輪）修復的回歸測試。

涵蓋：
1. serve 啟動路徑（_bind_project 的啟動診斷）不得在安全驗證前污染 registry
   ——第一輪修復只堵了 safety_validate_project 直呼路徑，serve 是最常見的
   長駐入口卻留了一模一樣的繞過（對抗式審查實測發現）。
2. `remagraph --project X store ...` 不得靜默啟動 stdio server 掛住終端。
3. 存量 DB（修復前由 v6 程式建立、錯種 min_writer_version=6）在下一次
   connect 時回填為 5。
4. FTS5 保留字短查詢（"or" → sanitize 成 '"or"'）的引號計長——第一輪
   修復無任何測試防護（revert 實測全綠）。
5. _query_single_db_for_request 的 status 預設 active——第一輪的 fanout
   測試全部注入自訂 query_fn 繞過了這個函式（revert 實測全綠）。
6. migrate 的 COMMIT/ROLLBACK 各自保護——revert 實測全綠。
7. process_store 用 BEGIN IMMEDIATE 開交易——revert 實測全綠（SQL spy，
   防 silent revert；真併發行為由 SQLite 語意保證）。
8. search --status all 逃生口（status=None 收斂為 active 後的顯式全查）。
9. invalidate_constraints 的冪等回報（混合 active/非 active 請求）。
10. fanout 的 BM25 scored 分支排序。
11. remagraph_maintain 綁定匹配時的正向路徑。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import remagraph.server as server
from remagraph import db as db_mod
from remagraph import maintenance as maintenance_mod
from remagraph import store as store_mod
from remagraph.arbitration import invalidate_constraints
from remagraph.db import _init_schema
from remagraph.models import SearchRequest
from remagraph.search import (
    _cross_project_fanout,
    _query_single_db_for_request,
    search_memories,
)

SUMMARY = "一筆長度足夠通過仲裁下限的測試 summary，內容填充填充填充填充"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)
    monkeypatch.delenv("REMAGRAPH_PROJECT", raising=False)
    home = tmp_path / "fake-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        db_mod, "DEFAULT_STATE_DIR", home / ".local" / "state" / "remagraph"
    )


def _insert_memory(
    conn: sqlite3.Connection,
    *,
    mem_id: str,
    summary: str = SUMMARY,
    kind: str = "status_update",
    task_id: str = "task-1",
    status: str = "active",
    project_id: str = "default",
    created_at: str = "2026-07-24T00:00:00Z",
) -> None:
    conn.execute(
        "INSERT INTO memories (id, project_id, kind, task_id, agent_id, timestamp, "
        "summary, learnings, handoff_note, tags, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'agent-1', ?, ?, '[]', '', '[]', ?, ?, ?)",
        (mem_id, project_id, kind, task_id, created_at, summary, status,
         created_at, created_at),
    )


def _mem_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# 1. serve 啟動路徑的 registry 污染
# ---------------------------------------------------------------------------


def test_bind_project_failure_does_not_clobber_registry(monkeypatch, tmp_path):
    dir_a = tmp_path / "state-a"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(dir_a))
    conn = db_mod.connect(project_id="proj-a")
    conn.close()
    assert db_mod.get_registered_state_dir("proj-a") == str(dir_a.resolve())

    dir_b = tmp_path / "state-b"
    dir_b.mkdir()
    (dir_b / "project.json").write_text(
        json.dumps({"project_id": "proj-b", "state_dir": str(dir_b)}),
        encoding="utf-8",
    )

    # serve 啟動的事故形狀：行程繼承 proj-b 的 env、帶 proj-a 啟動
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(dir_b))
    with pytest.raises(Exception):
        server._bind_project("proj-a")

    assert db_mod.get_registered_state_dir("proj-a") == str(dir_a.resolve()), (
        "serve 啟動診斷在安全驗證前污染了 registry"
    )


# ---------------------------------------------------------------------------
# 2. --project 前置帶子命令
# ---------------------------------------------------------------------------


def test_leading_project_flag_with_subcommand_errors_instead_of_serving(
    monkeypatch, capsys
):
    import sys as _sys

    run_serve = MagicMock()
    monkeypatch.setattr(server, "_run_serve", run_serve)
    monkeypatch.setattr(
        _sys, "argv", ["remagraph", "--project", "x", "store", "--task-id", "t"]
    )
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "store" in err
    run_serve.assert_not_called()


# ---------------------------------------------------------------------------
# 3. 存量 DB 的 min_writer_version 回填
# ---------------------------------------------------------------------------


def test_existing_db_with_misseeded_min_writer_is_backfilled(tmp_path, monkeypatch):
    state_dir = tmp_path / "legacy-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    conn = db_mod.connect(project_id="legacy-proj")
    # 模擬修復前 v6 程式錯種的值
    conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('min_writer_version', '6')"
    )
    conn.commit()
    conn.close()

    conn = db_mod.connect(project_id="legacy-proj")
    row = conn.execute(
        "SELECT value FROM _meta WHERE key='min_writer_version'"
    ).fetchone()
    conn.close()
    assert row[0] == "5", "存量 DB 錯種的 min_writer_version=6 未被回填"


# ---------------------------------------------------------------------------
# 4. FTS5 保留字短查詢的引號計長
# ---------------------------------------------------------------------------


def test_reserved_word_short_query_falls_back_to_list_mode():
    conn = _mem_conn()
    _insert_memory(conn, mem_id="mem-1")
    # sanitize 把 "or" 包成 '"or"'（長度 4）——修復前引號被算進長度而繞過
    # 短查詢攔截，走 FTS 對 2 字元 phrase 零 trigram 命中、永遠回空。
    resp = search_memories(conn, SearchRequest(query="or", task_id="task-1", top_k=10))
    assert [r["id"] for r in resp.results] == ["mem-1"]
    conn.close()


# ---------------------------------------------------------------------------
# 5. _query_single_db_for_request 的 status 語意（真 SQL，不經 mock query_fn）
# ---------------------------------------------------------------------------


class TestQuerySingleDbStatus:
    def test_defaults_to_active_only(self):
        conn = _mem_conn()
        _insert_memory(conn, mem_id="mem-a", summary="deployment pipeline note")
        _insert_memory(
            conn, mem_id="mem-s", summary="deployment pipeline old",
            status="superseded",
        )
        rows = _query_single_db_for_request(
            conn, SearchRequest(query="deployment", top_k=10),
            apply_project_filter=False,
        )
        assert [r["id"] for r in rows] == ["mem-a"]
        conn.close()

    def test_status_all_returns_everything(self):
        conn = _mem_conn()
        _insert_memory(conn, mem_id="mem-a", summary="deployment pipeline note")
        _insert_memory(
            conn, mem_id="mem-s", summary="deployment pipeline old",
            status="superseded",
        )
        rows = _query_single_db_for_request(
            conn, SearchRequest(query="deployment", status="all", top_k=10),
            apply_project_filter=False,
        )
        assert {r["id"] for r in rows} == {"mem-a", "mem-s"}
        conn.close()


def test_search_memories_status_all_escape_hatch():
    conn = _mem_conn()
    _insert_memory(conn, mem_id="mem-a", summary="deployment pipeline note")
    _insert_memory(
        conn, mem_id="mem-s", summary="deployment pipeline old", status="superseded"
    )
    resp = search_memories(conn, SearchRequest(query="deployment", status="all", top_k=10))
    assert {r["id"] for r in resp.results} == {"mem-a", "mem-s"}
    conn.close()


# ---------------------------------------------------------------------------
# 6. migrate 的 COMMIT/ROLLBACK 各自保護
# ---------------------------------------------------------------------------


class _CommitFailsConn:
    """代理 source 連線：COMMIT 拋 disk I/O、（模擬自動回滾後）ROLLBACK 拋
    cannot rollback。"""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real

    def execute(self, sql: str, *args: Any):
        stripped = sql.strip().upper()
        if stripped == "COMMIT":
            raise sqlite3.OperationalError("disk I/O error")
        if stripped == "ROLLBACK":
            raise sqlite3.OperationalError(
                "cannot rollback - no transaction is active"
            )
        return self._real.execute(sql, *args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def test_migrate_source_commit_failure_surfaces_original_error(tmp_path, monkeypatch):
    src_dir = tmp_path / "src-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(src_dir))
    conn = db_mod.connect(project_id="proj-src")
    _insert_memory(
        conn, mem_id="mem-20260101-001", project_id="proj-src",
        task_id="proj-dst-task",
    )
    conn.commit()
    conn.close()
    monkeypatch.delenv("REMAGRAPH_STATE_DIR")

    dst_dir = Path(db_mod.DEFAULT_STATE_DIR).parent / "remagraph-proj-dst"
    dst_dir.mkdir(parents=True)
    db_mod.connect_at_state_dir(dst_dir).close()

    real_connect_at_state_dir = db_mod.connect_at_state_dir

    def patched(state_dir: Path) -> Any:
        real = real_connect_at_state_dir(state_dir)
        if Path(state_dir).resolve() == src_dir.resolve():
            return _CommitFailsConn(real)
        return real

    monkeypatch.setattr(db_mod, "connect_at_state_dir", patched)
    monkeypatch.setattr(store_mod._db, "connect_at_state_dir", patched)

    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        # 修復前：except 裡對已 COMMIT 的 conn_tgt 執行 ROLLBACK 再拋
        # "cannot rollback"，遮蔽原始的 disk I/O error。
        store_mod.migrate_project_memories("proj-src", "proj-dst")


# ---------------------------------------------------------------------------
# 7. process_store 的 BEGIN IMMEDIATE（SQL spy，防 silent revert）
# ---------------------------------------------------------------------------


class _SqlSpyConn:
    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real
        self.sql_log: list[str] = []

    def execute(self, sql: str, *args: Any):
        self.sql_log.append(sql.strip())
        return self._real.execute(sql, *args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def test_process_store_opens_transaction_with_begin_immediate(tmp_path, monkeypatch):
    from remagraph.models import StoreRequest

    state_dir = tmp_path / "spy-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    real = db_mod.connect(project_id="spy-proj")
    spy = _SqlSpyConn(real)
    request = StoreRequest(
        project_id="spy-proj", task_id="spy-proj-task-1", agent_id="agent-1",
        kind="task_handoff", summary=SUMMARY,
        learnings=["一條有效的 learning 記錄"],
        handoff_note="一段長度足夠通過驗證的 handoff note 內容",
        tags=[],
    )
    response = store_mod.process_store(request, spy)
    assert response.status == "stored"
    begins = [s for s in spy.sql_log if s.upper().startswith("BEGIN")]
    assert begins, "process_store 沒有開啟交易"
    assert all("IMMEDIATE" in s.upper() for s in begins), (
        f"process_store 的交易不是 BEGIN IMMEDIATE（deferred 交易下併發"
        f"取號必撞 PRIMARY KEY）: {begins}"
    )
    real.close()


# ---------------------------------------------------------------------------
# 9. invalidate 的冪等回報（混合 active / 非 active）
# ---------------------------------------------------------------------------


def test_invalidate_mixed_statuses_reports_only_actually_updated():
    conn = _mem_conn()
    _insert_memory(
        conn, mem_id="mem-act", kind="discovered_constraint", status="active"
    )
    _insert_memory(
        conn, mem_id="mem-inv", kind="discovered_constraint", status="invalidated"
    )
    result = invalidate_constraints(["mem-act", "mem-inv"], conn)
    assert result.invalidated_count == 1
    assert result.invalidated_ids == ["mem-act"]
    conn.close()


# ---------------------------------------------------------------------------
# 10. fanout 的 BM25 scored 分支排序
# ---------------------------------------------------------------------------


def test_fanout_scored_results_sorted_by_bm25_ascending(monkeypatch):
    own = _mem_conn()
    _insert_memory(own, mem_id="own-worse", summary="alpha")
    foreign = _mem_conn()
    _insert_memory(foreign, mem_id="for-better", summary="alpha", project_id="other")

    scores = {"own-worse": -1.0, "for-better": -5.0}  # BM25 越低越相關

    def scored_query(c: sqlite3.Connection) -> list[sqlite3.Row]:
        inner = c.execute("SELECT * FROM memories").fetchall()
        out = []
        for r in inner:
            row = c.execute(
                "SELECT m.*, ? AS score FROM memories m WHERE m.id = ?",
                (scores[r["id"]], r["id"]),
            ).fetchone()
            out.append(row)
        return out

    monkeypatch.setattr(db_mod, "connect_foreign_project_readonly", lambda pid: foreign)
    monkeypatch.setattr(db_mod, "get_registered_state_dir", lambda pid: None)
    resp = _cross_project_fanout(
        own,
        SearchRequest(query="alpha", top_k=1),
        own_project_id="me",
        candidate_project_ids=["other"],
        query_fn=scored_query,
        cap=10,
        log_label="test",
    )
    assert [r["id"] for r in resp.results] == ["for-better"], (
        "scored 分支未依 BM25 升冪重排（更相關的 foreign 命中被截掉）"
    )
    own.close()


# ---------------------------------------------------------------------------
# 11. remagraph_maintain 綁定匹配時的正向路徑
# ---------------------------------------------------------------------------


def test_remagraph_maintain_matching_project_proceeds(monkeypatch):
    monkeypatch.setattr(server, "_bound_project_id", "proj-a")
    monkeypatch.setattr(
        maintenance_mod, "safety_validate_project", lambda pid: Path("/tmp/x")
    )
    monkeypatch.setattr(server, "safety_validate_project", lambda pid: Path("/tmp/x"))
    monkeypatch.setattr(
        server, "run_maintenance", lambda policy, pid, force=False: {"ok": True}
    )
    result = server.remagraph_maintain(project_id="proj-a")
    assert result["status"] == "ok", f"綁定匹配的 maintain 未執行: {result}"
