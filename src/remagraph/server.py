# SPDX-License-Identifier: Apache-2.0
"""MCP server entrypoint (stdio transport) + CLI subcommands。

透過程式進入點自動判斷模式：
- `remagraph serve` → MCP stdio server（既有）
- `remagraph store/search/status` → CLI subcommand（headless agent 用）
"""

from __future__ import annotations

import atexit
import os
import sqlite3
import sys
import threading
import time
import warnings as _warnings
from collections import defaultdict
from pathlib import Path
from typing import Any

from remagraph import db as _db
from remagraph import maintenance
from remagraph.maintenance import MaintenancePolicy, run_maintenance, safety_validate_project
from remagraph.models import SearchRequest, StatusRequest, StoreRequest
from remagraph.search import get_status, search_memories
from remagraph.store import migrate_project_memories, process_store

# 抑制 mcp 依賴鏈（pydantic_settings 對 FastMCP Settings 的
# IncompleteFieldDefinitionWarning）在每次 CLI 呼叫時印到 stderr 的雜訊——
# console entry point 是本模組，連 prompt-hook（UserPromptSubmit，每一則
# 使用者輸入都會執行）都會因頂層 import 印出這段上游警告。filter 必須在
# import mcp 之前生效，因此 FastMCP 的 import 刻意殿後（noqa: E402）。
_warnings.filterwarnings(
    "ignore", message=".*lifespan.*incomplete definition.*"
)
from mcp.server.fastmcp import FastMCP  # noqa: E402

# ---------------------------------------------------------------------------
# Rate limiter（簡易記憶體 token bucket, per agent_id）
# ---------------------------------------------------------------------------

_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX = 60  # calls per window


_RATE_LIMIT_SWEEP_THRESHOLD = 512
"""_buckets 的 key 數超過此值時，check() 順便全域清掃已完全過期的 key。

key 是呼叫端提供的任意 agent_id，且過期 timestamp 原本只在同 key 再次
check() 時才清理——長駐 serve 行程被大量不同 agent_id 呼叫時，_buckets
會單調成長（診斷發現的記憶體洩漏）。門檻設得比正常 agent 數高很多，
一般情境下永遠不會觸發掃描成本。"""


class _RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> bool:
        now = time.monotonic()
        window_start = now - _RATE_LIMIT_WINDOW
        with self._lock:
            if len(self._buckets) > _RATE_LIMIT_SWEEP_THRESHOLD:
                stale_keys = [
                    k
                    for k, ts in self._buckets.items()
                    if k != key and not any(t > window_start for t in ts)
                ]
                for k in stale_keys:
                    del self._buckets[k]
            self._buckets[key] = [t for t in self._buckets[key] if t > window_start]
            if len(self._buckets[key]) >= _RATE_LIMIT_MAX:
                return False
            self._buckets[key].append(now)
            return True


_rate_limiter = _RateLimiter()


def _check_rate_limit(key: str) -> None:
    if not _rate_limiter.check(key):
        raise RuntimeError(
            f"rate limit exceeded for {key!r} (max {_RATE_LIMIT_MAX} calls/{_RATE_LIMIT_WINDOW}s)"
        )


# ---------------------------------------------------------------------------
# FastMCP 伺服器實例
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "RemaGraph",
    instructions=(
        "RemaGraph — leave a trace wherever you go. Records the traces AI coding "
        "agents leave behind while working on tasks."
    ),
)

# ---------------------------------------------------------------------------
# DB 連線管理（lazy init，stdio 模式生命週期短，用 module-level singleton）
#
# BUG 1 修復（PPLX 架構審查共識）：`remagraph serve` 現在必須在啟動時就
# 明確綁定單一 project_id（見下方 _run_serve/_bind_project），並將該次
# connect() 的結果直接存進 _conn/_bound_project_id——_get_conn() 的
# lazy-init 分支因此只在「完全未經過 serve 啟動流程」的情境下才會被觸發
# （例如測試直接呼叫 remagraph_store/search/status 這些 tool handler），
# 此時維持與修復前完全相同的 project_id=None 語意，不影響既有測試。
# ---------------------------------------------------------------------------

_conn: sqlite3.Connection | None = None

_bound_project_id: str | None = None
"""目前這個 serve 行程啟動時綁定的 project_id（由 _bind_project() 設定）。

None 代表尚未經過 serve 啟動流程綁定（例如測試直接呼叫 tool handler）——
此時 _check_project_binding() 一律不視為 mismatch，維持修復前的既有行為。
"""

