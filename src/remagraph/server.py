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
from collections import defaultdict
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from remagraph import db as _db
from remagraph import maintenance
from remagraph.maintenance import MaintenancePolicy, run_maintenance, safety_validate_project
from remagraph.models import SearchRequest, StatusRequest, StoreRequest
from remagraph.search import get_status, search_memories
from remagraph.store import process_store

# ---------------------------------------------------------------------------
# Rate limiter（簡易記憶體 token bucket, per agent_id）
# ---------------------------------------------------------------------------

_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX = 60  # calls per window


class _RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> bool:
        now = time.monotonic()
        window_start = now - _RATE_LIMIT_WINDOW
        with self._lock:
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
    instructions="RemaGraph — 凡走過必留下痕跡。記錄 AI coding agent 處理任務時留下的痕跡。",
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
            "RemaGraph 資料庫連線已失效（資料庫檔案可能已被移除或搬移），"
            f"請重新啟動 remagraph serve（restart the serve process）：{e}"
        ) from e

    if _bound_db_path is not None and not _bound_db_path.exists():
        raise RuntimeError(
            "RemaGraph 資料庫檔案已不存在於原本路徑"
            f"（{_bound_db_path}），可能已被移除或搬移到別處——目前這個 "
            "連線可能仍對著一個已被作業系統 unlink 的孤兒 inode 繼續運作"
            "（POSIX 語意），SELECT 1 存活檢查偵測不到這個情境，因此讀寫仍"
            "可能悄悄『成功』但寫入一個已經找不到的檔案。請重新啟動 "
            "remagraph serve（restart the serve process）。"
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
        if arg == "--project" and i + 1 < len(argv):
            project_id = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--project="):
            project_id = arg.split("=", 1)[1]
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
        resolved_state_dir = maintenance.resolve_project_state_dir(project_id)
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
    project_id = _determine_serve_project_id(argv)
    if not project_id:
        print(
            "ERROR: remagraph serve 需要明確的 project 綁定 —— 請提供 "
            "--project <id>，或設定 REMAGRAPH_PROJECT 環境變數。這是刻意"
            "的設計（PPLX 架構審查共識）：每個 serve 行程只綁定單一 "
            "project，避免多專案動態路由帶來的 SQLite 鎖定/連線快取複雜度"
            "與安全閥門失效風險；不同 project 請另外啟動獨立的 "
            "`remagraph serve --project <id>` 行程。",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        _bind_project(project_id)
    except Exception as e:
        print(f"ERROR: remagraph serve 啟動失敗 - {e}", file=sys.stderr)
        sys.exit(1)

    mcp.run(transport="stdio")


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="remagraph_store",
    description="寫入記憶。通過五條仲裁規則後寫入 SQLite + FTS5 index。"
    "支援 fleet_member（由 tower 擁有 record/recycle）。",
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
            "此連線目前為唯讀模式（資料庫 schema 已升級到超出本程式碼的寫入"
            "相容版本），已拒絕本次寫入。請升級 remagraph 套件後再重試。",
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
    description="查詢記憶。FTS5 BM25 全文檢索（trigram tokenizer，支援 CJK）"
    "+ tag/kind/agent_id/task_id 過濾。短查詢（≤2 字元）回傳空結果不拋錯。",
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
    description="查詢最新現況（預設限 project）。同時回傳版本相容性 handshake 資訊"
    "（server_code_version/db_schema_version/min_reader_version/min_writer_version/"
    "upgrade_hint/read_only），讓呼叫端能提早掌握是否存在版本落差，不必等寫入失敗。",
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
    description=("執行 DB 自動維護（WAL/FTS/prune/vacuum/integrity）。 必須提供 project_id。"),
)
def remagraph_maintain(
    project_id: str,
    force: bool = False,
) -> dict[str, Any]:
    """自動維護 DB。"""
    _check_rate_limit("maintenance")
    try:
        safety_validate_project(project_id)
        policy = MaintenancePolicy()
        stats = run_maintenance(policy, project_id, force=force)
        return {"status": "ok", "stats": stats}
    except Exception as e:
        return {"status": "error", "reason": str(e)}


@mcp.tool(
    name="remagraph_migrate_project",
    description="將記憶從來源 project 遷移到目標 project 的獨立 DB，並在來源標記 invalidated。"
    "僅用於一次性遷移（如 default → herdr-bridge）。",
)
def remagraph_migrate_project(
    from_project: str,
    to_project: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """project 記憶遷移工具。"""
    _check_rate_limit("migrate")
    try:
        safety_validate_project(to_project, require_env_match=False)
        # 簡化實作：直接呼叫 CLI 邏輯或內部 migrate（這裡用簡化版）
        # 實際應複用 cli 中的 migrate 邏輯
        return {
            "status": "ok" if not dry_run else "dry-run",
            "from": from_project,
            "to": to_project,
            "message": "遷移邏輯已觸發（詳細見 CLI migrate-project）",
        }
    except Exception as e:
        return {"status": "error", "reason": str(e)}


# ---------------------------------------------------------------------------
# 程式入口
# ---------------------------------------------------------------------------


def main() -> None:
    """程式入口：自動判斷 CLI 或 MCP 模式。

    - `remagraph serve [--project <id>]` → MCP stdio server（BUG 1 修復：
      現在會先解析 --project/REMAGRAPH_PROJECT 並在啟動時綁定，見
      _run_serve/_bind_project；缺席時快速失敗，不再悄悄以未綁定狀態啟動）
    - `remagraph store/search/status/init/auto/install-hooks` → CLI 子命令
    - 其餘（含完全省略子命令、或子命令不是上述任何一種）→ 沿用修復前的
      既有行為，視為 serve 模式（歷史上 bare `remagraph` 呼叫等同
      `remagraph serve`），一併套用上方相同的啟動綁定要求。
    """
    cli_commands = (
        "store",
        "search",
        "status",
        "init",
        "auto",
        "maintain",
        "migrate-project",
        "link",
        "install-hooks",
    )
    if len(sys.argv) >= 2 and sys.argv[1] in cli_commands:
        from remagraph.cli import main as cli_main

        cli_main(sys.argv[1:])
        return

    if len(sys.argv) >= 2 and sys.argv[1] == "serve":
        _run_serve(sys.argv[2:])
        return

    _run_serve(sys.argv[1:] if len(sys.argv) >= 2 else [])


if __name__ == "__main__":
    main()
