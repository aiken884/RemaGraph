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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 常數
# ---------------------------------------------------------------------------

DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "remagraph"
DB_FILENAME = "remagraph.db"
SCHEMA_VERSION = 5
MAX_DB_SIZE_MB = 100  # soft limit via PRAGMA max_page_count
_ALLOWED_STATE_DIR_RE = re.compile(r"^[a-zA-Z0-9_/.-]+$")
DEFAULT_PROJECT_ID = "default"

# ---------------------------------------------------------------------------
# 前向相容性 meta 欄位預設值（自 v5 起）
#
# 背景：獨立釘版的舊消費端一旦打開一個 schema_version 比自己程式碼還新的
# 資料庫，就會卡在 MigrationError，而該錯誤訊息是寫死在「消費端當時執行的
# 那份舊程式碼」裡 —— 之後即使我們改善訊息文字，舊消費端也永遠讀不到。
#
# 解法：把升級指引存進資料庫本身的 _meta 表（消費端一定會開、一定會讀到），
# 而不是寫死在程式碼字串常數裡。之後任何版本的 migration 都能更新這個存進
# DB 的值，並讓「讀 _meta.upgrade_hint」這個行為從現在開始就種進消費端。
# ---------------------------------------------------------------------------
_MIN_READER_VERSION_DEFAULT = "1"

_UPGRADE_HINT_TEXT = (
    "此資料庫的 schema 版本比目前執行的 remagraph 程式碼更新，程式碼為避免資料損毀"
    "已拒絕開啟。請選擇以下其一處理："
    "1) 將已安裝的 remagraph 套件升級到與此資料庫 schema 相容的版本；"
    "2) 設定環境變數 REMAGRAPH_STATE_DIR 指向另一個全新、獨立的目錄，改用全新資料庫；"
    "3) 若確認可捨棄此資料庫的既有資料，找到並刪除該 state_dir 目錄下的 "
    "remagraph.db 檔案後重新初始化。"
)


class MigrationError(RuntimeError):
    """Schema migration 失敗。"""


class _MarkedConnection(sqlite3.Connection):
    """sqlite3.Connection 子類別，僅用於取得一般 instance __dict__。

    背景：經拋棄式腳本實測驗證，純 C 擴充型別的 sqlite3.Connection 實例
    本身**不**支援任意屬性賦值（`conn.foo = 1` 會直接拋出
    `AttributeError: 'sqlite3.Connection' object has no attribute 'foo'`，
    因為該型別沒有 instance __dict__）。繼承出的子類別則會取得一般的
    instance __dict__，因此支援任意屬性賦值 —— 且該子類別實例仍是
    sqlite3.Connection 的實例（isinstance 檢查、既有型別標註
    `sqlite3.Connection`、既有呼叫端的所有行為都不受影響，本類別本身
    不覆寫任何行為/方法）。

    connect() 一律以此類別作為 sqlite3.connect(factory=...) 的 factory，
    使唯讀降級標記（見 _run_migrations 的三層版本相容性判斷）得以掛載在
    連線物件上，供 store.process_store() 讀取判斷。
    """


# _run_migrations 判定唯讀降級時，掛在連線物件上的標記屬性名稱。
# store.process_store() 會以 getattr(conn, READ_ONLY_ATTR, False) 讀取。
READ_ONLY_ATTR = "remagraph_read_only"
READ_ONLY_DETAIL_ATTR = "remagraph_read_only_detail"


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
        factory=_MarkedConnection,
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


