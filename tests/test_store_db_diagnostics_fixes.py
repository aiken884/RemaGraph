# SPDX-License-Identifier: Apache-2.0
"""全專案診斷（批次 3）的 store/db 回歸測試。

涵蓋五個診斷發現：
1. migrate_project_memories 的 INSERT OR IGNORE 在目標 DB 已有同 id 記錄時
   靜默不插入，卻仍把來源標 invalidated、migrated += 1——memory id
   （mem-YYYYMMDD-NNN）是每個 DB 各自獨立的序列，同一天各自存過記憶就會
   碰撞，構成靜默資料遺失。
2. migrate 迴圈中目標 INSERT 成功後、來源 UPDATE 失敗（SQLITE_BUSY 等）
   → 記 skipped 但兩邊照樣 COMMIT，同一筆記憶同時以 active 存在兩邊。
3. 全新 DB 把 min_writer_version 種為 SCHEMA_VERSION（6），但 v5→v6
   migration 刻意保留 5（docstring 論證 v5 舊程式寫 v6 DB 完全安全）——
   同一 schema 兩種相容性判定，v6 程式新建的 DB 會讓 v5 釘版消費端被
   錯誤降級唯讀。
4. _migrate_v3_to_v4 重建 memories 表後未 rebuild FTS：v3 時代刪過列的
   DB（rowid 有洞）升級後 rowid 全面位移，FTS 索引指向錯誤的列。
5. process_store 的 except 裡 conn.execute("ROLLBACK") 沒有保護：SQLite
   在 disk-full 等錯誤下自動回滾交易，此時 ROLLBACK 再拋
   "cannot rollback"，遮蔽原始錯誤並打破「回傳 StoreResponse」的契約。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from remagraph import db as db_mod
from remagraph import store as store_mod
from remagraph.models import StoreRequest

SUMMARY = "一筆長度足夠通過仲裁下限的測試 summary，內容填充填充填充填充"


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)
    monkeypatch.delenv("REMAGRAPH_PROJECT", raising=False)
    home = tmp_path / "fake-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        db_mod, "DEFAULT_STATE_DIR", home / ".local" / "state" / "remagraph"
    )
    return home


def _insert_memory(
    conn: sqlite3.Connection,
    *,
    mem_id: str,
    project_id: str,
    task_id: str = "task-fixture",
    summary: str = SUMMARY,
    status: str = "active",
) -> None:
    now = "2026-07-24T00:00:00Z"
    conn.execute(
        "INSERT INTO memories (id, project_id, kind, task_id, agent_id, timestamp, "
        "summary, learnings, handoff_note, tags, status, created_at, updated_at) "
        "VALUES (?, ?, 'task_handoff', ?, 'agent-fixture', ?, ?, '[]', '', '[]', ?, ?, ?)",
        (mem_id, project_id, task_id, now, summary, status, now, now),
    )


def _setup_migration_pair(tmp_path, monkeypatch):
    """建 source（proj-src，registered）與 target（proj-dst）兩個獨立 DB。

    source 有一筆啟發式命中 proj-dst 的記錄 mem-20260101-001；
    target 已有「自己的」同 id 記錄（不同內容）——重現 per-DB 獨立日序列
    的必然碰撞。回傳 (src_dir, dst_db_path, source_summary)。
    """
    src_dir = tmp_path / "src-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(src_dir))
    conn = db_mod.connect(project_id="proj-src")
    source_summary = "這筆來自 source、必須抵達 target 的記憶內容" + "填" * 10
    _insert_memory(
        conn,
        mem_id="mem-20260101-001",
        project_id="proj-src",
        task_id="proj-dst-task",
        summary=source_summary,
    )
    conn.commit()
    conn.close()
    monkeypatch.delenv("REMAGRAPH_STATE_DIR")

    dst_dir = Path(db_mod.DEFAULT_STATE_DIR).parent / "remagraph-proj-dst"
    dst_dir.mkdir(parents=True)
    conn_dst = db_mod.connect_at_state_dir(dst_dir)
    _insert_memory(
        conn_dst,
        mem_id="mem-20260101-001",
        project_id="proj-dst",
        summary="target 自己既有的、內容完全不同的記憶" + "填" * 10,
    )
    conn_dst.commit()
    conn_dst.close()
    return src_dir, dst_dir / db_mod.DB_FILENAME, source_summary


def _rows(db_path: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. migrate id 碰撞
# ---------------------------------------------------------------------------


def test_migrate_id_collision_does_not_silently_drop_data(tmp_path, monkeypatch):
    src_dir, dst_db, source_summary = _setup_migration_pair(tmp_path, monkeypatch)

    result = store_mod.migrate_project_memories("proj-src", "proj-dst")

    # 來源內容必須真的抵達 target（允許 re-id）
    arrived = _rows(
        dst_db, "SELECT * FROM memories WHERE summary = ?", (source_summary,)
    )
    assert len(arrived) == 1, "id 碰撞時來源記憶被靜默丟棄"
    assert arrived[0]["project_id"] == "proj-dst"
    # target 原有記錄不得被覆蓋
    original = _rows(
        dst_db, "SELECT * FROM memories WHERE id = 'mem-20260101-001'"
    )
    assert len(original) == 1
    assert "target 自己既有的" in original[0]["summary"]
    # 來源標 invalidated、回報一致
    src_row = _rows(
        src_dir / db_mod.DB_FILENAME,
        "SELECT * FROM memories WHERE id = 'mem-20260101-001'",
    )[0]
    assert src_row["status"] == "invalidated"
    assert result.migrated_count == 1
    assert result.skipped_ids == []


# ---------------------------------------------------------------------------
# 2. migrate 來源 UPDATE 失敗不得留下兩邊 active 的矛盾
# ---------------------------------------------------------------------------


class _FailingSourceUpdateConn:
    """代理 source 連線：標 invalidated 的 UPDATE 一律拋 locked。"""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real

    def execute(self, sql: str, *args: Any):
        if sql.strip().startswith("UPDATE memories SET status='invalidated'"):
            raise sqlite3.OperationalError("database is locked")
        return self._real.execute(sql, *args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def test_migrate_source_update_failure_rolls_back_target_insert(tmp_path, monkeypatch):
    # 刻意用「不會 id 碰撞」的情境：此測試針對的是 UPDATE 失敗的回滾，
    # 不能讓 INSERT OR IGNORE 的碰撞行為（另一個測試的主題）掩蓋斷言。
    src_dir, dst_db, source_summary = _setup_migration_pair(tmp_path, monkeypatch)
    conn_fix = sqlite3.connect(str(src_dir / db_mod.DB_FILENAME))
    conn_fix.execute(
        "UPDATE memories SET id='mem-20260202-001' WHERE id='mem-20260101-001'"
    )
    conn_fix.commit()
    conn_fix.close()

    real_connect_at_state_dir = db_mod.connect_at_state_dir

    def patched(state_dir: Path) -> Any:
        conn = real_connect_at_state_dir(state_dir)
        if Path(state_dir).resolve() == src_dir.resolve():
            return _FailingSourceUpdateConn(conn)
        return conn

    monkeypatch.setattr(db_mod, "connect_at_state_dir", patched)
    monkeypatch.setattr(store_mod._db, "connect_at_state_dir", patched)

    result = store_mod.migrate_project_memories("proj-src", "proj-dst")

    # 該筆必須回報 skipped，且 target 不得留下已插入的副本
    assert result.skipped_ids == ["mem-20260202-001"]
    assert result.migrated_count == 0
    leaked = _rows(
        dst_db, "SELECT * FROM memories WHERE summary = ?", (source_summary,)
    )
    assert leaked == [], "來源 UPDATE 失敗後，target 留下了 active 副本（兩邊矛盾）"
    # 來源必須維持 active（沒有被標 invalidated）
    src_row = _rows(
        src_dir / db_mod.DB_FILENAME,
        "SELECT * FROM memories WHERE id = 'mem-20260202-001'",
    )[0]
    assert src_row["status"] == "active"


# ---------------------------------------------------------------------------
# 3. 全新 DB 的 min_writer_version 必須與 migration 路徑一致（= 5）
# ---------------------------------------------------------------------------


def test_fresh_db_min_writer_version_matches_migration_path(tmp_path, monkeypatch):
    state_dir = tmp_path / "fresh-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    conn = db_mod.connect(project_id="fresh-proj")
    row = conn.execute(
        "SELECT value FROM _meta WHERE key='min_writer_version'"
    ).fetchone()
    conn.close()
    assert row[0] == "5", (
        "全新 DB 的 min_writer_version 與 v5→v6 migration 保留的值不一致，"
        "v5 釘版消費端會被錯誤降級唯讀"
    )


# ---------------------------------------------------------------------------
# 4. v3→v4 migration 必須 rebuild FTS（rowid 位移）
# ---------------------------------------------------------------------------

_V3_SCHEMA = """
CREATE TABLE memories (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL DEFAULT 'default',
    kind        TEXT NOT NULL CHECK (
        kind IN ('task_handoff', 'status_update', 'discovered_constraint')
    ),
    task_id     TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    summary     TEXT NOT NULL,
    learnings   TEXT NOT NULL DEFAULT '[]',
    handoff_note TEXT NOT NULL DEFAULT '',
    tags        TEXT NOT NULL DEFAULT '[]',
    status      TEXT NOT NULL DEFAULT 'active' CHECK (
        status IN ('active', 'superseded', 'invalidated')
    ),
    embedding   BLOB,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE VIRTUAL TABLE memories_fts USING fts5(
    summary, learnings, handoff_note, tags,
    tokenize='trigram', content='memories', content_rowid='rowid'
);
CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, summary, learnings, handoff_note, tags)
    VALUES (new.rowid, new.summary, new.learnings, new.handoff_note, new.tags);
