# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the MCP tool remagraph_migrate_project.

背景：修復前，這個 tool 是一個純粹的空殼 -- 只呼叫
safety_validate_project(to_project, ...) 驗證目標專案，然後回傳一則寫死的
假訊息「Migration logic has been triggered (see CLI migrate-project for
details)」，完全不執行任何實際的資料搬移，也從不真的標記來源記錄
invalidated。真正的（且原本也有 bug 的）遷移邏輯只存在 CLI 的
cmd_migrate_project 裡。

本次修復後，remagraph_migrate_project 呼叫與 CLI 共用的
store.migrate_project_memories()，本檔驗證：
1. 呼叫後，來源那幾筆記錄真的被標記 invalidated，目標資料庫真的多了對應
   筆數的 active 記錄（不再是假訊息）。
2. dry_run=True 誠實回報會遷移幾筆，而不是永遠回傳 0 或不確定；且與後續
   真的執行時的實際遷移筆數一致。
3. 未登記的 from_project、唯讀降級目標，都回傳乾淨的
   {"status": "error", "reason": ...}，而不是讓例外原樣往外傳（沿用既有
   MCP tool 的錯誤處理慣例，見 test_server.py 對 MigrationError 的既有
   測試）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import remagraph.server as server
from remagraph import db as db_mod


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)
    monkeypatch.delenv("REMAGRAPH_PROJECT", raising=False)
    home = tmp_path / "fake-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(db_mod, "DEFAULT_STATE_DIR", home / ".local" / "state" / "remagraph")
    server._conn = None
    yield home
    server._conn = None


def _insert_memory(conn: sqlite3.Connection, *, mem_id: str, project_id: str, tags):
    now = "2026-07-24T00:00:00Z"
    summary = "足夠長的摘要內容用來通過仲裁規則檢查門檻，至少三十個中文字元以上"
    conn.execute(
        """
        INSERT INTO memories (
            id, project_id, kind, task_id, agent_id, timestamp,
            summary, learnings, handoff_note, tags, status, created_at, updated_at
        ) VALUES (?, ?, 'task_handoff', 'task-fixture', 'agent-fixture', ?,
                  ?, '[]', '', ?, 'active', ?, ?)
        """,
        (mem_id, project_id, now, summary, json.dumps(tags), now, now),
    )


def _read_row(db_path: Path, mem_id: str):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM memories WHERE id = ?", (mem_id,)).fetchone()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1：真正的遷移，不再是假訊息
# ---------------------------------------------------------------------------


def test_migrate_tool_actually_moves_data_not_a_canned_message(tmp_path, monkeypatch):
    proj_a_dir = tmp_path / "proj-a-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(proj_a_dir))
    conn = db_mod.connect(project_id="proj-a")
    _insert_memory(conn, mem_id="mem-a-1", project_id="proj-a", tags=["proj-b"])
    conn.close()

    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path / "caller-state"))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "caller-proj")

    result = server.remagraph_migrate_project(
        from_project="proj-a", to_project="proj-b", dry_run=False
    )

    assert result["status"] == "ok"
    assert result["from"] == "proj-a"
    assert result["to"] == "proj-b"
    assert result["dry_run"] is False
    assert result["migrated_count"] == 1
    assert "message" not in result  # 舊的假訊息欄位不應再出現

    src_row = _read_row(proj_a_dir / "remagraph.db", "mem-a-1")
    assert src_row["status"] == "invalidated"

    to_state = db_mod.get_registered_state_dir("proj-b")
    assert to_state is not None
    tgt_row = _read_row(Path(to_state) / "remagraph.db", "mem-a-1")
    assert tgt_row is not None
    assert tgt_row["project_id"] == "proj-b"
    assert tgt_row["status"] == "active"


# ---------------------------------------------------------------------------
# 2：dry_run 誠實回報，且與實際執行一致
# ---------------------------------------------------------------------------


def test_migrate_tool_dry_run_matches_subsequent_real_run(tmp_path, monkeypatch):
    proj_a_dir = tmp_path / "proj-a-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(proj_a_dir))
    conn = db_mod.connect(project_id="proj-a")
    for i in range(5):
        _insert_memory(conn, mem_id=f"mem-a-{i}", project_id="proj-a", tags=["proj-b"])
    conn.close()

    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path / "caller-state"))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "caller-proj")

    dry = server.remagraph_migrate_project(
        from_project="proj-a", to_project="proj-b", dry_run=True
    )
    assert dry["status"] == "dry-run"
    assert dry["dry_run"] is True
    assert dry["migrated_count"] == 5

    real = server.remagraph_migrate_project(
        from_project="proj-a", to_project="proj-b", dry_run=False
    )
    assert real["migrated_count"] == dry["migrated_count"] == 5


# ---------------------------------------------------------------------------
# 3：錯誤情境回傳乾淨的 {"status": "error", ...}，不讓例外原樣往外傳
# ---------------------------------------------------------------------------


def test_migrate_tool_returns_clean_error_for_unregistered_from_project(tmp_path, monkeypatch):
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path / "caller-state"))

    result = server.remagraph_migrate_project(
        from_project="never-registered-project", to_project="proj-b"
    )
    assert result["status"] == "error"
    assert "never-registered-project" in result["reason"]


def test_migrate_tool_returns_clean_error_for_read_only_target(tmp_path, monkeypatch):
    proj_a_dir = tmp_path / "proj-a-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(proj_a_dir))
    conn = db_mod.connect(project_id="proj-a")
    _insert_memory(conn, mem_id="mem-a-1", project_id="proj-a", tags=["proj-ro"])
    conn.close()

    to_state = tmp_path / "proj-ro-state"
    db_mod.connect_at_state_dir(to_state).close()
    ro_conn = sqlite3.connect(str(to_state / "remagraph.db"))
    ro_conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', ?)",
        (str(db_mod.SCHEMA_VERSION + 1),),
    )
    ro_conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES ('min_reader_version', '1')")
    ro_conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('min_writer_version', ?)",
        (str(db_mod.SCHEMA_VERSION + 1),),
    )
    ro_conn.commit()
    ro_conn.close()

    # env 綁架修復後 to 解析走 registry 優先（無視 REMAGRAPH_STATE_DIR），
    # 用明確登記讓目標指到唯讀降級的目錄。
    db_mod.register_known_project("proj-ro", to_state)

    result = server.remagraph_migrate_project(from_project="proj-a", to_project="proj-ro")
    assert result["status"] == "error"
    assert "proj-ro" in result["reason"]

    src_row = _read_row(proj_a_dir / "remagraph.db", "mem-a-1")
    assert src_row["status"] == "active"