def get_compat_status(conn: sqlite3.Connection) -> dict[str, Any]:
    """回傳目前連線的版本相容性 handshake 資訊。

    供 remagraph_status（MCP tool 與 CLI `status` 子命令共用）在回應中附加
    版本落差資訊，讓呼叫端（agent）能在 session 一開始或定期呼叫時就提早
    掌握相容性現況，而不必等到 remagraph_store 寫入失敗才第一次得知
    （PPLX 架構改善計畫 item 3；三層版本判斷本身見 item 2 的
    _handle_newer_than_code_schema）。

    回傳欄位：
        server_code_version: 目前執行中程式碼的 SCHEMA_VERSION 常數。
        db_schema_version: 資料庫 _meta 表實際存下的 schema_version
            （防禦性讀取 —— connect() 成功後理論上必定存在，但仍走
            _read_meta_int_defensively，不假設它一定等於
            server_code_version，也不因結構意外而拋例外）。
        min_reader_version / min_writer_version: 資料庫存下的前向相容性
            欄位（見 item 1 / _migrate_v4_to_v5 起種下）。若資料庫是
            item 1 之前建立、且尚未走過該 migration（例如未升級的 v4
            資料庫），這兩個欄位可能不存在 —— 一律回傳 None，不拋例外。
        upgrade_hint: 資料庫存下的升級指引文字，重用既有的
            _read_upgrade_hint_defensively（item 1 拒絕降級訊息已使用的
            同一套防禦性讀取邏輯），缺漏時回傳 None。
        read_only: 目前連線是否處於 item 2 引入的唯讀降級模式
            （見 READ_ONLY_ATTR）。
    """
    return {
        "server_code_version": SCHEMA_VERSION,
        "db_schema_version": _read_meta_int_defensively(conn, "schema_version"),
        "min_reader_version": _read_meta_int_defensively(conn, "min_reader_version"),
        "min_writer_version": _read_meta_int_defensively(conn, "min_writer_version"),
        "upgrade_hint": _read_upgrade_hint_defensively(conn),
        "read_only": bool(getattr(conn, READ_ONLY_ATTR, False)),
    }


# ---------------------------------------------------------------------------
# Cross-project registry（PPLX 架構改善計畫 item 4a）
#
# 背景：目前每個 project_id 各自對應完全獨立的 state_dir / DB 檔案（見
# maintenance.resolve_project_state_dir），彼此互不知道對方存在 —— 每個專案
# 的資料庫是一座孤島。後續項目（4b 跨專案標籤搜尋、5 recall_related）都需要
# 一個輕量、共用的「登記簿」，記錄哪些 project_id 存在、各自的 state_dir 在
# 哪裡，才能在需要時開啟「別的」專案的資料庫。本節只落地這個登記簿本身。
#
# 設計決策：這張 registry 表落在 DEFAULT_STATE_DIR（~/.local/state/remagraph/）
# 的 remagraph.db，與 "default" 專案自己的 memories 共用同一份檔案 —— 因為
# DEFAULT_STATE_DIR 是唯一一個「任何專案、任何時候都不需要額外設定就能解析
# 出來」的位置（resolve_project_state_dir 對其他 project_id 的解析結果視
# env/project.json 而定，唯獨 DEFAULT_STATE_DIR 本身是常數）。
#
# 刻意選擇「idempotent CREATE TABLE IF NOT EXISTS、獨立於一般 per-project
# migration chain」，而非替 SCHEMA_VERSION 新增一個 v6 migration 步驟：
# _run_migrations() 會在**每一個**專案的 connect() 都執行一次（見 connect()
# 呼叫序）。若把 project_registry 併入該 migration chain，會導致這張表被
# 建立在**每一個**專案自己的 remagraph.db 裡 —— 但這張表在概念上完全不屬於
# 任何單一專案自己的記憶 schema，只跟 DEFAULT_STATE_DIR 這個共用位置有關；
# 把它塞進每個專案自己的資料庫檔案裡，會把「這座孤島跟別的孤島無關」這個
# 既有隔離設計弄髒。因此改用一個獨立、專屬於 DEFAULT_STATE_DIR 連線路徑的
# idempotent 建表輔助函式（_connect_default_registry_db），只在真正需要
# 讀寫 registry 時才確保該表存在，完全不影響、也不出現在任何其他專案自己的
# 資料庫檔案裡，SCHEMA_VERSION 維持不變。
# ---------------------------------------------------------------------------

_PROJECT_REGISTRY_DDL = """
    CREATE TABLE IF NOT EXISTS project_registry (
        project_id TEXT PRIMARY KEY,
        state_dir  TEXT NOT NULL,
        first_seen TEXT NOT NULL,
        last_seen  TEXT NOT NULL
    )
"""


