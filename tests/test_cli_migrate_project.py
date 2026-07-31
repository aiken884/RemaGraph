# SPDX-License-Identifier: Apache-2.0
"""Regression tests for `remagraph migrate-project` (cli.cmd_migrate_project).

背景：cmd_migrate_project 過去是唯一真正存在的遷移實作，但把來源資料庫
路徑寫死為 Path.home()/".local/state/remagraph/remagraph.db"（隱含假設
from_project 永遠是 'default'）。本次修復把核心邏輯抽到
store.migrate_project_memories()，cmd_migrate_project 現在只是把結構化
結果轉成既有的 print()/sys.exit(1) 使用者體驗的薄 wrapper。

本檔驗證：CLI 端行為（成功遷移的輸出格式、dry-run 誠實回報筆數、來源
未登記時的清楚錯誤、--from/--to 相同時的既有錯誤）維持可用，且真的呼叫到
共用核心邏輯（而不是又一次寫死路徑）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import remagraph.cli as cli_mod
from remagraph import db as db_mod


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)
    monkeypatch.delenv("REMAGRAPH_PROJECT", raising=False)
    home = tmp_path / "fake-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(db_mod, "DEFAULT_STATE_DIR", home / ".local" / "state" / "remagraph")
    return home


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


def _migrate_args(from_proj: str, to_proj: str, *, dry_run: bool = False, force: bool = False):
    args = ["migrate-project", "--from", from_proj, "--to", to_proj]
    if dry_run:
        args.append("--dry-run")
    if force:
        args.append("--force")
    return args


# ---------------------------------------------------------------------------
# --from == --to -> 既有的快速拒絕（在呼叫共用函式之前就攔下）
# ---------------------------------------------------------------------------


def test_from_equals_to_rejected_before_touching_any_db(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path / "caller-state"))
    with pytest.raises(SystemExit) as ei:
        cli_mod.main(_migrate_args("same-proj", "same-proj"))
    assert ei.value.code == 1
    assert "cannot be the same" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 未登記的 from_project -> 清楚的錯誤 + exit 1
# ---------------------------------------------------------------------------


def test_unregistered_from_project_exits_with_clear_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path / "caller-state"))
    with pytest.raises(SystemExit) as ei:
        cli_mod.main(_migrate_args("totally-unknown-project", "proj-target"))
    assert ei.value.code == 1
    err = capsys.readouterr().err
    assert "totally-unknown-project" in err


# ---------------------------------------------------------------------------
# dry-run：誠實回報預估筆數，不寫入任何資料
# ---------------------------------------------------------------------------


def test_dry_run_reports_honest_count_without_writing(tmp_path, monkeypatch, capsys):
    proj_a_dir = tmp_path / "proj-a-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(proj_a_dir))
    conn = db_mod.connect(project_id="proj-a")
    _insert_memory(conn, mem_id="mem-a-1", project_id="proj-a", tags=["proj-b"])
    _insert_memory(conn, mem_id="mem-a-2", project_id="proj-a", tags=["proj-b"])
    conn.close()

    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path / "caller-state"))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "caller-proj")

    cli_mod.main(_migrate_args("proj-a", "proj-b", dry_run=True))
    out = capsys.readouterr().out
    assert "2 record(s) would be migrated" in out

    # dry-run 不得有任何寫入副作用。
    row = _read_row(proj_a_dir / "remagraph.db", "mem-a-1")
    assert row["status"] == "active"


# ---------------------------------------------------------------------------
# 實際遷移：輸出格式 + 資料庫最終狀態
# ---------------------------------------------------------------------------


def test_real_migration_moves_matching_rows_and_prints_summary(tmp_path, monkeypatch, capsys):
    proj_a_dir = tmp_path / "proj-a-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(proj_a_dir))
    conn = db_mod.connect(project_id="proj-a")
    _insert_memory(conn, mem_id="mem-a-1", project_id="proj-a", tags=["proj-b"])
    _insert_memory(conn, mem_id="mem-a-unrelated", project_id="proj-a", tags=["nothing"])
    conn.close()

    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path / "caller-state"))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "caller-proj")

    cli_mod.main(_migrate_args("proj-a", "proj-b", force=True))
    out = capsys.readouterr().out
    assert "Found 1 record(s) to migrate" in out
    assert "Migration complete: 1 record(s)" in out
    assert "Suggestion" not in out  # --force 略過建議訊息

    src_row = _read_row(proj_a_dir / "remagraph.db", "mem-a-1")
    assert src_row["status"] == "invalidated"
    unrelated = _read_row(proj_a_dir / "remagraph.db", "mem-a-unrelated")
    assert unrelated["status"] == "active"

    to_state = db_mod.get_registered_state_dir("proj-b")
    assert to_state is not None
    tgt_row = _read_row(Path(to_state) / "remagraph.db", "mem-a-1")
    assert tgt_row is not None
    assert tgt_row["project_id"] == "proj-b"
    assert tgt_row["status"] == "active"


def test_real_migration_without_force_prints_maintain_suggestion(tmp_path, monkeypatch, capsys):
    proj_a_dir = tmp_path / "proj-a-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(proj_a_dir))
    conn = db_mod.connect(project_id="proj-a")
    _insert_memory(conn, mem_id="mem-a-1", project_id="proj-a", tags=["proj-b"])
    conn.close()

    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path / "caller-state"))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "caller-proj")

    cli_mod.main(_migrate_args("proj-a", "proj-b"))
    out = capsys.readouterr().out
    assert "Suggestion: run 'remagraph maintain --project proj-b --force'" in out