_bound_db_path: Path | None = None
"""目前這個 _conn 連線實際綁定的 SQLite 資料庫檔案路徑（絕對路徑，已
resolve()），供 _get_conn() 的存活檢查使用（獨立對抗式審查發現的缺口修復，
見 _get_conn docstring）。每次成功建立 _conn（無論是 _bind_project() 的
啟動綁定路徑，或 _get_conn() 自身的 lazy-init 分支）都會同步更新此值；
_safe_close() 關閉連線時一併清空。"""


def _resolve_conn_db_path(conn: sqlite3.Connection) -> Path | None:
    """透過 `PRAGMA database_list` 取得 conn 實際連到的 'main' 資料庫檔案
    絕對路徑（已 resolve()）。

    與 search._own_connection_db_path() 用途相似（兩者都是透過同一個
    PRAGMA 判斷連線實際連到哪個實體檔案），但目的不同：那裡是為了跨專案
    fan-out 的物理別名判斷，這裡是為了記錄 bind 當下的路徑，供之後
    _get_conn() 的存活檢查比對『這個路徑現在是否還存在』。刻意獨立實作
    （而非 import search 模組），避免 server.py 與 search.py 之間新增不
    必要的模組依賴。任何解析失敗（in-memory 連線、PRAGMA 執行失敗、查無
    'main' 資料庫、路徑無法解析）一律回傳 None——呼叫端須將 None 視為
    『無法判斷』，此時 _get_conn() 略過檔案存在性檢查，維持修復前僅靠
    SELECT 1 的行為，不會誤判。
    """
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    try:
        file_path = row["file"]
    except (IndexError, KeyError, TypeError):
        return None
    if not file_path:
        return None
    try:
        return Path(file_path).resolve()
    except (OSError, ValueError):
        return None


def _maybe_warn_default() -> None:
    try:
        if _db.is_using_default_state_dir():
            print(
                "WARNING: default state dir, set REMAGRAPH_PROJECT for isolation", file=sys.stderr
            )
    except Exception:
        pass


def _get_conn() -> sqlite3.Connection:
    """取得 SQLite 連線（lazy init，首次呼叫時建立）。

    BUG 1 修復（PPLX 架構審查共識，item 7 liveness check）：回傳既有連線
    前，先做一次輕量 `SELECT 1` 存活檢查——資料庫檔案若在連線建立後被移除
    或搬移，這裡會提早發現並拋出清楚的錯誤，而不是讓後續某次寫入以難以
    理解的底層 sqlite3 錯誤悄悄失敗，逼使操作者重新啟動 serve 行程。

    獨立對抗式審查發現的缺口：單靠 `SELECT 1` 無法偵測「資料庫檔案所在的
    目錄/檔案本身被整個移除或搬移」這個情境——POSIX 語意下，一個行程已經
    開啟的檔案描述符（file descriptor）在其指向的 inode 被 unlink 之後仍然
    完全有效，對著這個「孤兒 inode」的讀寫會繼續悄悄成功，只是這個 inode
    已經不再能從原本的路徑名稱找到。因此額外在 `SELECT 1` 之外，明確用
    `Path.exists()` 檢查『_bound_db_path 記錄的路徑，現在是否還存在』——
    這是唯二能偵測到此情境的手段之一（另一個是比對 inode number，這裡選擇
    路徑存在性檢查，足以涵蓋『整個目錄被移除/搬移』這個真實重現的事故
    形狀，實作也更簡單直接）。
    """
    global _conn, _bound_db_path
    if _conn is None:
        _maybe_warn_default()
        _conn = _db.connect(project_id=_bound_project_id)
        _bound_db_path = _resolve_conn_db_path(_conn)
        atexit.register(_safe_close)
        return _conn

    try:
        _conn.execute("SELECT 1")
    except Exception as e:
        raise RuntimeError(
            "RemaGraph database connection is no longer valid (the database file "
            "may have been removed or moved). Please restart `remagraph serve`: "
            f"{e}"
        ) from e

    if _bound_db_path is not None and not _bound_db_path.exists():
        raise RuntimeError(
            "RemaGraph database file no longer exists at its original path "
            f"({_bound_db_path}); it may have been removed or moved elsewhere. "
            "This connection may still be operating on an orphaned inode that "
            "was unlinked by the OS (POSIX semantics), which the SELECT 1 "
            "liveness check cannot detect — so reads/writes may silently "
            "\"succeed\" while writing to a file that can no longer be found. "
            "Please restart `remagraph serve`."
        )

    return _conn


