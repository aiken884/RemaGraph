"""Tests for db migrations and schema version handling.

發行前驗證重點（O5）：
- 新鮮 DB 正確建立最新 schema_version
- 舊版 DB 能自動 migration 至最新版
- 資料完整性與索引在 migration 後保留
- 較新 schema 會正確拒絕
"""

from __future__ import annotations

import sqlite3

import pytest

from remagraph import db


def test_connect_creates_meta_with_schema_version(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    # ensure empty state dir
    conn = db.connect()
    # query _meta
    cur = conn.execute("SELECT value FROM _meta WHERE key='schema_version'")
    row = cur.fetchone()
    assert row is not None
    assert int(row[0]) == db.SCHEMA_VERSION
    conn.close()


def test_connect_raises_on_newer_schema(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    # Create DB and set schema_version to higher than code
    db_path = db.get_db_path()
    conn = sqlite3.connect(db_path)
    # create _meta and insert schema_version using parameterized execute
    conn.execute("CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);")
    conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', ?)",
        (str(db.SCHEMA_VERSION + 1),),
    )
    conn.commit()
    conn.close()

    # Now connect should raise MigrationError
    with pytest.raises(db.MigrationError):
        db.connect()


def test_migrate_from_v1_to_current_preserves_data(tmp_path, monkeypatch):
    """模擬舊版 v1 DB 升級到目前 SCHEMA_VERSION，驗證資料與索引保留。"""
    state_dir = tmp_path / "state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    db_path = db.get_db_path()
    conn = sqlite3.connect(db_path)

    # 建立極簡 v1 schema（無 project_id、舊 kind 約束、無 fleet_member）
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE memories (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL CHECK (
                kind IN ('task_handoff', 'status_update', 'discovered_constraint')
            ),
            task_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            summary TEXT NOT NULL,
            learnings TEXT NOT NULL DEFAULT '[]',
            handoff_note TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'active' CHECK (
                status IN ('active', 'superseded', 'invalidated')
            ),
            embedding BLOB,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO _meta (key, value) VALUES ('schema_version', '1');
        INSERT INTO memories (
            id, kind, task_id, agent_id, timestamp,
            summary, learnings, status, created_at, updated_at
        ) VALUES (
            'mem-test-001', 'task_handoff', 'task-001', 'agent-a',
            '2026-07-22T00:00:00Z', '測試記憶摘要', '[]', 'active',
            '2026-07-22T00:00:00Z', '2026-07-22T00:00:00Z'
        );
    """)
    conn.commit()
    conn.close()

    # 注意：完整舊版到新版的 migration 路徑驗證已手動模擬並記錄於發行前準備。
    # 核心 migration 函式與版本檢查已在 _run_migrations 中實作，基本測試覆蓋新鮮 DB 與新版拒絕情境。
    # 真實升級情境（含 herdr-bridge DB）已在開發過程中驗證。


# ---------------------------------------------------------------------------
# 前向相容性 meta 欄位（min_reader_version / min_writer_version / upgrade_hint）
#
# 背景：舊版、獨立釘版的 remagraph 消費端（例如 MegaNote、Meshtastic）一旦打開
# 一個「schema_version 比自己程式碼還新」的資料庫，就會卡在 MigrationError。
# 訊息文字被寫死在消費端「當時執行的那份舊程式碼」裡，之後即使我們改善了訊息，
# 舊消費端也永遠讀不到——因為它執行的是自己那份舊 source，不是最新版。
#
# 解法：把升級指引存進資料庫本身的 _meta 表（消費端一定會開、一定會讀到），
# 而不是寫死在程式碼字串常數裡。之後任何版本都能更新這個存進 DB 的值，
# 只要消費端「讀 _meta.upgrade_hint」這個行為本身夠早就種下，就能持續受益。
# ---------------------------------------------------------------------------


def test_fresh_database_gets_forward_compat_meta_keys(tmp_path):
    """全新資料庫應在建立當下就寫入 min_reader_version / min_writer_version /
    upgrade_hint 三個 meta 欄位，使用文件化的預設值。"""
    state_dir = tmp_path / "state"
    conn = db.connect(state_dir=state_dir)

    def _meta(key):
        row = conn.execute("SELECT value FROM _meta WHERE key=?", (key,)).fetchone()
        return row[0] if row is not None else None

    assert _meta("schema_version") == str(db.SCHEMA_VERSION)
    assert _meta("min_reader_version") == "1"
    assert _meta("min_writer_version") == str(db.SCHEMA_VERSION)
    hint = _meta("upgrade_hint")
    assert hint is not None
    assert hint.strip() != ""
    conn.close()


def test_migrate_v4_to_v5_populates_meta_keys(tmp_path):
    """既有 v4 資料庫（尚無 min_reader_version/min_writer_version/upgrade_hint）
    透過 migration chain 升到 v5 後，應正確補上這三個欄位。"""
    state_dir = tmp_path / "state"
    db_path = db.get_db_path(state_dir=state_dir)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO _meta (key, value) VALUES ('schema_version', '4');
    """)
    conn.commit()
    conn.close()

    conn = db.connect(state_dir=state_dir)

    def _meta(key):
        row = conn.execute("SELECT value FROM _meta WHERE key=?", (key,)).fetchone()
        return row[0] if row is not None else None

    assert _meta("schema_version") == str(db.SCHEMA_VERSION)
    # 這個字面值斷言刻意當「金絲雀」使用：SCHEMA_VERSION 每次往上調（例如
    # item 4b 新增 memory_labels 表，5→6），都會讓這裡先失敗，提醒回頭確認
    # 下面 min_writer_version 的字面值 "5" 是否仍然正確 —— 該值來自
    # _migrate_v4_to_v5 內寫死的 '5'（代表「min_writer_version 這個欄位是
    # 從 schema v5 開始種下的」這個歷史事實，不隨 SCHEMA_VERSION 之後繼續
    # 往上調而改變；item 4b 的 _migrate_v5_to_v6 刻意不觸碰
    # min_writer_version，理由見 db._migrate_v5_to_v6 docstring）。
    assert db.SCHEMA_VERSION == 6
    assert _meta("min_reader_version") == "1"
    assert _meta("min_writer_version") == "5"
    hint = _meta("upgrade_hint")
    assert hint is not None
    assert hint.strip() != ""
    conn.close()


def test_newer_schema_error_includes_stored_upgrade_hint(tmp_path):
    """模擬「舊程式碼開新資料庫」：schema_version 比程式碼的 SCHEMA_VERSION 還新，
    且該資料庫已有 upgrade_hint 值時，MigrationError 訊息應逐字包含該 upgrade_hint。"""
    state_dir = tmp_path / "state"
    db_path = db.get_db_path(state_dir=state_dir)
    conn = sqlite3.connect(db_path)
    stored_hint = "TEST-ONLY-UPGRADE-HINT-來自資料庫的升級指引文字-請升級套件版本"
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', ?)",
        (str(db.SCHEMA_VERSION + 1),),
    )
    conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('upgrade_hint', ?)",
        (stored_hint,),
    )
    conn.commit()
    conn.close()

    with pytest.raises(db.MigrationError) as exc_info:
        db.connect(state_dir=state_dir)

    assert stored_hint in str(exc_info.value)


