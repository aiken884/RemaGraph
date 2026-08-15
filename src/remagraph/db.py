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
SCHEMA_VERSION = 6
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

# 全新資料庫與 migration 路徑必須種下同一個 min_writer_version（診斷發現的
# 不一致：全新 DB 過去種 SCHEMA_VERSION=6，但 _migrate_v5_to_v6 刻意保留 5
# ——其 docstring 論證 v5 舊程式寫 v6 DB 完全安全，因為 memory_labels 是純
# 新增的獨立表。同一 schema 兩種相容性判定，會讓 v6 程式「新建」的資料庫
# 把仍在跑 v5 釘版程式的消費端錯誤降級成唯讀）。未來若有真正修改 memories
# 表結構的 migration，兩處要一起升。
_MIN_WRITER_VERSION_DEFAULT = "5"

_UPGRADE_HINT_TEXT = (
    "This database's schema version is newer than the currently running "
    "remagraph code; to avoid data corruption, the code has refused to open "
    "it. Please choose one of the following: "
    "1) Upgrade the installed remagraph package to a version compatible with "
    "this database's schema; "
    "2) Set the REMAGRAPH_STATE_DIR environment variable to point to a new, "
    "separate directory and use a fresh database; "
    "3) If you are certain you can discard this database's existing data, "
    "find and delete the remagraph.db file under that state_dir and "
    "reinitialize."
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


def _resolve_default_state_dir() -> Path:
    """解析『共用/預設』state 目錄目前實際生效的位置。

    背景（外部 subprocess 隔離缺口）：DEFAULT_STATE_DIR 是模組載入當下就
    算好的常數（Path.home() / ".local" / "state" / "remagraph"），過去唯一
    能覆寫它的方式是 Python 層級 monkeypatch 這個模組屬性本身（見
    tests/conftest.py 的 _isolate_default_state_dir autouse fixture）——這
    只對「同一個 process 內」執行的程式碼有效。任何透過 subprocess 呼叫真正
    安裝好的 `remagraph` CLI 的外部整合測試/工具，是完全不同的 OS process，
    monkeypatch 完全碰不到它，會悄悄把寫入洩漏到這台機器真正的
    ~/.local/state/remagraph/remagraph.db（PPLX 架構審查確認過的真實缺口）。

    因此新增 REMAGRAPH_HOME 環境變數，作為 REMAGRAPH_STATE_DIR 之外、專門
    給外部消費端使用的獨立覆寫機制：
    - 有設定：套用與 REMAGRAPH_STATE_DIR 完全相同的安全驗證（
      _ALLOWED_STATE_DIR_RE 字元白名單 + 禁止落在系統目錄下），驗證通過後
      resolve() 回傳。
    - 未設定：原樣回傳目前的 DEFAULT_STATE_DIR 模組屬性 —— 不論它是否已被
      conftest.py monkeypatch 過。這保證兩個機制彼此獨立、可以同時並存：
      conftest.py 的 monkeypatch 繼續完整掌控 RemaGraph 自己 pytest 套件的
      隔離（走這個 else 分支），REMAGRAPH_HOME 則是外部消費端專屬、額外的
      環境變數層級覆寫（走 if 分支），兩者互不干擾。
    """
    env_home = os.environ.get("REMAGRAPH_HOME")
    if env_home:
        if not _ALLOWED_STATE_DIR_RE.match(env_home):
            raise ValueError(f"REMAGRAPH_HOME contains invalid characters: {env_home!r}")
        resolved_home = Path(env_home).resolve()
        forbidden_prefixes = ("/etc", "/usr", "/bin", "/sbin", "/dev", "/proc", "/sys")
        if str(resolved_home).startswith(forbidden_prefixes):
            raise ValueError(f"REMAGRAPH_HOME invalid: {resolved_home}")
        return resolved_home
    return DEFAULT_STATE_DIR


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
        # 與上面 REMAGRAPH_STATE_DIR 分支、下面 _resolve_default_state_dir()
        # 一致地呼叫 .resolve()：is_using_default_state_dir() 會拿這個分支的
        # 回傳值去跟 _resolve_default_state_dir()（一律回傳已 resolve 過的
        # 路徑）比較是否相等。若這裡不 resolve，呼叫端傳入的『同一個真實目錄』
        # 只要拼法不同（例如 macOS 上 /tmp vs /private/tmp、或帶了尚未展開的
        # ".." 片段），就會被誤判為不同目錄而回傳錯誤的 False（對抗式審查
        # 發現，見 tests/test_remagraph_home_env.py 的
        # test_is_using_default_state_dir_true_when_explicit_dir_is_unresolved_
        # spelling_of_remagraph_home）。
        resolved = state_dir.resolve()
    else:
        resolved = _resolve_default_state_dir()
    resolved.mkdir(parents=True, exist_ok=True)
    resolved.chmod(0o700)
    return resolved


def is_using_default_state_dir(state_dir: Path | None = None) -> bool:
    resolved = get_state_dir(state_dir)
    return resolved == _resolve_default_state_dir()


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
        env_project_id = os.environ.get("REMAGRAPH_PROJECT", "default")
        if env_project_id != "default":
            # 若 env 有 project 但未傳，嘗試驗證
            project_id = env_project_id
            if not skip_safety_check:
                resolved = safety_validate_project(project_id)
                state_dir = resolved

    db_path = get_db_path(state_dir=state_dir)

    conn = sqlite3.connect(
        str(db_path),
        # busy_timeout=150ms（0.7.0 項目 C，PPLX 審查定案）：SQLite 層的
        # busy handler 等待上限。刻意建線時固定而非交易區間動態切換——
        # check_same_thread=False 的共用連線上動態改 PRAGMA 有競態面；
        # 短 timeout 搭配 process_store 的應用層退避重試（L3），最壞總
        # 預算約 1.3 秒。
        timeout=0.15,
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

    # 執行 migration chain（必須先於 _init_schema！）
    #
    # 背景（真正的 v1 舊資料庫崩潰 bug）：_init_schema() 的 executescript
    # 內含 `CREATE TABLE IF NOT EXISTS memories (...)`（對已存在的舊表是
    # no-op），但同一個 script 稍後又有
    # `CREATE INDEX IF NOT EXISTS idx_memories_project_id ON memories(project_id)`。
    # 若對一個貨真價實的 v1 資料庫（project_id 欄位是後來才由
    # _migrate_v2_to_v3 的 ALTER TABLE 加上）先呼叫 _init_schema()，這條
    # CREATE INDEX 會直接以 sqlite3.OperationalError: no such column:
    # project_id 崩潰 —— migration chain 永遠沒有機會把該欄位補上。
    #
    # 因此必須先讓 _run_migrations() 把舊資料庫的 memories 表結構（欄位、
    # CHECK 約束）逐步升到目前的 SCHEMA_VERSION，_init_schema() 之後才接手
    # 補上 FTS5 虛擬表、triggers、indexes、memory_labels 等 migration chain
    # 本身不負責的 DDL 物件（全部使用 IF NOT EXISTS，對已是目前版本的資料庫
    # 或全新資料庫皆為安全的冪等操作）。
    #
    # 對全新資料庫（無既有檔案）：_run_migrations() 偵測不到 _meta.schema_
    # version（尚未存在），走「全新資料庫」分支，只寫入 _meta 欄位、不觸碰
    # memories 表；隨後 _init_schema() 照常從零建立完整的目前版本 schema ——
    # 與修復前的行為完全一致，只是兩步驟的呼叫順序對調。
    # 對已是目前 SCHEMA_VERSION 的資料庫：_run_migrations() 判定版本相符後
    # 直接 no-op 返回；隨後 _init_schema() 的每一條 IF NOT EXISTS 語句也都
    # 是 no-op —— 同樣與修復前的行為完全一致。
    needs_fts_rebuild = _run_migrations(conn)
    # 執行 schema 初始化（見上方說明：此時 memories 表若曾是舊版，已由
    # migration chain 補齊必要欄位/約束，以下皆為安全的冪等 IF NOT EXISTS）
    _init_schema(conn)
    if needs_fts_rebuild:
        # v1/v2/v3 起點的資料庫在 migration 後 rowid 位移或 FTS 索引尚未
        # 建立過內容——此時 memories_fts 已由 _init_schema 保證存在，
        # rebuild 一次讓全文索引與 memories 表重新對齊（見 _run_migrations
        # docstring 的診斷修復說明）。
        conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
        conn.commit()

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


def connect_at_state_dir(state_dir: Path) -> sqlite3.Connection:
    """在明確、呼叫端已完成合法性驗證的 state_dir 開啟一個可讀寫連線。

    背景（PPLX 架構改善計畫 —— migrate-project 真實實作缺口修復）：
    store.migrate_project_memories() 需要分別開啟『來源專案』與『目標專案』
    兩個各自明確的 state_dir 的連線 —— 但這兩個 state_dir 都是呼叫端（透過
    db.get_registered_state_dir()/maintenance.safety_validate_project() 或
    from_project == DEFAULT_PROJECT_ID 的特例）主動解析出來的權威路徑，與
    目前這個行程 REMAGRAPH_STATE_DIR/REMAGRAPH_PROJECT 環境變數剛好是什麼
    完全無關。

    若改用一般的 connect(state_dir=...)：get_state_dir() 對 REMAGRAPH_
    STATE_DIR 環境變數有最高優先權，一旦目前行程已設定該變數（例如 MCP
    server 長駐行程啟動時透過 server._bind_project 綁定了自己主要專案的
    state_dir——見 server.py），傳入的 state_dir 參數會被整個忽略，實際打開
    的其實是行程自己主要專案的資料庫，而不是呼叫端真正要求的遷移來源/目標
    ——等同悄悄操作錯的 DB 檔案，且不會拋出任何例外讓呼叫端得知。

    因此本函式完全比照 connect_foreign_project_readonly() 的既有設計哲學
    （見該函式 docstring）：直接對明確路徑開連線，完全不經過
    get_state_dir()/get_db_path() 的環境變數解析路徑。與
    connect_foreign_project_readonly() 的差異：本函式開的是一般可讀寫連線
    （不強制 PRAGMA query_only），並且會執行與 connect() 完全相同的
    _run_migrations()/_init_schema() 步驟（讓一個從未被開過的全新目標
    專案目錄，也能像 connect() 一樣自動補齊完整 schema，而不是像修復前的
    CLI migrate-project 那樣，對一個尚未存在 memories 表的全新目標 DB 直接
    INSERT 而崩潰）——因此若來源/目標的 schema 版本比目前程式碼新，回傳的
    連線一樣會被 _run_migrations() 掛上 db.READ_ONLY_ATTR 唯讀降級標記，供
    呼叫端在真正寫入前檢查、清楚拒絕。

    刻意不呼叫 safety_validate_project()（那是「驗證 project_id 是否有權
    使用某個 state_dir」的業務規則，呼叫端必須在拿到 state_dir 之前、自行
    決定是否需要那層驗證——本函式只負責『對已經決定好的路徑開連線』這一件
    事）、不觸發 light_maintenance_on_connect（呼叫端未必是以某個
    project_id 的身份操作這個連線，觸發該專案自己的維護排程並不恰當）。

    Args:
        state_dir: 已解析、呼叫端保證合法的目標目錄（可能尚不存在，會被
            自動建立）。

    Returns:
        已完成 migration/schema 初始化的連線。若該資料庫的 schema 版本比
        目前程式碼新，回傳的連線會被標記 READ_ONLY_ATTR = True。
    """
    resolved = Path(state_dir).resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    resolved.chmod(0o700)
    db_path = resolved / DB_FILENAME

    conn = sqlite3.connect(
        str(db_path),
        timeout=0.15,  # busy_timeout 150ms，同 connect()（0.7.0 項目 C）
        isolation_level=None,
        check_same_thread=False,
        factory=_MarkedConnection,
    )
    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    max_pages = (MAX_DB_SIZE_MB * 1024 * 1024) // page_size
    conn.execute(f"PRAGMA max_page_count={max_pages}")

    needs_fts_rebuild = _run_migrations(conn)
    _init_schema(conn)
    if needs_fts_rebuild:
        # 與 connect() 相同的 migration 後 FTS rebuild（見 _run_migrations
        # docstring 的診斷修復說明）。
        conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
        conn.commit()

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

# ---------------------------------------------------------------------------
# Cross-project edges（PPLX 架構改善計畫 item 5）
#
# 背景：item 4a 的 project_registry 只記錄「有哪些 project 存在、各自的
# state_dir 在哪」，並不記錄「這些 project 彼此之間有什麼關係」。item 5 需要
# 這層關係，才能實作 recall_related()：從某個 project_id 出發，沿著明確宣告
# 過的關聯邊，找出範圍受限（而非 item 4b cross_project_label 那種對『所有』
# 已知專案無差別 fan-out）的一組「相關」專案。
#
# 這是專案『之間』的關聯 metadata（不屬於任一單一專案自己的記憶內容），因此
# 依循與 project_registry 完全相同的落地決策：落在 DEFAULT_STATE_DIR 的
# remagraph.db（與 project_registry 同一份檔案），透過同一個
# _connect_default_registry_db() 冪等建表輔助函式管理，同樣刻意獨立於
# per-project 的 SCHEMA_VERSION migration chain（理由與 project_registry
# 完全相同，見上方 item 4a 的大段說明——這張表與任何單一專案自己的記憶
# schema 無關，混進每個專案自己的資料庫檔案會弄髒既有的『孤島』隔離設計）。
# ---------------------------------------------------------------------------

_PROJECT_EDGES_DDL = """
    CREATE TABLE IF NOT EXISTS project_edges (
        from_project TEXT NOT NULL,
        to_project   TEXT NOT NULL,
        relation     TEXT NOT NULL CHECK (
            relation IN ('depends_on', 'sibling', 'shares_upstream', 'monorepo_member')
        ),
        created_at   TEXT NOT NULL,
        PRIMARY KEY (from_project, to_project, relation)
    )
"""

# 與 DDL 內的 CHECK 約束保持同步的 Python 端合法值集合，供
# declare_project_edge() 在寫入資料庫前先行驗證，讓呼叫端拿到的是清楚的
# ValueError（而不是等 CHECK 約束在 SQL 層才失敗、被目前 best-effort 的
# try/except Exception 吞掉、悄無聲息地什麼都沒發生）。
_VALID_PROJECT_EDGE_RELATIONS = frozenset(
    {"depends_on", "sibling", "shares_upstream", "monorepo_member"}
)


def _utcnow_iso() -> str:
    """回傳目前 UTC 時間的 ISO8601 字串，供 registry first_seen/last_seen 使用。

    獨立成小函式方便測試以 monkeypatch 控制時間（驗證 last_seen 確實更新、
    first_seen 確實保留），不必真的在測試裡 sleep。
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect_default_registry_db() -> sqlite3.Connection:
    """開啟『真正的』DEFAULT_STATE_DIR 的 remagraph.db 連線，供 registry 讀寫。

    刻意直接使用 _resolve_default_state_dir() 的解析結果，完全不經過
    get_state_dir()/resolve_project_state_dir() 的一般環境變數導向解析 ——
    REMAGRAPH_STATE_DIR 一旦設定，會整個蓋掉呼叫端想要的目標目錄（見
    get_state_dir() 的優先序），而 registry 的存在理由正是「不論目前呼叫
    行程的 project 情境是什麼，都能有同一個、唯一的共用落地位置」。

    _resolve_default_state_dir() 本身只認 REMAGRAPH_HOME 這個獨立於
    REMAGRAPH_STATE_DIR 之外的專屬環境變數（未設定時原樣回傳 DEFAULT_
    STATE_DIR 模組常數，見該函式 docstring）——因此外部消費端（例如透過
    subprocess 呼叫真正安裝好的 `remagraph` CLI 的整合測試）可以用
    REMAGRAPH_HOME 重新導向這個共用落地位置，而不必依賴只在同一個 process
    內有效的 Python 層級 monkeypatch。

    僅確保 project_registry 與 project_edges（PPLX 架構改善計畫 item 5，見
    下方說明）兩張表存在（皆為 CREATE TABLE IF NOT EXISTS，冪等），刻意不
    呼叫 _init_schema()/_run_migrations() —— 那兩者屬於一般 per-project
    記憶 schema 的初始化路徑，這兩張表與其無關，也不需要參與 memories/_meta
    的 migration chain（見上方設計決策說明）。
    """
    default_state_dir = _resolve_default_state_dir()
    default_state_dir.mkdir(parents=True, exist_ok=True)
    default_state_dir.chmod(0o700)
    db_path = default_state_dir / DB_FILENAME
    conn = sqlite3.connect(
        str(db_path),
        # busy_timeout 150ms（0.7.0 項目 C 對抗式審查 #3 補實作）：registry
        # 是多 agent 每次路徑解析都會 upsert 的最高頻共用寫入點，長鎖時
        # 不該讓每個 CLI 指令卡到 sqlite 預設的 5 秒。
        timeout=0.15,
        isolation_level=None,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_PROJECT_REGISTRY_DDL)
    conn.execute(_PROJECT_EDGES_DDL)
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


def get_registered_state_dir(project_id: str) -> str | None:
    """回傳單一 project_id 在 registry 內登記的 state_dir 字串；查無此
    project_id 或任何讀取失敗一律回傳 None，絕不拋出例外。

    與 list_known_projects() 的差異：list_known_projects() 一次讀出『所有』
    已知專案，供需要枚舉全量候選清單的呼叫端使用（例如
    search._search_cross_project_by_label 的 item 4b fan-out 候選來源）；
    本函式只針對『單一』已知 project_id 查詢其 state_dir，供只需要「這一個
    project_id 的 state_dir 是什麼」這種窄範圍查詢的呼叫端使用——例如
    search._cross_project_fanout 的物理路徑別名判斷：它必須對『任何』呼叫端
    傳入的候選逐一判斷（包括 item 5 include_related 只窄範圍 fan-out 到
    project_edges 關聯專案的情境），不應該因此連帶讓該呼叫端也枚舉一次
    全量已知專案清單——見
    tests/test_project_edges_and_recall_related.py 的
    test_cross_project_label_include_related_all_projects_are_fully_decoupled
    對 list_known_projects() 呼叫次數的明確斷言。

    永遠讀取 DEFAULT_STATE_DIR（透過 _connect_default_registry_db()），與
    list_known_projects()/connect_foreign_project_readonly() 一致。
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
    state_dir = row["state_dir"]
    return str(state_dir) if state_dir else None


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

    TOCTOU 安全性（獨立對抗式審查發現，追蹤事項 #22）：改以 SQLite URI
    `file:<path>?mode=ro` 開啟連線（sqlite3.connect(uri, uri=True)），而不是
    依賴一個獨立的 `foreign_db_path.exists()` 預檢查 + 一般模式 connect()。
    原因：一般模式的 sqlite3.connect() 對『不存在的檔案』並不會拋出例外，
    而是悄悄建立一個全新的空白資料庫檔案——若檔案在 exists() 檢查之後、
    connect() 呼叫之前才被刪除（另一行程清掉了該 project 的 state_dir），
    舊實作會回傳一個「看起來正常、實則完全空白」的連線，違反本函式
    『絕不憑空生出一個新的空資料庫』的承諾。mode=ro 讓 SQLite 在檔案不存在
    時於 connect() 呼叫當下就直接拋出 sqlite3.OperationalError（mode=ro
    明確禁止建立新檔案），因此這個競態窗口從根本上不存在——真正的安全機制
    是 connect() 本身，不是任何檢查時間點的快照。下方仍保留一個
    `foreign_db_path.exists()` 判斷，但純粹是快速路徑最佳化（避免明知不存在
    還嘗試開啟連線、產生不必要的 OperationalError 例外），不是安全機制本身；
    即使這個預檢查因競態而誤報「存在」，下面的 mode=ro connect() 仍會在
    真正嘗試開啟時正確地拋出並被捕捉、回傳 None（見
    tests/test_project_registry.py 的
    test_connect_foreign_project_readonly_closes_toctou_gap_via_ro_uri，
    專門驗證此點——刻意讓 exists() 預檢查說謊也不影響最終正確性）。

    Returns:
        已就緒、真正唯讀的連線；若 project_id 不在 registry 內、其
        state_dir/db 檔案已不存在（registry 可能已過期 —— 對應目錄可能已
        被刪除，或在檢查與開啟之間才被刪除）、或開啟過程任何原因失敗，一律
        回傳 None，絕不拋出例外，也絕不會在該路徑憑空建立新檔案。
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
        # 快速路徑最佳化，非安全機制本身 —— 見上方 docstring。即使這個判斷
        # 因競態而誤報，下面的 mode=ro connect() 仍是真正把關的那一道。
        return None

    try:
        foreign_conn = sqlite3.connect(
            f"file:{foreign_db_path}?mode=ro",
            uri=True,
            isolation_level=None,
            check_same_thread=False,
        )
        foreign_conn.row_factory = sqlite3.Row
        foreign_conn.execute("PRAGMA query_only = 1")
        return foreign_conn
    except sqlite3.OperationalError:
        # mode=ro 對不存在的檔案會在此處拋出（"unable to open database
        # file"）——這正是 TOCTOU 競態視窗（exists() 檢查之後、connect() 之前
        # 檔案才被刪除）下會發生的情況。mode=ro 明確禁止建立新檔案，因此絕不
        # 會像一般模式那樣悄悄生出一個空白資料庫；正確地回傳 None。
        return None
    except Exception:
        return None


def declare_project_edge(from_project: str, to_project: str, relation: str) -> None:
    """宣告一筆 (from_project, to_project, relation) 關聯 edge（PPLX 架構
    改善計畫 item 5），供 recall_related() traversal 使用。

    冪等：同一個 (from_project, to_project, relation) 三元組重複宣告只是
    no-op（PRIMARY KEY 衝突時以 INSERT OR IGNORE 靜默略過，不更新
    created_at ——一筆關聯『何時第一次被宣告』本身沒有理由被後續重複宣告
    改寫，這點與 project_registry.first_seen 的保留邏輯精神一致）。

    錯誤處理的兩種層次，刻意分開對待（與 register_known_project 的純
    best-effort 慣例不同，這裡多了一層）：

    1. relation 不在 _VALID_PROJECT_EDGE_RELATIONS 內 —— 這是呼叫端的
       程式設計錯誤（傳入了 schema 不接受的關係類型），而不是「寫入當下
       剛好失敗」這種基礎設施層級的問題。呼叫端應該在開發階段就發現並修正
       這個錯誤，而不是被靜默吞掉、造成「明明呼叫了卻什麼都沒發生」的
       困惑——因此明確 raise ValueError，不吞。CLI 的 `remagraph link`
       子命令會捕捉這個例外並轉換為使用者可讀的錯誤訊息（見 cli.cmd_link）。
    2. 目錄無法建立、DB 鎖定、權限不足等基礎設施層級的失敗 —— 與
       register_known_project 一致，一律 best-effort 吞下、不拋出，因為
       呼叫端（CLI `link` 子命令）在這類情境下能做的補救有限，且不應該讓
       「registry 這個輔助功能寫入失敗」阻斷呼叫端原本要做的事。
    """
    if relation not in _VALID_PROJECT_EDGE_RELATIONS:
        raise ValueError(
            f"invalid relation {relation!r}; must be one of "
            f"{sorted(_VALID_PROJECT_EDGE_RELATIONS)}"
        )

    conn: sqlite3.Connection | None = None
    try:
        conn = _connect_default_registry_db()
        now = _utcnow_iso()
        conn.execute(
            """
            INSERT OR IGNORE INTO project_edges
                (from_project, to_project, relation, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (from_project, to_project, relation, now),
        )
    except Exception:
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def get_project_edges(project_id: str) -> list[dict[str, Any]]:
    """回傳 project_id 涉及的所有 edges，不論它是 from_project 還是
    to_project。

    對稱讀取的理由：對「什麼東西跟我相關」這個問題而言，關聯本身在
    traversal 的意義上並不因為『是我宣告的』還是『對方宣告的』而有差異——
    A 宣告了 A depends_on B，從 B 的角度看，「A 相關」這件事同樣成立
    （見下方 recall_related() docstring 對於方向性/對稱性更完整的討論）。
    因此本函式一律用 `WHERE from_project = ? OR to_project = ?` 查詢，讓
    呼叫端不必自己判斷、也不必記得兩次分別查詢兩個方向。

    任何讀取失敗一律回傳空清單，不拋出例外（與 list_known_projects 一致的
    防禦慣例）。
    """
    conn: sqlite3.Connection | None = None
    try:
        conn = _connect_default_registry_db()
        rows = conn.execute(
            """
            SELECT from_project, to_project, relation, created_at
            FROM project_edges
            WHERE from_project = ? OR to_project = ?
            ORDER BY from_project, to_project, relation
            """,
            (project_id, project_id),
        ).fetchall()
        return [
            {
                "from_project": str(row["from_project"]),
                "to_project": str(row["to_project"]),
                "relation": str(row["relation"]),
                "created_at": str(row["created_at"]),
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


def recall_related(project_id: str, hops: int = 1) -> set[str]:
    """從 project_id 出發，沿 project_edges 做廣度優先搜尋（BFS），回傳
    `hops` 層以內、所有相關的 project_id 集合（不含 project_id 自己）。

    方向性 vs. 對稱性的設計決策（四種 relation 一律視為對稱、雙向可走）：

    project_edges 的 schema 本身是有方向的（from_project/to_project 各自
    是獨立欄位），這保留了『誰宣告的、宣告時的方向語意是什麼』這個歷史
    紀錄；但本函式的 traversal 刻意把全部四種 relation 都當成無向邊來走，
    理由：

    - sibling / shares_upstream：這兩者從語意上就是互相的關係——A 是 B
      的 sibling，等同 B 也是 A 的 sibling；A、B 共享同一個上游，這個事實
      對雙方都成立。方向性在這裡只是『誰先打了這行指令』的偶然，不帶有
      任何額外資訊，若只單向可走，反而會產生「B 明明也 sibling A，卻在
      recall 時看不到 A」這種違反直覺的不對稱。
    - depends_on：這是四者中唯一『看起來』該有方向性的——A depends_on B
      直覺上像是「A 需要知道 B 的事，B 不需要知道 A 的事」。但本專案的
      recall_related 目的並非建構一個嚴謹的相依關係圖（例如拓樸排序、
      建構順序），而是『這個 agent 手上這個專案，還有哪些别的專案的記憶
      可能對目前這個任務有幫助』這種盡力而為的探索式召回：若 A
      depends_on B，B 的變更（例如 API 修改、已知限制）幾乎必然是 A 需要
      知道的事——這正是既有的正向直覺；但反過來，A 使用 B 過程中踩到的坑
      （例如「呼叫 B 的某個 API 時要注意這個限制」）對『正在維護 B』的
      agent 來說同樣是有價值的上游回饋（B 的維護者常常需要知道下游是怎麼
      用的、遇到什麼問題）。既然 recall_related 只是『多找一些可能有關的
      上下文來源』、而非強制寫入或改變任何資料，讓下游/上游都能雙向互相
      發現彼此的記憶，利大於弊。
    - monorepo_member：同一個 monorepo 的成員關係本質上是群組隸屬，天生
      對稱（A、B 同屬一個 monorepo，這件事不因宣告方向而改變）。

    綜合以上：四種 relation 在 traversal 上一律對稱處理，這與
    get_project_edges() 本身（不論從哪一側查詢都找得到同一筆 edge）的
    對稱讀取語意完全一致，也是實作上最簡單、最不容易在未來出現「咦，這個
    方向怎麼漏了」這種細微 bug 的做法。

    cycle 安全：以 visited 集合追蹤已走訪過的 project_id，每一層只擴展
    尚未走訪過的鄰居，因此即使 edge graph 中存在環（例如 A-B-C-A），也不會
    重複走訪、不會無窮迴圈——最多在『目前已知的所有 project_id 數量』耗盡
    前就會自然終止（frontier 收斂為空集合時提前 break）。

    hops<=0 時直接回傳空集合（沒有任何一層可以走）。
    """
    if hops <= 0:
        return set()

    visited: set[str] = {project_id}
    frontier: set[str] = {project_id}
    related: set[str] = set()

    for _ in range(hops):
        if not frontier:
            break
        next_frontier: set[str] = set()
        for pid in frontier:
            for edge in get_project_edges(pid):
                neighbor = (
                    edge["to_project"] if edge["from_project"] == pid else edge["from_project"]
                )
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                next_frontier.add(neighbor)
                related.add(neighbor)
        frontier = next_frontier

    return related


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

        -- 每個記憶可掛上多個命名空間化標籤（PPLX 架構改善計畫 item 4b），
        -- 供跨專案標籤搜尋使用（見 db.list_known_projects /
        -- db.connect_foreign_project_readonly，item 4a）。標籤格式慣例
        -- （namespace:value，如 dep:opencode、topic:auth）由 arbitration.py
        -- 的 validate_labels() 於 store 時驗證，本表本身不對 label 內容加
        -- CHECK 約束（與 tags 欄位一致，驗證邏輯放在應用層而非 DDL 層）。
        CREATE TABLE IF NOT EXISTS memory_labels (
            memory_id TEXT NOT NULL REFERENCES memories(id),
            label     TEXT NOT NULL,
            PRIMARY KEY (memory_id, label)
        );

        -- 依 label 本身查詢（不限定 memory_id）的效能 index —— 跨專案標籤
        -- 搜尋會對每個已知專案各自的資料庫都執行「WHERE label = ?」，此
        -- index 讓每個資料庫檔案各自的這類查詢維持高效。
        CREATE INDEX IF NOT EXISTS idx_memory_labels_label ON memory_labels(label);
    """)


def _run_migrations(conn: sqlite3.Connection) -> bool:
    """檢查 _meta.schema_version 並執行 migration chain。

    回傳值：是否需要在 _init_schema() 之後 rebuild memories_fts——起始版本
    < 4 時為 True（診斷修復）：(a) v3 起點經 _migrate_v3_to_v4 重建 memories
    表，rowid 位移使既有 FTS 索引指向錯誤的列；(b) v1/v2 起點在整條鏈中
    memories_fts 根本尚未存在（稍後才由 _init_schema 建立），external-content
    FTS 虛擬表建立時不會自動索引既有列，不 rebuild 的話全文檢索永遠是空的。
    兩種情況都必須在 memories_fts 確定存在（_init_schema 之後）rebuild 一次，
    因此交由呼叫端（connect()/connect_at_state_dir()）依本回傳值執行。
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
            (_MIN_WRITER_VERSION_DEFAULT,),
        )
        conn.execute(
            "INSERT OR REPLACE INTO _meta (key, value) VALUES ('upgrade_hint', ?)",
            (_UPGRADE_HINT_TEXT,),
        )
        return False

    current_version = int(row[0])

    if current_version == SCHEMA_VERSION:
        # 已是最新版本。修補存量資料庫的 min_writer_version（對抗式審查
        # 發現的缺口）：_MIN_WRITER_VERSION_DEFAULT 修復只治「新建」DB，
        # 修復前由 v6 程式建立的存量 DB 仍帶著錯種的 min_writer_version=6，
        # v5 釘版消費端對它們照樣被錯誤降級唯讀。此回填嚴格限定
        # SCHEMA_VERSION == 6 且值恰為錯種的 "6"——未來版本若真的需要提高
        # min_writer_version，由該版的 migration 自行設定，不受此修補影響。
        if SCHEMA_VERSION == 6:
            mw_row = conn.execute(
                "SELECT value FROM _meta WHERE key='min_writer_version'"
            ).fetchone()
            if mw_row is not None and mw_row[0] == "6":
                conn.execute(
                    "INSERT OR REPLACE INTO _meta (key, value) "
                    "VALUES ('min_writer_version', ?)",
                    (_MIN_WRITER_VERSION_DEFAULT,),
                )
        return False

    initial_version = current_version

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
    if current_version == 5:
        _migrate_v5_to_v6(conn)
        current_version = 6

    if current_version > SCHEMA_VERSION:
        _handle_newer_than_code_schema(conn, current_version)

    return initial_version < 4


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
            f"This database has been upgraded to schema_version={current_version}, "
            f"whose required minimum write-compatible version "
            f"min_writer_version={min_writer} is higher than the currently "
            f"running code's version SCHEMA_VERSION={SCHEMA_VERSION}. To avoid "
            "data corruption, this connection has switched to read-only mode "
            "(remagraph_search / remagraph_status remain usable); this write "
            "has been rejected. Please upgrade the installed remagraph package "
            "to a version compatible with this database before retrying the write."
        )
        if stored_hint:
            detail += f" [database-embedded upgrade hint] {stored_hint}"
        setattr(conn, READ_ONLY_ATTR, True)
        setattr(conn, READ_ONLY_DETAIL_ATTR, detail)
        return

    # Tier 3：連讀都不安全 —— 維持 item 1 既有的強制拒絕行為不變。
    message = (
        f"Database schema_version={current_version} is newer than the code's "
        f"SCHEMA_VERSION={SCHEMA_VERSION}; cannot downgrade. "
        "Please choose one of the following: "
        "1) Update the installed remagraph package to a version compatible "
        "with this schema version; "
        "2) Set REMAGRAPH_STATE_DIR to point to a separate directory and use "
        "a fresh database; "
        "3) If you are certain you can discard this database's existing "
        f"data, delete {DB_FILENAME} under that state_dir and reinitialize."
    )
    if stored_hint:
        message += f" [database-embedded upgrade hint] {stored_hint}"
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

    重要：`INSERT INTO memories_new (...) SELECT (...) FROM memories` 必須
    明確列出兩邊的欄位名稱、逐一對應，不可用 `SELECT *`（純位置對應）。
    原因：_migrate_v2_to_v3 是用 `ALTER TABLE memories ADD COLUMN
    project_id ...` 加上這個欄位 —— SQLite 的 ALTER TABLE ADD COLUMN 一律
    把新欄位加在表的**最後面**，而 memories_new 宣告的欄位順序是
    project_id 緊接在 id 之後（對齊 _init_schema 的宣告順序）。若沿用
    `SELECT *`，來源資料列會被整組錯位塞進去（例如 kind 的值被塞進
    project_id 欄位、task_id 的值被塞進 kind 欄位……），對一個真正的
    v1→v2→v3→v4 資料庫會直接讓 CHECK/NOT NULL 約束炸掉，或更糟：在欄位
    剛好都是 TEXT 型別、約束又剛好沒違反時，資料被靜默錯位寫入而不拋出
    任何錯誤。已用獨立重現腳本驗證：`SELECT *` 版本對貨真價實的 v1 資料會
    以 `sqlite3.IntegrityError: NOT NULL constraint failed:
    memories_new.status` 崩潰（embedding 的 NULL 值被錯位塞進 status 這個
    NOT NULL 欄位）。
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
        INSERT INTO memories_new (
            id, project_id, kind, task_id, agent_id, timestamp, summary,
            learnings, handoff_note, tags, status, embedding, created_at, updated_at
        )
        SELECT
            id, project_id, kind, task_id, agent_id, timestamp, summary,
            learnings, handoff_note, tags, status, embedding, created_at, updated_at
        FROM memories;
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
    # 注意：上方 INSERT INTO memories_new ... SELECT 不帶 rowid，新表的列
    # 取得「連續」的新 rowid；v3 時代只要發生過任何 DELETE，舊 rowid 就有
    # 空洞，重建後全面位移——external-content 的 memories_fts 以 rowid 為
    # 索引 key，因此本 migration 執行後 FTS 索引必須 rebuild。rebuild 不在
    # 此處做（貨真價實的 v1/v2 起點資料庫走到這裡時 memories_fts 尚不存在
    # ——該虛擬表由 _init_schema 建立，而 connect() 是先跑 migration chain
    # 才跑 _init_schema），統一由 connect()/connect_at_state_dir() 在
    # _init_schema 之後依 _run_migrations 的回傳值執行（診斷修復）。
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


def _migrate_v5_to_v6(conn: sqlite3.Connection) -> None:
    """v5→v6: 加入 memory_labels 多對多命名空間標籤表（PPLX 架構改善計畫
    item 4b）。

    與 _init_schema() 內對 memory_labels 的定義完全一致（皆為
    IF NOT EXISTS，冪等），確保透過 _init_schema 直接建立的全新資料庫，
    與透過本 migration 補上該表的既有 v5 資料庫，最終結構完全相同。

    刻意不更新 min_reader_version / min_writer_version（維持 v4→v5 種下的
    值不變）：memory_labels 是純新增的獨立表，不修改 memories 表本身的欄位
    或 CHECK 約束，因此仍未升級的舊版（SCHEMA_VERSION=5）程式碼，對一個已
    升級到 v6 的資料庫寫入一般 memories 記錄仍然完全安全——只是不會意識到
    / 不會寫入 memory_labels 而已，不構成資料損毀風險。這與 v4→v5 那次
    migration 的情境不同（該次的 min_writer_version 語意是防範『未來若真的
    修改 memories 表結構』的假設性風險，而非本次這種純加法變更）。
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS memory_labels (
            memory_id TEXT NOT NULL REFERENCES memories(id),
            label     TEXT NOT NULL,
            PRIMARY KEY (memory_id, label)
        );
        CREATE INDEX IF NOT EXISTS idx_memory_labels_label ON memory_labels(label);
    """)
    conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', '6')")
