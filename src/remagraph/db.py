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
SCHEMA_VERSION = 4
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


def connect(
    state_dir: Path | None = None,
    project_id: str | None = None,
    *,
    skip_maintenance: bool = False,
    skip_safety_check: bool = False,
) -> sqlite3.Connection:
    """建立 SQLite 連線並初始化。

    1. 展開路徑、建立目錄（若需要）
    2. 建立 sqlite3.Connection（WAL 模式、FK ON）
    3. 執行 schema 初始化（_init_schema）
    4. 執行 migration（_run_migrations）
    5. 設定 DB 檔案權限為 0600
    6. 回傳已就緒的連線

    嚴格安全閥門：若提供 project_id，會驗證 state_dir 與 project 對映。

    Args:
        skip_maintenance: 僅供維護子系統自身開啟連線時使用 —— 略過啟動時
            自動輕量維護（light_maintenance_on_connect）的呼叫，避免
            maintenance.py 內部開連線時重新觸發維護，造成無窮遞迴。一般
            外部呼叫者（CLI、MCP server）不得傳入，維持預設 False 以保留
            既有行為。
        skip_safety_check: 僅供 maintenance._record_violation 自身開啟連線
            時使用 —— 略過本函式開頭（明確 project_id 分支與
            REMAGRAPH_PROJECT 環境變數相容分支）的 safety_validate_project
            呼叫，避免「記錄違規已發生」這個內部自我記錄路徑重新觸發同一個
            目前正在失敗的安全驗證，造成無窮遞迴。一般外部呼叫者（CLI、MCP
            server）不得傳入，維持預設 False 以保留既有的安全閥門強制行為。

    Raises:
        OSError: 目錄無法建立（權限不足）
        MigrationError: Schema migration 失敗
        sqlite3.DatabaseError: 資料庫損毀
        SafetyValveError: 不合規的 project/state_dir 使用
    """
    from remagraph.maintenance import light_maintenance_on_connect, safety_validate_project

    if project_id:
        # 強制走權威解析 + 安全閥（除非呼叫者明確要求略過，見上方 skip_safety_check 說明）
        if not skip_safety_check:
            resolved = safety_validate_project(project_id)
            state_dir = resolved
    else:
        # 相容舊呼叫，但記錄警告（未來可移除）
        if os.environ.get("REMAGRAPH_PROJECT", "default") != "default":
            # 若 env 有 project 但未傳，嘗試驗證
            project_id = os.environ.get("REMAGRAPH_PROJECT")
            if not skip_safety_check:
                resolved = safety_validate_project(project_id)
                state_dir = resolved

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

    # 啟動時自動輕量維護（含 integrity + WAL + migration）
    # skip_maintenance 供維護子系統自身開連線時使用，避免重新觸發本身造成遞迴
    if project_id and not skip_maintenance:
        light_maintenance_on_connect(project_id)

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
                kind IN ('task_handoff', 'status_update', 'discovered_constraint', 'fleet_member')
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
    if current_version == 3:
        _migrate_v3_to_v4(conn)
        current_version = 4

    if current_version > SCHEMA_VERSION:
        raise MigrationError(
            f"資料庫 schema_version={current_version} 比程式碼的 "
            f"SCHEMA_VERSION={SCHEMA_VERSION} 還新，無法降級。"
            "請選擇以下其一處理："
            "1) 更新已安裝的 remagraph 套件至相容此 schema 版本的版本；"
            "2) 設定 REMAGRAPH_STATE_DIR 指向另一個獨立目錄，改用全新資料庫；"
            "3) 若確認可捨棄此資料庫的既有資料，刪除該 state_dir 下的 "
            f"{DB_FILENAME} 後重新初始化。"
        )


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', '2')")


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE memories ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default'")
    conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', '3')")


def _migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    """v3→v4: 加入 fleet_member kind（需重建 CHECK 約束）。
    使用標準 SQLite 重建表方式更新 CHECK。
    """
    # 重建表以更新 CHECK constraint 加入 'fleet_member'
    conn.executescript("""
        PRAGMA foreign_keys=OFF;
        BEGIN TRANSACTION;
        CREATE TABLE memories_new (
            id          TEXT PRIMARY KEY,
            project_id  TEXT NOT NULL DEFAULT 'default',
            kind        TEXT NOT NULL CHECK (
                kind IN ('task_handoff', 'status_update', 'discovered_constraint', 'fleet_member')
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
        INSERT INTO memories_new SELECT * FROM memories;
        DROP TABLE memories;
        ALTER TABLE memories_new RENAME TO memories;

        -- 重建 FTS 相關 triggers/indexes（_init_schema 已保證存在，但 migration 需重建以對應）
        DROP TRIGGER IF EXISTS memories_ai;
        DROP TRIGGER IF EXISTS memories_ad;
        DROP TRIGGER IF EXISTS memories_au;
        CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, summary, learnings, handoff_note, tags)
            VALUES (new.rowid, new.summary, new.learnings, new.handoff_note, new.tags);
        END;
        CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, summary, learnings, handoff_note, tags)
            VALUES ('delete', old.rowid, old.summary, old.learnings, old.handoff_note, old.tags);
        END;
        CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, summary, learnings, handoff_note, tags)
            VALUES ('delete', old.rowid, old.summary, old.learnings, old.handoff_note, old.tags);
            INSERT INTO memories_fts(rowid, summary, learnings, handoff_note, tags)
            VALUES (new.rowid, new.summary, new.learnings, new.handoff_note, new.tags);
        END;

        -- 確保 indexes
        CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind);
        CREATE INDEX IF NOT EXISTS idx_memories_project_id ON memories(project_id);
        CREATE INDEX IF NOT EXISTS idx_memories_task_id ON memories(task_id);
        CREATE INDEX IF NOT EXISTS idx_memories_agent_id ON memories(agent_id);
        CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);
        CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_memories_dedup
            ON memories(kind, status) WHERE status = 'active';

        COMMIT;
        PRAGMA foreign_keys=ON;
    """)
    conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', '4')")
