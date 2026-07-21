"""測試 db.py — SQLite 連線管理、schema 初始化與 migration，涵蓋 D05 B1-B6。"""

import sqlite3
import stat

import pytest

from remagraph import db as db_mod


# ---------------------------------------------------------------------------
# B1: get_db_path / connect 基本功能
# ---------------------------------------------------------------------------
def test_get_db_path_default():
    """B1: get_db_path() 回傳預設路徑。"""
    path = db_mod.get_db_path()
    assert path.name == "remagraph.db"
    assert ".local" in str(path)
    assert "state" in str(path)
    assert "remagraph" in str(path)


def test_get_db_path_custom(tmp_path):
    """B1: get_db_path(state_dir) 使用自訂目錄。"""
    custom = tmp_path / "test-state"
    path = db_mod.get_db_path(state_dir=custom)
    assert str(custom) in str(path)
    assert path.name == "remagraph.db"
    # 目錄應被自動建立
    assert custom.exists()


def test_connect_creates_directory(tmp_path):
    """B1: connect() 在目錄不存在時自動建立。"""
    state_dir = tmp_path / "new-remagraph"
    assert not state_dir.exists()
    conn = db_mod.connect(state_dir=state_dir)
    assert state_dir.exists()
    assert state_dir.is_dir()
    conn.close()


def test_connect_returns_connection(tmp_path):
    """B1: connect() 回傳 sqlite3.Connection。"""
    conn = db_mod.connect(state_dir=tmp_path / "state")
    assert isinstance(conn, sqlite3.Connection)
    conn.close()


def test_connect_wal_mode(tmp_path):
    """B5: WAL mode 應在連線時啟用。"""
    conn = db_mod.connect(state_dir=tmp_path / "state")
    row = conn.execute("PRAGMA journal_mode").fetchone()
    assert row is not None
    # WAL mode
    assert row[0].upper() == "WAL"
    conn.close()


def test_connect_foreign_keys_on(tmp_path):
    """B6: foreign_keys 應啟用。"""
    conn = db_mod.connect(state_dir=tmp_path / "state")
    row = conn.execute("PRAGMA foreign_keys").fetchone()
    assert row is not None
    assert row[0] == 1
    conn.close()


# ---------------------------------------------------------------------------
# B2: init_db 建立全部 schema 物件
# ---------------------------------------------------------------------------
def test_init_schema_creates_memories_table(tmp_path):
    """B2: _init_schema 建立 memories 主表。"""
    conn = db_mod.connect(state_dir=tmp_path / "state")
    # 確認表存在
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memories'"
    ).fetchall()
    assert len(tables) == 1
    conn.close()


def test_init_schema_creates_fts_table(tmp_path):
    """B2: _init_schema 建立 memories_fts 虛擬表。"""
    conn = db_mod.connect(state_dir=tmp_path / "state")
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memories_fts'"
    ).fetchall()
    assert len(tables) == 1
    conn.close()


def test_init_schema_creates_triggers(tmp_path):
    """B2: _init_schema 建立三個 FTS5 同步 trigger（ai, ad, au）。"""
    conn = db_mod.connect(state_dir=tmp_path / "state")
    triggers = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger'"
    ).fetchall()
    trigger_names = {row[0] for row in triggers}
    assert "memories_ai" in trigger_names
    assert "memories_ad" in trigger_names
    assert "memories_au" in trigger_names
    conn.close()


def test_init_schema_creates_indexes(tmp_path):
    """B2: _init_schema 建立所有效能 index。"""
    conn = db_mod.connect(state_dir=tmp_path / "state")
    indexes = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
    index_names = {row[0] for row in indexes}
    expected = [
        "idx_memories_kind",
        "idx_memories_task_id",
        "idx_memories_agent_id",
        "idx_memories_status",
        "idx_memories_created_at",
        "idx_memories_dedup",
    ]
    for name in expected:
        assert name in index_names, f"Missing index: {name}"
    conn.close()


def test_init_schema_creates_meta_table(tmp_path):
    """B2: _init_schema 建立 _meta 表且含 schema_version。"""
    conn = db_mod.connect(state_dir=tmp_path / "state")
    row = conn.execute("SELECT value FROM _meta WHERE key='schema_version'").fetchone()
    assert row is not None
    assert row[0] == "2"
    conn.close()


def test_memories_table_has_all_columns(tmp_path):
    """B2: memories 表包含全部 14 欄位（含 timestamp）。"""
    conn = db_mod.connect(state_dir=tmp_path / "state")
    cols = conn.execute("PRAGMA table_info(memories)").fetchall()
    col_names = {row[1] for row in cols}
    expected_cols = {
        "id", "kind", "task_id", "agent_id", "timestamp",
        "summary", "learnings", "handoff_note", "tags",
        "status", "embedding", "created_at", "updated_at",
    }
    for col in expected_cols:
        assert col in col_names, f"Missing column: {col}"
    conn.close()