def _utcnow_iso() -> str:
    """回傳目前 UTC 時間的 ISO8601 字串，供 registry first_seen/last_seen 使用。

    獨立成小函式方便測試以 monkeypatch 控制時間（驗證 last_seen 確實更新、
    first_seen 確實保留），不必真的在測試裡 sleep。
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect_default_registry_db() -> sqlite3.Connection:
    """開啟『真正的』DEFAULT_STATE_DIR 的 remagraph.db 連線，供 registry 讀寫。

    刻意直接使用 DEFAULT_STATE_DIR 模組常數，完全不經過
    get_state_dir()/resolve_project_state_dir() 的環境變數導向解析 ——
    REMAGRAPH_STATE_DIR 一旦設定，會整個蓋掉呼叫端想要的目標目錄（見
    get_state_dir() 的優先序），而 registry 的存在理由正是「不論目前呼叫
    行程的 project 情境是什麼，都能有同一個、唯一的共用落地位置」。

    僅確保 project_registry 表存在（CREATE TABLE IF NOT EXISTS，冪等），
    刻意不呼叫 _init_schema()/_run_migrations() —— 那兩者屬於一般
    per-project 記憶 schema 的初始化路徑，registry 表與其無關，也不需要
    參與 memories/_meta 的 migration chain（見上方設計決策說明）。
    """
    DEFAULT_STATE_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_STATE_DIR.chmod(0o700)
    db_path = DEFAULT_STATE_DIR / DB_FILENAME
    conn = sqlite3.connect(
        str(db_path),
        isolation_level=None,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_PROJECT_REGISTRY_DDL)
    try:
        db_path.chmod(0o600)
    except OSError:
        pass
    return conn


def register_known_project(project_id: str, state_dir: Path | str) -> None:
    """把 (project_id, state_dir) 這對資訊 upsert 進共用 registry。

    Best-effort：任何失敗（目錄無法建立、DB 鎖定、權限不足……）一律吞下，
    絕不拋出例外 —— 呼叫端（目前是 maintenance.resolve_project_state_dir）
    依賴這個保證才能安全地在每次正常解析路徑上都呼叫本函式，而不必擔心
    registry 本身的問題會反過來弄壞主要功能。此慣例與 audit.append_event
    一致（且呼叫端仍應自行再包一層 try/except，屬於本專案既有的『雙重
    防禦』慣例，見 maintenance._record_violation 對 append_event 的呼叫
    方式）。

    first_seen 只在該 project_id 第一次出現時寫入；已存在的列只更新
    state_dir（若已改變）與 last_seen，不會產生重複列。
    """
    conn: sqlite3.Connection | None = None
    try:
        conn = _connect_default_registry_db()
        now = _utcnow_iso()
        resolved_state_dir = str(Path(state_dir).resolve())
        conn.execute(
            """
            INSERT INTO project_registry (project_id, state_dir, first_seen, last_seen)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
                state_dir = excluded.state_dir,
                last_seen = excluded.last_seen
            """,
            (project_id, resolved_state_dir, now, now),
        )
    except Exception:
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def list_known_projects() -> list[dict[str, str]]:
    """回傳 registry 內所有已知專案的 (project_id, state_dir, first_seen,
    last_seen)。

    永遠讀取 DEFAULT_STATE_DIR（透過 _connect_default_registry_db()），不受
    呼叫端當下 REMAGRAPH_STATE_DIR/REMAGRAPH_PROJECT 環境變數影響 —— 即使
    目前行程的 project 情境是別的專案，這裡看到的仍然是同一份共用 registry。
    任何讀取失敗一律回傳空清單，不拋出例外。
    """
    conn: sqlite3.Connection | None = None
    try:
        conn = _connect_default_registry_db()
        rows = conn.execute(
            "SELECT project_id, state_dir, first_seen, last_seen "
            "FROM project_registry ORDER BY project_id"
        ).fetchall()
        return [
            {
                "project_id": str(row["project_id"]),
                "state_dir": str(row["state_dir"]),
                "first_seen": str(row["first_seen"]),
                "last_seen": str(row["last_seen"]),
            }
            for row in rows
        ]
    except Exception:
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def connect_foreign_project_readonly(project_id: str) -> sqlite3.Connection | None:
    """開啟『另一個』已知專案的資料庫連線，供跨專案唯讀查閱使用。

    這是後續項目（4b 跨專案標籤搜尋、5 recall_related）需要的原語 ——
    本任務僅落地並測試這個原語本身，不實作實際的跨專案查詢邏輯。

    刻意不透過 connect()：get_state_dir() 對『目前呼叫行程』的
    REMAGRAPH_STATE_DIR 環境變數有最高優先權，若直接呼叫
    connect(state_dir=<foreign>) 而目前行程本身已設定 REMAGRAPH_STATE_DIR
    指向別的（自己的）目錄，get_state_dir() 會整個忽略傳入的 state_dir、
    逕自沿用 env 裡的目錄 —— 等同悄悄開錯資料庫，且不會拋出任何例外讓呼叫端
    得知。因此本函式直接組出目標 state_dir 的 db 路徑並自行開連線，完全不
    經過任何環境變數解析路徑，也完全不呼叫 safety_validate_project（那是
    保護『目前行程自己主要專案』設計的安全閥，不適用於主動、唯讀查閱『另一
    個』已知專案）、不觸發該外部專案的 light_maintenance_on_connect（純讀取
    用途，不該對它觸發維護）。

    連線額外設定 PRAGMA query_only=1，讓這個連線在 SQLite 層級真的無法
    寫入，落實「readonly」這個名稱的承諾。

    Returns:
        已就緒、真正唯讀的連線；若 project_id 不在 registry 內、其
        state_dir/db 檔案已不存在（registry 可能已過期 —— 對應目錄可能已
        被刪除）、或開啟過程任何原因失敗，一律回傳 None，絕不拋出例外。
    """
    conn: sqlite3.Connection | None = None
    row: sqlite3.Row | None = None
    try:
        conn = _connect_default_registry_db()
        row = conn.execute(
            "SELECT state_dir FROM project_registry WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    except Exception:
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    if row is None:
        return None

    state_dir_str = row["state_dir"]
    if not state_dir_str:
        return None

    foreign_db_path = Path(state_dir_str) / DB_FILENAME
    if not foreign_db_path.exists():
        return None

    try:
        foreign_conn = sqlite3.connect(
            str(foreign_db_path),
            isolation_level=None,
            check_same_thread=False,
        )
        foreign_conn.row_factory = sqlite3.Row
        foreign_conn.execute("PRAGMA query_only = 1")
        return foreign_conn
    except Exception:
        return None


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
        # 全新資料庫 —— 寫入初始版本，並同步種下前向相容性欄位
        # （min_reader_version / min_writer_version / upgrade_hint），
        # 不只讓 v4→v5 migration 路徑補上這些欄位。
        conn.execute(
            "INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO _meta (key, value) VALUES ('min_reader_version', ?)",
            (_MIN_READER_VERSION_DEFAULT,),
        )
        conn.execute(
            "INSERT OR REPLACE INTO _meta (key, value) VALUES ('min_writer_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO _meta (key, value) VALUES ('upgrade_hint', ?)",
            (_UPGRADE_HINT_TEXT,),
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
    if current_version == 4:
        _migrate_v4_to_v5(conn)
        current_version = 5

    if current_version > SCHEMA_VERSION:
        _handle_newer_than_code_schema(conn, current_version)


def _handle_newer_than_code_schema(conn: sqlite3.Connection, current_version: int) -> None:
    """資料庫 schema_version（current_version）比程式碼的 SCHEMA_VERSION 還新時
    的三層版本相容性判斷。

    比較程式碼的 SCHEMA_VERSION 與資料庫本身存下的 min_reader_version /
    min_writer_version（見 item 1，_migrate_v4_to_v5 起種下的前向相容性
    欄位）：

    1. SCHEMA_VERSION >= min_writer_version
       → 完全相容（讀寫皆安全）：不做任何事、正常 return，維持與過去
         schema_version <= SCHEMA_VERSION 時完全相同的行為。
    2. min_reader_version <= SCHEMA_VERSION < min_writer_version
       → 讀相容但寫不安全：不 raise，只在連線物件上掛唯讀標記
         （READ_ONLY_ATTR），交由呼叫端（store.process_store）在真正嘗試
         寫入時才擋下，讀取路徑（search/status）完全不受影響。
    3. SCHEMA_VERSION < min_reader_version
       → 連讀都不安全：維持 item 1 既有行為，raise MigrationError（靜態
         訊息 + 防禦性讀取的 upgrade_hint）。

    防禦性 fallback：min_reader_version / min_writer_version 任一讀取失敗
    或缺漏（例如結構已改變、連 _meta 表都不可信任的未來資料庫），一律視為
    兩者都等於 current_version，退回本函式重構前的嚴格全有全無行為 ——
    絕不能讓「讀不到欄位」意外變成寬鬆預設值。
    """
    min_reader = _read_meta_int_defensively(conn, "min_reader_version")
    min_writer = _read_meta_int_defensively(conn, "min_writer_version")
    if min_reader is None or min_writer is None:
        min_reader = current_version
        min_writer = current_version

    if SCHEMA_VERSION >= min_writer:
        # Tier 1：完全相容，維持原本「沒事發生」的行為。
        return

    stored_hint = _read_upgrade_hint_defensively(conn)

    if SCHEMA_VERSION >= min_reader:
        # Tier 2：讀相容、寫不安全 —— 不 raise，只標記唯讀，交給
        # store.process_store() 在寫入路徑擋下。
        detail = (
            f"此資料庫已升級至 schema_version={current_version}，其要求的最低"
            f"寫入相容版本 min_writer_version={min_writer} 高於目前執行的程式碼"
            f"版本 SCHEMA_VERSION={SCHEMA_VERSION}。為避免資料損毀，此連線已切換"
            "為唯讀模式（remagraph_search / remagraph_status 可正常使用），已"
            "拒絕本次寫入。請將已安裝的 remagraph 套件升級到與此資料庫相容的"
            "版本後再重試寫入。"
        )
        if stored_hint:
            detail += f" [資料庫內建升級提示] {stored_hint}"
        setattr(conn, READ_ONLY_ATTR, True)
        setattr(conn, READ_ONLY_DETAIL_ATTR, detail)
        return

    # Tier 3：連讀都不安全 —— 維持 item 1 既有的強制拒絕行為不變。
    message = (
        f"資料庫 schema_version={current_version} 比程式碼的 "
        f"SCHEMA_VERSION={SCHEMA_VERSION} 還新，無法降級。"
        "請選擇以下其一處理："
        "1) 更新已安裝的 remagraph 套件至相容此 schema 版本的版本；"
        "2) 設定 REMAGRAPH_STATE_DIR 指向另一個獨立目錄，改用全新資料庫；"
        "3) 若確認可捨棄此資料庫的既有資料，刪除該 state_dir 下的 "
        f"{DB_FILENAME} 後重新初始化。"
    )
    if stored_hint:
        message += f" [資料庫內建升級提示] {stored_hint}"
    raise MigrationError(message)


def _read_upgrade_hint_defensively(conn: sqlite3.Connection) -> str | None:
    """嘗試從 _meta 讀取 upgrade_hint，供拒絕降級時附加參考資訊。

    此函式必須極度防禦：呼叫當下的資料庫，其 schema_version 已確認比目前
    程式碼的 SCHEMA_VERSION 還新 —— 換言之它可能來自結構已改變的未來版本，
    連 _meta 表本身的存在或欄位是否符合預期都不可信任。任何失敗（表不存在、
    欄位不存在、讀取例外……）一律吞下並回傳 None，絕不讓例外中斷原本的
    拒絕流程 —— 現有靜態訊息必須永遠是可靠的最終防線。
    """
    try:
        row = conn.execute("SELECT value FROM _meta WHERE key = 'upgrade_hint'").fetchone()
    except Exception:
        return None
    if row is None:
        return None
    try:
        value = row[0]
    except Exception:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _read_meta_int_defensively(conn: sqlite3.Connection, key: str) -> int | None:
    """嘗試從 _meta 讀取指定 key 並轉為 int，供三層版本相容性判斷使用。

    與 _read_upgrade_hint_defensively 相同的防禦精神：呼叫當下的資料庫
    schema_version 已確認比程式碼的 SCHEMA_VERSION 還新，連 _meta 表本身
    的存在或欄位是否符合預期都不可信任。任何失敗（表不存在、欄位不存在、
    值無法轉為 int……）一律回傳 None，絕不拋出例外 —— 呼叫端會將 None
    視為「讀取失敗」並整體 fallback 回嚴格行為，而不是替單一欄位套用
    寬鬆預設值。
    """
    try:
        row = conn.execute("SELECT value FROM _meta WHERE key = ?", (key,)).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


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


def _migrate_v4_to_v5(conn: sqlite3.Connection) -> None:
    """v4→v5: 加入前向相容性 meta 欄位 —— min_reader_version、min_writer_version、
    upgrade_hint。目的是讓「資料庫本身」成為未來版本回頭教導舊消費端如何升級的
    管道，而不是依賴消費端當下執行中的程式碼版本（該版本一旦部署就凍結，
    永遠學不到日後對錯誤訊息的任何改進）。
    """
    conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('min_reader_version', ?)",
        (_MIN_READER_VERSION_DEFAULT,),
    )
    conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('min_writer_version', '5')"
    )
    conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('upgrade_hint', ?)",
        (_UPGRADE_HINT_TEXT,),
    )
    conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', '5')")