END;
CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, summary, learnings, handoff_note, tags)
    VALUES ('delete', old.rowid, old.summary, old.learnings, old.handoff_note, old.tags);
END;
CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO _meta (key, value) VALUES ('schema_version', '3');
"""


def test_v3_to_v4_migration_rebuilds_fts_after_rowid_shift(tmp_path, monkeypatch):
    state_dir = tmp_path / "v3-state"
    state_dir.mkdir()
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    db_path = state_dir / db_mod.DB_FILENAME

    raw = sqlite3.connect(str(db_path))
    raw.executescript(_V3_SCHEMA)
    now = "2026-07-24T00:00:00Z"
    for i, summary in enumerate(
        ["first legacy memory about alpha", "second about bravo", "third about charlie"],
        start=1,
    ):
        raw.execute(
            "INSERT INTO memories (id, project_id, kind, task_id, agent_id, timestamp, "
            "summary, learnings, handoff_note, tags, status, created_at, updated_at) "
            "VALUES (?, 'default', 'task_handoff', 'task-1', 'agent-1', ?, ?, "
            "'[]', '', '[]', 'active', ?, ?)",
            (f"mem-2026010{i}-001", now, summary, now, now),
        )
    # 刪第一筆，讓 rowid 出現空洞（v3 時代的 prune/cleanup 都會這麼做）
    raw.execute("DELETE FROM memories WHERE id = 'mem-20260101-001'")
    raw.commit()
    raw.close()

    conn = db_mod.connect()  # 觸發 v3 → 現行 schema 的完整 migration

    rows = conn.execute(
        "SELECT m.id FROM memories_fts JOIN memories m ON m.rowid = memories_fts.rowid "
        "WHERE memories_fts MATCH '\"charlie\"'"
    ).fetchall()
    conn.close()
    assert [r["id"] for r in rows] == ["mem-20260103-001"], (
        "migration 後 FTS 索引指向錯誤的列（rowid 位移未 rebuild）"
    )


# ---------------------------------------------------------------------------
# 5. process_store 的 ROLLBACK 二次例外不得遮蔽原始錯誤
# ---------------------------------------------------------------------------


class _DiskFullConn:
    """代理連線：INSERT INTO memories 拋 disk-full，且（模擬 SQLite 自動
    回滾後的狀態）後續 ROLLBACK 拋 cannot rollback。"""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real

    def execute(self, sql: str, *args: Any):
        stripped = sql.strip().upper()
        if stripped.startswith("INSERT INTO MEMORIES"):
            raise sqlite3.OperationalError("database or disk is full")
        if stripped == "ROLLBACK":
            raise sqlite3.OperationalError(
                "cannot rollback - no transaction is active"
            )
        return self._real.execute(sql, *args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def test_process_store_disk_full_returns_error_response_not_rollback_crash(
    tmp_path, monkeypatch
):
    state_dir = tmp_path / "store-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    real = db_mod.connect(project_id="store-proj")
    conn = _DiskFullConn(real)

    request = StoreRequest(
        project_id="store-proj",
        task_id="store-proj-task-1",
        agent_id="agent-1",
        kind="task_handoff",
        summary=SUMMARY,
        learnings=["一條有效的 learning 記錄"],
        handoff_note="一段長度足夠通過驗證的 handoff note 內容",
        tags=[],
    )
    response = store_mod.process_store(request, conn)  # 修復前這裡直接拋例外

    assert response.status == "error"
    assert "disk is full" in (response.detail or ""), (
        f"原始 disk-full 錯誤被遮蔽: {response.detail!r}"
    )
    real.close()