def _safe_close() -> None:
    """atexit 安全關閉連線。"""
    global _conn, _bound_db_path
    if _conn is not None:
        _db.close(_conn)
        _conn = None
        _bound_db_path = None


def _check_project_binding(project_id: str | None) -> dict[str, Any] | None:
    """判斷本次工具呼叫的 project_id 是否與目前 serve 行程綁定的
    _bound_project_id 衝突（BUG 1 修復，PPLX 架構審查共識）。

    PPLX 共識明確拒絕在單一 serve 行程內動態切換/快取多個 project 的連線
    （SQLite WAL 鎖定與 checkpoint 在多個長駐連線間互相干擾、eviction
    policy 複雜度、破壞安全閥門『單一 env var 值』的既有假設、stdio
    server 下 dict 快取的執行緒安全疑慮）——因此衝突時一律回傳清楚的
    結構化錯誤，要求呼叫端改為對該 project 另起一個獨立的
    `remagraph serve --project <id>` 行程，而不是嘗試代為切換或悄悄沿用
    目前連線。

    未綁定（_bound_project_id 為 None，例如測試直接呼叫 tool handler、
    未經過 serve 啟動流程）或呼叫端省略 project_id（None，維持既有的
    all_projects/eff_project「使用連線目前預設值」語意，例如
    remagraph_search）時，一律不視為 mismatch。
    """
    if _bound_project_id is None:
        return None
    if project_id is None or project_id == _bound_project_id:
        return None
    return {
        "status": "error",
        "reason": "project_mismatch",
        "detail": (
            f"this remagraph serve process is bound to project "
            f"'{_bound_project_id}' (set at startup via --project or the "
            f"REMAGRAPH_PROJECT environment variable); it cannot serve a "
            f"request for a different project_id ({project_id!r}). Start a "
            f"separate `remagraph serve --project {project_id}` process for "
            "that project instead."
        ),
    }


# ---------------------------------------------------------------------------
# `remagraph serve` 啟動綁定（BUG 1 修復，PPLX 架構審查共識）
# ---------------------------------------------------------------------------


def _determine_serve_project_id(argv: list[str]) -> str | None:
    """從 `remagraph serve` 的參數中解析 --project（支援 `--project X` 與
    `--project=X` 兩種寫法），缺席時退回 REMAGRAPH_PROJECT 環境變數。

    刻意手寫極簡解析而非引入 argparse subparser——serve 目前只需要這一個
    選項，argparse 子命令機制是 cli.py 既有的、以人類互動為主的一般 CLI
    慣例，這裡刻意保持最小、與 MCP host 啟動時傳入的極簡參數列表相稱。
    """
    project_id: str | None = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--project":
            if i + 1 >= len(argv):
                # 末端無值不得靜默 fallback 到 REMAGRAPH_PROJECT——使用者
                # 以為指定了 A，行程實際綁到 env 裡的 B（診斷發現）。
                print(
                    "ERROR: --project requires a value "
                    "(usage: remagraph serve --project <id>)",
                    file=sys.stderr,
                )
                sys.exit(1)
            project_id = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--project="):
            project_id = arg.split("=", 1)[1]
            if not project_id:
                print(
                    "ERROR: --project= requires a non-empty value "
                    "(usage: remagraph serve --project=<id>)",
                    file=sys.stderr,
                )
                sys.exit(1)
            i += 1
            continue
        i += 1
    if project_id:
        return project_id
    return os.environ.get("REMAGRAPH_PROJECT") or None


