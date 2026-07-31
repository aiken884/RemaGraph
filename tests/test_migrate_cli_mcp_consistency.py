# SPDX-License-Identifier: Apache-2.0
"""CLI `migrate-project` 與 MCP tool `remagraph_migrate_project` 對同一個
遷移情境，必須產生一致的最終資料庫狀態 -- 兩者現在共用同一個核心實作
(store.migrate_project_memories())，這裡驗證這個共用真的生效，而不是各自
還藏著一份獨立、可能行為分歧的邏輯。

兩條路徑各自使用完全獨立的 tmp state_dir 執行同一個遷移情境（相同的
project 名稱、相同的來源記錄），跑完後比較兩邊資料庫的最終內容結構
（排除遷移當下的即時時間戳記——那本來就預期不同）是否一致。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import remagraph.cli as cli_mod
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


def _read_row(db_path: Path, mem_id: str) -> dict:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (mem_id,)).fetchone()
        assert row is not None
        return dict(row)
    finally:
        conn.close()


def _normalize(row: dict) -> dict:
    """去掉遷移當下才會產生、預期本來就會不同的欄位（learnings 裡含即時
    時間戳記），其餘欄位必須完全一致。"""
    normalized = dict(row)
    learnings = json.loads(normalized.pop("learnings"))
    normalized["learnings_count"] = len(learnings)
    normalized["learnings_has_migration_breadcrumb"] = any(
        entry.startswith("migrated-to:") for entry in learnings
    )
    return normalized


def _run_one_scenario(tmp_path, monkeypatch, *, via: str) -> tuple[dict, dict]:
    """建一個獨立的來源/目標情境、跑一次遷移（CLI 或 MCP），回傳
    (來源已標記 invalidated 的那一列, 目標新增的那一列) 的 normalize 後
    結果。"""
    scenario_root = tmp_path / via
    proj_a_dir = scenario_root / "proj-a-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(proj_a_dir))
    monkeypatch.delenv("REMAGRAPH_PROJECT", raising=False)
    conn = db_mod.connect(project_id="proj-a")
    _insert_memory(conn, mem_id="mem-a-1", project_id="proj-a", tags=["proj-b"])
    conn.close()

    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(scenario_root / "caller-state"))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "caller-proj")

    if via == "cli":
        cli_mod.main(["migrate-project", "--from", "proj-a", "--to", "proj-b", "--force"])
    elif via == "mcp":
        result = server.remagraph_migrate_project(
            from_project="proj-a", to_project="proj-b", dry_run=False
        )
        assert result["status"] == "ok"
    else:
        raise AssertionError(via)

    src_row = _read_row(proj_a_dir / "remagraph.db", "mem-a-1")

    to_state = db_mod.get_registered_state_dir("proj-b")
    assert to_state is not None
    tgt_row = _read_row(Path(to_state) / "remagraph.db", "mem-a-1")

    return _normalize(src_row), _normalize(tgt_row)


def test_cli_and_mcp_paths_produce_structurally_consistent_final_state(
    tmp_path, monkeypatch, capsys
):
    cli_src, cli_tgt = _run_one_scenario(tmp_path, monkeypatch, via="cli")
    capsys.readouterr()  # 清掉 CLI print 輸出，不干擾後續斷言

    mcp_src, mcp_tgt = _run_one_scenario(tmp_path, monkeypatch, via="mcp")

    assert cli_src == mcp_src
    assert cli_tgt == mcp_tgt

    # 額外釘住兩邊都真的完成了遷移（不是兩邊都巧合地什麼都沒做而『一致』）。
    assert cli_src["status"] == "invalidated"
    assert cli_src["learnings_has_migration_breadcrumb"] is True
    assert cli_tgt["status"] == "active"
    assert cli_tgt["project_id"] == "proj-b"