def test_memories_fts_trigram_tokenizer(tmp_path):
    """B2: FTS5 使用 trigram tokenizer。"""
    conn = db_mod.connect(state_dir=tmp_path / "state")
    # 用 FTS5 的內建功能確認 tokenizer
    conn.execute(
        "INSERT INTO memories (id, kind, task_id, agent_id, timestamp, summary, "
        "learnings, handoff_note, tags, status, embedding, created_at, updated_at) "
        "VALUES ('mem-test-001', 'task_handoff', 'task-1', 'test', '2026-07-21T00:00:00Z', "
        "'測試中文 trigram 是否正確分詞', '[]', '', '[]', 'active', NULL, "
        "'2026-07-21T00:00:00Z', '2026-07-21T00:00:00Z')"
    )
    # trigram: 'tri' 應可匹配到 'trigram' 的 trigram
    rows = conn.execute(
        "SELECT * FROM memories_fts WHERE memories_fts MATCH 'tri'"
    ).fetchall()
    assert len(rows) >= 1, "trigram tokenizer should match substrings"
    conn.close()


# ---------------------------------------------------------------------------
# B3: 重複 init 不報錯
# ---------------------------------------------------------------------------
def test_reconnect_preserves_data(tmp_path):
    """B3: 重複 connect 不破壞既有資料。"""
    state_dir = tmp_path / "state"
    conn1 = db_mod.connect(state_dir=state_dir)

    # 插入一筆測試資料
    conn1.execute(
        "INSERT INTO memories (id, kind, task_id, agent_id, timestamp, summary, "
        "learnings, handoff_note, tags, status, embedding, created_at, updated_at) "
        "VALUES ('mem-test-002', 'task_handoff', 'task-2', 'test', "
        "'2026-07-21T00:00:00Z', "
        "'this is a test summary that must be at least thirty characters long "
        "for the test to pass', "
        "'[\"test learning\"]', 'test handoff note here', '[\"test\"]', "
        "'active', NULL, '2026-07-21T00:00:00Z', '2026-07-21T00:00:00Z')"
    )
    conn1.commit()
    conn1.close()

    # 重新連線
    conn2 = db_mod.connect(state_dir=state_dir)
    rows = conn2.execute("SELECT id FROM memories WHERE id='mem-test-002'").fetchall()
    assert len(rows) == 1
    conn2.close()


def test_double_init_is_idempotent(tmp_path):
    """B3: _init_schema 重複呼叫不拋錯（IF NOT EXISTS）。"""
    state_dir = tmp_path / "state"
    conn = db_mod.connect(state_dir=state_dir)
    # 手動再呼叫一次 _init_schema（應該不拋錯）
    db_mod._init_schema(conn)
    conn.close()


# ---------------------------------------------------------------------------
# B4: migration 介面存在
# ---------------------------------------------------------------------------
def test_run_migrations_noop_for_v1(tmp_path):
    """B4: v1 schema_version=1 時 migration 為 no-op。"""
    state_dir = tmp_path / "state"
    conn = db_mod.connect(state_dir=state_dir)
    # _run_migrations 應正常完成，不拋錯
    db_mod._run_migrations(conn)
    row = conn.execute("SELECT value FROM _meta WHERE key='schema_version'").fetchone()
    assert row[0] == "2"
    conn.close()


# ---------------------------------------------------------------------------
# 權限測試（僅在 POSIX 環境）
# ---------------------------------------------------------------------------
def test_state_directory_permissions(tmp_path):
    """State 目錄應設為 0700。"""
    state_dir = tmp_path / "state"
    conn = db_mod.connect(state_dir=state_dir)
    conn.close()

    mode = stat.S_IMODE(state_dir.stat().st_mode)
    assert mode == 0o700, f"Expected 0o700, got {oct(mode)}"


def test_db_file_permissions(tmp_path):
    """DB 檔案應設為 0600。"""
    state_dir = tmp_path / "state"
    conn = db_mod.connect(state_dir=state_dir)
    conn.close()

    db_path = state_dir / "remagraph.db"
    mode = stat.S_IMODE(db_path.stat().st_mode)
    assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"


# ---------------------------------------------------------------------------
# REMAGRAPH_STATE_DIR 環境變數
# ---------------------------------------------------------------------------
def test_env_var_state_dir(tmp_path, monkeypatch):
    """REMAGRAPH_STATE_DIR 環境變數應覆蓋預設路徑。"""
    custom = tmp_path / "env-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(custom))
    path = db_mod.get_db_path()
    assert str(custom) in str(path)


# ---------------------------------------------------------------------------
# SQLite 版本檢查
# ---------------------------------------------------------------------------
def test_sqlite_version_sufficient():
    """Runtime SQLite 版本應 ≥ 3.38。"""
    assert sqlite3.sqlite_version_info >= (3, 38, 0), (
        f"SQLite {sqlite3.sqlite_version} < 3.38, trigram 可能不可用"
    )


# ---------------------------------------------------------------------------
# close 安全關閉
# ---------------------------------------------------------------------------
def test_close_connection(tmp_path):
    """close() 應安全關閉連線。"""
    conn = db_mod.connect(state_dir=tmp_path / "state")
    db_mod.close(conn)
    # 關閉後操作應拋錯
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_fk_and_wal_on_memory(tmp_path):
    """:memory: 連線也應啟用 FK 和 WAL。"""
    state_dir = tmp_path / "state"
    conn = db_mod.connect(state_dir=state_dir)
    fk = conn.execute("PRAGMA foreign_keys").fetchone()
    assert fk[0] == 1
    wal = conn.execute("PRAGMA journal_mode").fetchone()
    assert wal[0].upper() == "WAL"
    conn.close()
