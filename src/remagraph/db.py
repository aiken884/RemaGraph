# SPDX-License-Identifier: Apache-2.0
"""SQLite 連線管理與 schema 初始化。

本模組負責：
- 展開 state 路徑（預設 ~/.local/state/remagraph/，可透過 REMAGRAPH_STATE_DIR 覆蓋）
- 建立 SQLite 連線（WAL 模式、SERIALIZED 隔離）
- Schema 初始化與 migration 編排
- DB 容量上限保護（max_db_size）
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 常數
# ---------------------------------------------------------------------------

DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "remagraph"
DB_FILENAME = "remagraph.db"
SCHEMA_VERSION = 3
MAX_DB_SIZE_MB = 100  # soft limit via PRAGMA max_page_count
_ALLOWED_STATE_DIR_RE = re.compile(r"^[a-zA-Z0-9_/.-]+$")
DEFAULT_PROJECT_ID = "default"


class MigrationError(RuntimeError):
    """Schema migration 失敗。"""


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------


def get_db_path(state_dir: Path | None = None) -> Path:
    resolved = get_state_dir(state_dir)
    return resolved / DB_FILENAME


def get_state_dir(state_dir: Path | None = None) -> Path:
    env_dir = os.environ.get("REMAGRAPH_STATE_DIR")
    if env_dir:
        if not _ALLOWED_STATE_DIR_RE.match(env_dir):
            raise ValueError(f"REMAGRAPH_STATE_DIR contains invalid characters: {env_dir!r}")
        resolved = Path(env_dir).resolve()
        forbidden_prefixes = ("/etc", "/usr", "/bin", "/sbin", "/dev", "/proc", "/sys")
        if str(resolved).startswith(forbidden_prefixes):
            raise ValueError(f"REMAGRAPH_STATE_DIR invalid: {resolved}")
    elif state_dir is not None:
        resolved = state_dir
    else:
        resolved = DEFAULT_STATE_DIR
    resolved.mkdir(parents=True, exist_ok=True)
    resolved.chmod(0o700)
    return resolved


def is_using_default_state_dir(state_dir: Path | None = None) -> bool:
    resolved = get_state_dir(state_dir)
    return resolved == DEFAULT_STATE_DIR


def load_project_metadata(state_dir: Path | None = None) -> dict[str, Any]:
    state = get_state_dir(state_dir)
    meta_file = state / "project.json"
    if not meta_file.exists():
        return {"project_id": DEFAULT_PROJECT_ID}
    try:
        import json

        data = json.loads(meta_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"project_id": DEFAULT_PROJECT_ID}
    except Exception:
        return {"project_id": DEFAULT_PROJECT_ID}


def validate_project_metadata(
    expected_project: str | None = None, state_dir: Path | None = None
) -> None:
    meta = load_project_metadata(state_dir)
    current = meta.get("project_id", DEFAULT_PROJECT_ID)
    if expected_project and current != expected_project and current != DEFAULT_PROJECT_ID:
        raise ValueError(f"Project metadata mismatch: expected {expected_project}, found {current}")


def connect(state_dir: Path | None = None) -> sqlite3.Connection:
    """建立 SQLite 連線並初始化。

    1. 展開路徑、建立目錄（若需要）
    2. 建立 sqlite3.Connection（WAL 模式、FK ON）
    3. 執行 schema 初始化（_init_schema）
    4. 執行 migration（_run_migrations）
    5. 設定 DB 檔案權限為 0600
    6. 回傳已就緒的連線

    Raises:
        OSError: 目錄無法建立（權限不足）
        MigrationError: Schema migration 失敗
        sqlite3.DatabaseError: 資料庫損毀
    """
    db_path = get_db_path(state_dir=state_dir)

    conn = sqlite3.connect(
        str(db_path),
        isolation_level=None,  # 自動 commit 模式；手動管理 transaction
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row

    # 啟用 WAL 模式與 FK
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    # 容量上限
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    max_pages = (MAX_DB_SIZE_MB * 1024 * 1024) // page_size
    conn.execute(f"PRAGMA max_page_count={max_pages}")

    # 執行 schema 初始化
    _init_schema(conn)
    # 執行 migration chain
    _run_migrations(conn)

    # 設定 DB 檔案權限
    try:
        db_path.chmod(0o600)
    except OSError:
        pass

    return conn


def close(conn: sqlite3.Connection) -> None:
    """安全關閉 SQLite 連線。"""
    try:
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 內部函式
# ---------------------------------------------------------------------------


def _init_schema(conn: sqlite3.Connection) -> None:
    """執行完整 DDL（對齊 D02 §1.5 最終完整 DDL）。

    所有語句使用 IF NOT EXISTS，確保冪等。
    包括：memories 主表、FTS5 虛擬表、triggers、indexes、_meta 表。
    """
    conn.executescript("""
        -- 主表（含 timestamp 欄位）
        CREATE TABLE IF NOT EXISTS memories (
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

        -- FTS5 虛擬表（trigram tokenizer，支援 CJK）
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            summary,
            learnings,
            handoff_note,
            tags,
            tokenize='trigram',
            content='memories',
            content_rowid='rowid'
        );

        -- INSERT 自動同步 FTS5
        CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, summary, learnings, handoff_note, tags)
            VALUES (new.rowid, new.summary, new.learnings, new.handoff_note, new.tags);
        END;

        -- DELETE 自動同步 FTS5
        CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, summary, learnings, handoff_note, tags)
            VALUES ('delete', old.rowid, old.summary, old.learnings, old.handoff_note, old.tags);
        END;

        -- UPDATE 自動同步 FTS5
        CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, summary, learnings, handoff_note, tags)
            VALUES ('delete', old.rowid, old.summary, old.learnings, old.handoff_note, old.tags);
            INSERT INTO memories_fts(rowid, summary, learnings, handoff_note, tags)
            VALUES (new.rowid, new.summary, new.learnings, new.handoff_note, new.tags);
        END;

        -- 效能 indexes
        CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind);
        CREATE INDEX IF NOT EXISTS idx_memories_project_id ON memories(project_id);
        CREATE INDEX IF NOT EXISTS idx_memories_task_id ON memories(task_id);
        CREATE INDEX IF NOT EXISTS idx_memories_agent_id ON memories(agent_id);
        CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);
        CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_memories_dedup
            ON memories(kind, status) WHERE status = 'active';

        -- 版本追蹤
        CREATE TABLE IF NOT EXISTS _meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)


def _run_migrations(conn: sqlite3.Connection) -> None:
    """檢查 _meta.schema_version 並執行 migration chain。

    目前只有 v1（初始版本），未來版本在此新增 migration 函式。
    """
    # 確保 _meta 表存在（_init_schema 已建立，但以防萬一）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    row = conn.execute("SELECT value FROM _meta WHERE key='schema_version'").fetchone()

    if row is None:
        # 全新資料庫 —— 寫入初始版本
        conn.execute(
            "INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        return

    current_version = int(row[0])

    if current_version == SCHEMA_VERSION:
        # 已是最新版本
        return

    # 未來版本 migration chain：
    # if current_version == 1:
    #     _migrate_v1_to_v2(conn)
    #     current_version = 2
    if current_version == 1:
        _migrate_v1_to_v2(conn)
        current_version = 2
    if current_version == 2:
        _migrate_v2_to_v3(conn)
        current_version = 3

    if current_version > SCHEMA_VERSION:
        raise MigrationError(
            f"資料庫 schema_version={current_version} 比程式碼的 "
            f"SCHEMA_VERSION={SCHEMA_VERSION} 還新，無法降級"
        )


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', '2')")


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE memories ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default'")
    conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', '3')")