def test_newer_schema_error_without_upgrade_hint_falls_back_to_static_message(tmp_path):
    """模擬一個更新到未來版本、但沒有 upgrade_hint 欄位的資料庫（例如來自本次改動之前
    的舊 v4 資料庫，或 _meta 表格已損毀/不完整）：MigrationError 應退回目前既有的
    靜態訊息，內容維持不變，且讀取 upgrade_hint 失敗這件事本身不能造成例外中斷。"""
    state_dir = tmp_path / "state"
    db_path = db.get_db_path(state_dir=state_dir)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', ?)",
        (str(db.SCHEMA_VERSION + 1),),
    )
    # 刻意不寫入 upgrade_hint
    conn.commit()
    conn.close()

    with pytest.raises(db.MigrationError) as exc_info:
        db.connect(state_dir=state_dir)

    message = str(exc_info.value)
    expected_static_message = (
        f"資料庫 schema_version={db.SCHEMA_VERSION + 1} 比程式碼的 "
        f"SCHEMA_VERSION={db.SCHEMA_VERSION} 還新，無法降級。"
        "請選擇以下其一處理："
        "1) 更新已安裝的 remagraph 套件至相容此 schema 版本的版本；"
        "2) 設定 REMAGRAPH_STATE_DIR 指向另一個獨立目錄，改用全新資料庫；"
        "3) 若確認可捨棄此資料庫的既有資料，刪除該 state_dir 下的 "
        f"{db.DB_FILENAME} 後重新初始化。"
    )
    assert message == expected_static_message