def _bind_project(project_id: str) -> None:
    """`remagraph serve` 啟動時綁定單一 project_id。

    刻意在啟動當下、MCP stdio 迴圈開始接受工具呼叫之前就呼叫
    _db.connect(project_id=project_id)——這既建立本行程唯一、長駐的連線，
    也立即觸發 maintenance.safety_validate_project 的強制驗證：env var
    不符、缺漏，或任何版本相容性問題都會在啟動當下就浮現並讓行程快速失敗，
    而不是被埋在之後某次工具呼叫裡、變成令人困惑的失敗（真實事故正是
    因為這個驗證從未在 serve 路徑上被觸發過）。

    啟動診斷（一律印到 stderr）：目前綁定哪一個 project_id、解析出的
    state_dir 是什麼，讓操作者能立即看到一個正在跑的 serve 行程實際連到
    哪裡，不必等到之後才從 audit log 察覺。若 REMAGRAPH_STATE_DIR 剛好已
    設定成與此 project_id 解析結果不同的目錄（正是造成真實事故的 env var
    繼承情境），額外印出明確警告——safety_validate_project 隨後仍會
    raise/拒絕，這裡只是搶先說明『為什麼』，在例外往外傳之前先讓操作者
    看到（見 maintenance.resolve_project_state_dir 目前的 env-優先解析
    行為：REMAGRAPH_STATE_DIR 一旦設定，該函式一律直接回傳其值本身，因此
    這個分歧目前只在防禦性場景/未來實作調整下才可能出現，仍保留此檢查
    做為未來的保護網）。

    若連線在唯讀降級模式下成功建立（見 db.READ_ONLY_ATTR，PPLX 共識
    edge case 4a），印出明確的啟動警告；remagraph_store 本身也會主動檢查
    同一個標記並提早拒絕寫入（見該函式）。
    """
    global _conn, _bound_project_id, _bound_db_path

    try:
        # register=False：這裡只是啟動診斷的解析，發生在下方 _db.connect()
        # 觸發 safety_validate_project 之前——絕不能在驗證前就把
        # (project_id, env 繼承來的目錄) upsert 進 registry（對抗式審查
        # 實測發現的繞過：serve 行程繼承別的專案的 REMAGRAPH_STATE_DIR
        # 啟動，安全閥正確拒絕、行程失敗，但 registry 已被污染，後續
        # cross-project fan-out 會照著開錯專案的資料庫）。合法登記由
        # safety_validate_project 在全部驗證通過後執行。
        resolved_state_dir = maintenance.resolve_project_state_dir(
            project_id, register=False
        )
    except Exception:
        resolved_state_dir = None

    if resolved_state_dir is not None:
        print(
            f"[remagraph serve] bound to project '{project_id}', "
            f"state_dir={resolved_state_dir}",
            file=sys.stderr,
        )
        env_state_dir = os.environ.get("REMAGRAPH_STATE_DIR")
        if env_state_dir:
            try:
                env_resolved: Path | None = Path(env_state_dir).resolve()
            except OSError:
                env_resolved = None
            if env_resolved is not None and env_resolved != resolved_state_dir:
                print(
                    "[remagraph serve] WARNING: REMAGRAPH_STATE_DIR is "
                    f"currently set to {env_resolved}, which does NOT match "
                    f"the resolved state_dir for project '{project_id}' "
                    f"({resolved_state_dir}). This is exactly the "
                    "env-var-inheritance scenario that can cause a serve "
                    "process to silently bind to the wrong project's "
                    "database. The connection attempt below will likely be "
                    "rejected by the safety valve.",
                    file=sys.stderr,
                )

    conn = _db.connect(project_id=project_id)  # 觸發 safety_validate_project

    if getattr(conn, _db.READ_ONLY_ATTR, False):
        print(
            "[remagraph serve] WARNING: this serve process starts in "
            "read-only mode (database schema is newer than this code's "
            "write-compatibility version); store operations will be "
            "rejected until the remagraph package is upgraded.",
            file=sys.stderr,
        )

    _conn = conn
    _bound_project_id = project_id
    _bound_db_path = _resolve_conn_db_path(conn)
    atexit.register(_safe_close)


def _run_serve(argv: list[str]) -> None:
    """`remagraph serve` 的實際啟動邏輯：解析 --project/REMAGRAPH_PROJECT、
    缺席時快速失敗（不啟動 MCP stdio 迴圈），否則綁定後才進入
    `mcp.run(transport="stdio")`。
    """
    if any(a in ("-h", "--help") for a in argv):
        # help 委派給 cli.py 的 argparse serve subparser（單一說明來源）；
        # argparse 會印出說明並 sys.exit(0)，絕不能走到下方的 project
        # 綁定與 MCP stdio 啟動。
        from remagraph.cli import main as cli_main

        cli_main(["serve", "--help"])
        return

    project_id = _determine_serve_project_id(argv)
    if not project_id:
        print(
            "ERROR: remagraph serve requires an explicit project binding — "
            "provide --project <id> or set the REMAGRAPH_PROJECT environment "
            "variable. This is intentional (per PPLX architecture review "
            "consensus): each serve process binds to exactly one project, to "
            "avoid the SQLite locking / connection-cache complexity and "
            "safety-valve bypass risk that dynamic multi-project routing "
            "within a single process would introduce. Start a separate "
            "`remagraph serve --project <id>` process per project.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        _bind_project(project_id)
    except Exception as e:
        print(f"ERROR: remagraph serve failed to start - {e}", file=sys.stderr)
        sys.exit(1)

    mcp.run(transport="stdio")


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="remagraph_store",
    description=(
        "Store a memory. Written to SQLite + FTS5 index after passing five "
        "arbitration rules. Supports fleet_member (record/recycle owned by "
        "the tower)."
    ),
)
def remagraph_store(
    project_id: str,
    task_id: str,
    agent_id: str,
    kind: str,
    summary: str,
    learnings: list[str],
    handoff_note: str = "",
    tags: list[str] | None = None,
    invalidates: list[str] | None = None,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    """agent 寫入記憶。

    labels（PPLX 架構改善計畫 item 4b）：與 tags 是兩個獨立的概念，刻意不
    合併——tags 是既有的自由格式欄位（無格式要求，供既有的 tag 過濾搜尋
    使用，見 search.py 的 kind/status/tags/agent_id/task_id 過濾），改動
    tags 的語意會牽動既有呼叫端；labels 則是新增的、有明確 namespace:value
    格式要求的受控詞彙（見 arbitration.validate_labels），專供 item 4b 的
    跨專案標籤搜尋使用（remagraph_search 的 cross_project_label 參數）。
    """
    _check_rate_limit(agent_id)
    mismatch = _check_project_binding(project_id)
    if mismatch is not None:
        return mismatch
    try:
        conn = _get_conn()
    except Exception as e:
        return {"status": "error", "reason": str(e)}
    # BUG 1 修復 item 6（PPLX 架構審查共識）：主動、及早檢查唯讀降級標記，
    # 回傳與既有 store.process_store() 唯讀拒絕慣例一致的結構
    # （status="rejected", reason="read_only_mode"）。process_store()
    # 本身也會在最前面做同一個檢查（defense-in-depth）；這裡提早攔截讓
    # serve 啟動時就已知處於唯讀模式的情境，不必先建構 StoreRequest 或
    # 進入 process_store 才發現。
    if getattr(conn, _db.READ_ONLY_ATTR, False):
        detail = getattr(
            conn,
            _db.READ_ONLY_DETAIL_ATTR,
            "This connection is currently in read-only mode (the database "
            "schema has been upgraded beyond this code's write-compatible "
            "version); this write has been rejected. Please upgrade the "
            "remagraph package and retry.",
        )
        return {"status": "rejected", "reason": "read_only_mode", "detail": detail}
    request = StoreRequest(
        project_id=project_id,
        task_id=task_id,
        agent_id=agent_id,
        kind=kind,  # type: ignore[arg-type]
        summary=summary,
        learnings=learnings,
        handoff_note=handoff_note,
        tags=tags or [],
        invalidates=invalidates,
        labels=labels or [],
    )
    response = process_store(request, conn)
    result: dict[str, Any] = {
        "status": response.status,
        "superseded": response.superseded,
        "invalidated_count": response.invalidated_count,
    }
    if response.id:
        result["id"] = response.id
    if response.reason:
        result["reason"] = response.reason
    if response.detail:
        result["detail"] = response.detail
    return result


@mcp.tool(
    name="remagraph_search",
    description=(
        "Search memories. FTS5 BM25 full-text search (trigram tokenizer, "
        "CJK-capable) plus tag/kind/agent_id/task_id filters. Short queries "
        "(<=2 characters) return an empty result instead of raising an error."
    ),
)
def remagraph_search(
    query: str,
    top_k: int = 20,
    kind: str | None = None,
    status: str | None = None,
    tags: list[str] | None = None,
    project_id: str | None = None,
    agent_id: str | None = None,
    task_id: str | None = None,
    all_projects: bool = False,
    cross_project_label: str | None = None,
    include_related: bool = False,
    related_hops: int = 1,
) -> dict[str, Any]:
    """agent 查詢記憶（FTS5 BM25）。

    cross_project_label（PPLX 架構改善計畫 item 4b）：與既有的 all_projects
    是完全獨立的兩個維度，刻意不合併——all_projects 只移除『目前這個資料庫
    檔案內』的 project_id 過濾（每個 project 各自是獨立的 SQLite 檔案，
    all_projects 從不開啟其他檔案）；cross_project_label 則會透過 item 4a
    的 registry（db.list_known_projects/connect_foreign_project_readonly）
    真正開啟其他 project 各自獨立的資料庫檔案，查詢各自的 memory_labels
    表並合併結果（詳見 search._search_cross_project_by_label）。提供
    cross_project_label 時，其餘全文檢索/過濾參數（query/kind/tags/...）
    不適用，只依 label 精確比對。

    include_related（PPLX 架構改善計畫 item 5）：與 cross_project_label /
    all_projects 是第三個完全獨立的維度——只 fan out 到透過
    db.recall_related()（project_edges traversal，需先以 `remagraph link`
    宣告關聯）在 related_hops 之內找到的『明確關聯』專案，且查詢方式是
    正常的 FTS query（非 label 精確比對），詳見
    search._search_related_projects。需要 project_id 作為 traversal
    起點；project_id 為 None 時優雅退化為一般搜尋，不展開 related
    fan-out（見 search.search_memories 的分派邏輯）。
    """
    _check_rate_limit(agent_id or "anonymous")
    mismatch = _check_project_binding(project_id)
    if mismatch is not None:
        return mismatch
    try:
        conn = _get_conn()
    except Exception as e:
        return {"status": "error", "reason": str(e)}
    eff_project = None if all_projects else project_id
    request = SearchRequest(
        query=query,
        top_k=top_k,
        kind=kind,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        tags=tags,
        project_id=eff_project,
        agent_id=agent_id,
        task_id=task_id,
        cross_project_label=cross_project_label,
        include_related=include_related,
        related_hops=related_hops,
    )
    response = search_memories(conn, request)
    return {
        "results": response.results,
        "has_more": response.has_more,
        "cross_project_fanout_capped": response.cross_project_fanout_capped,
        "candidates_total": response.candidates_total,
        "candidates_searched": response.candidates_searched,
        "candidates_skipped": response.candidates_skipped,
    }


@mcp.tool(
    name="remagraph_status",
    description=(
        "Query the latest status (scoped to a project by default). Also "
        "returns version-compatibility handshake info "
        "(server_code_version/db_schema_version/min_reader_version/"
        "min_writer_version/upgrade_hint/read_only) so the caller can detect a "
        "version gap early, without waiting for a write to fail."
    ),
)
def remagraph_status(
    project_id: str | None = None, limit: int = 20, all_projects: bool = False
) -> dict[str, Any]:
    """查詢所有 active status_update（依 task_id 去重取最新），並附上版本相容性
    handshake 資訊（見 db.get_compat_status）。"""
    _check_rate_limit("status")
    mismatch = _check_project_binding(project_id)
    if mismatch is not None:
        return mismatch
    try:
        conn = _get_conn()
    except Exception as e:
        return {"status": "error", "reason": str(e)}
    eff_project = None if all_projects else project_id
    request = StatusRequest(limit=limit, project_id=eff_project)
    response = get_status(conn, request)
    result: dict[str, Any] = {"latest": response.latest}
    result.update(_db.get_compat_status(conn))
    return result


@mcp.tool(
    name="remagraph_maintain",
    description=(
        "Run automatic DB maintenance (WAL/FTS/prune/vacuum/integrity). "
        "project_id is required."
    ),
)
def remagraph_maintain(
    project_id: str,
    force: bool = False,
) -> dict[str, Any]:
    """自動維護 DB。"""
    _check_rate_limit("maintenance")
    # 與 remagraph_store/search/status 相同的 project binding 檢查（診斷
    # 修復）：綁定 project A 的 serve 行程不得以 B 的名義執行 prune/vacuum
    # 這類有資料影響的維護——單行程單專案是 PPLX 共識的核心約束。
    mismatch = _check_project_binding(project_id)
    if mismatch is not None:
        return mismatch
    try:
        safety_validate_project(project_id)
        policy = MaintenancePolicy()
        stats = run_maintenance(policy, project_id, force=force)
        return {"status": "ok", "stats": stats}
    except Exception as e:
        return {"status": "error", "reason": str(e)}


@mcp.tool(
    name="remagraph_migrate_project",
    description=(
        "Migrate memories from a source project to a target project's "
        "separate DB, and mark them invalidated in the source. For one-time "
        "migrations only (e.g. default -> a dedicated per-project DB). Performs a real "
        "migration (not a stub): reads from_project's registered state_dir "
        "(resolved via the shared project registry), heuristically matches "
        "records that look like they belong to to_project, copies them into "
        "to_project's own DB, and marks the originals invalidated in the "
        "source. dry_run=True reports the exact count that would be "
        "migrated (using the same match query as a real run) without "
        "writing anything."
    ),
)
def remagraph_migrate_project(
    from_project: str,
    to_project: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """project 記憶遷移工具。

    真正的遷移邏輯（來源/目標 state_dir 解析、啟發式比對、逐筆搬移、唯讀
    降級檢查）全部在 store.migrate_project_memories() 裡，供本 tool 與 CLI
    的 cmd_migrate_project 共用，這裡只負責把結構化結果轉成 MCP tool 該有
    的 dict 回應。
    """
    _check_rate_limit("migrate")
    try:
        result = migrate_project_memories(from_project, to_project, dry_run=dry_run)
        return {
            "status": "dry-run" if result.dry_run else "ok",
            "from": result.from_project,
            "to": result.to_project,
            "dry_run": result.dry_run,
            "migrated_count": result.migrated_count,
            "skipped_ids": result.skipped_ids,
        }
    except Exception as e:
        return {"status": "error", "reason": str(e)}


# ---------------------------------------------------------------------------
# 程式入口
# ---------------------------------------------------------------------------


def main() -> None:
    """程式入口：自動判斷 CLI 或 MCP 模式。

    分派規則（診斷後收斂；歷史 serve fallback 只保留兩種明確形狀）：

    - bare `remagraph`（無任何參數）→ serve 模式（歷史行為：MCP host 常以
      bare 呼叫啟動 stdio server），套用 _run_serve 的啟動綁定要求。
    - `remagraph --project <id>` / `--project=<id>` 開頭（無 serve 子命令）
      → 同上 serve fallback（MCP host 的另一種歷史設定寫法）。
    - `remagraph serve [...]` → MCP stdio server（BUG 1 修復：啟動時綁定
      單一 project，缺席時快速失敗，見 _run_serve/_bind_project）。
    - 其餘一切（已知 CLI 子命令、-h/--help/--version、頂層旗標前置寫法、
      以及打錯的子命令）→ 一律交給 cli.py 的 argparse parser：已知子命令
      正常執行，help/version 印出後 exit 0，typo 得到 invalid choice 的
      exit 2——修復前 typo 或 --version 會靜默落入 serve fallback，在
      REMAGRAPH_PROJECT 已設定的環境下甚至直接啟動 stdio server 掛住
      終端機（診斷發現的同族缺口）。
    """
    argv = sys.argv[1:]

    if not argv:
        _run_serve([])
        return

    if argv[0] == "serve":
        _run_serve(argv[1:])
        return

    if argv[0] == "--project" or argv[0].startswith("--project="):
        # 歷史 serve fallback 只涵蓋「純 serve 參數」的形狀；若後面還帶著
        # CLI 子命令（如 `remagraph --project X store ...`，比 typo 更自然
        # 的誤用寫法），靜默綁定 X 啟動 stdio server 掛住終端正是本輪修復
        # 要消滅的失敗模式（對抗式審查發現的殘留入口）——改為明確報錯。
        cli_commands = (
            "store", "search", "status", "init", "auto", "maintain",
            "migrate-project", "link", "install-hooks", "prompt-hook", "doctor", "serve",
        )
        stray = next((a for a in argv[1:] if a in cli_commands), None)
        if stray is not None:
            print(
                f"ERROR: the subcommand {stray!r} must come before --project "
                f"(usage: remagraph {stray} --project <id> ...); a leading "
                "--project without a subcommand starts the MCP stdio server.",
                file=sys.stderr,
            )
            sys.exit(2)
        _run_serve(argv)
        return

    from remagraph.cli import main as cli_main

    cli_main(argv)


if __name__ == "__main__":
    main()
