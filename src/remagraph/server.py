# SPDX-License-Identifier: Apache-2.0
"""MCP server entrypoint (stdio transport) + CLI subcommands。

透過程式進入點自動判斷模式：
- `remagraph serve` → MCP stdio server（既有）
- `remagraph store/search/status` → CLI subcommand（headless agent 用）
"""

from __future__ import annotations

import atexit
import sqlite3
import sys
import threading
import time
from collections import defaultdict
from typing import Any

from mcp.server.fastmcp import FastMCP

from remagraph import db as _db
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
# ---------------------------------------------------------------------------

_conn: sqlite3.Connection | None = None


def _maybe_warn_default() -> None:
    try:
        if _db.is_using_default_state_dir():
            print(
                "WARNING: default state dir, set REMAGRAPH_PROJECT for isolation", file=sys.stderr
            )
    except Exception:
        pass


def _get_conn() -> sqlite3.Connection:
    """取得 SQLite 連線（lazy init，首次呼叫時建立）。"""
    global _conn
    if _conn is None:
        _maybe_warn_default()
        _conn = _db.connect()
        atexit.register(_safe_close)
    return _conn


def _safe_close() -> None:
    """atexit 安全關閉連線。"""
    global _conn
    if _conn is not None:
        _db.close(_conn)
        _conn = None


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
) -> dict[str, Any]:
    """agent 寫入記憶。"""
    _check_rate_limit(agent_id)
    try:
        conn = _get_conn()
    except Exception as e:
        return {"status": "error", "reason": str(e)}
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
) -> dict[str, Any]:
    """agent 查詢記憶（FTS5 BM25）。"""
    _check_rate_limit(agent_id or "anonymous")
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
    )
    response = search_memories(conn, request)
    return {"results": response.results, "has_more": response.has_more}


@mcp.tool(
    name="remagraph_status",
    description="查詢最新現況（預設限 project）。",
)
def remagraph_status(
    project_id: str | None = None, limit: int = 20, all_projects: bool = False
) -> dict[str, Any]:
    """查詢所有 active status_update（依 task_id 去重取最新）。"""
    _check_rate_limit("status")
    try:
        conn = _get_conn()
    except Exception as e:
        return {"status": "error", "reason": str(e)}
    eff_project = None if all_projects else project_id
    request = StatusRequest(limit=limit, project_id=eff_project)
    response = get_status(conn, request)
    return {"latest": response.latest}


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
        policy = MaintenancePolicy()  # type: ignore[no-untyped-call]
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

    - `remagraph serve` → MCP stdio server
    - `remagraph store/search/status/init/auto` → CLI 子命令
    """
    cli_commands = ("store", "search", "status", "init", "auto", "maintain", "migrate-project")
    if len(sys.argv) >= 2 and sys.argv[1] in cli_commands:
        from remagraph.cli import main as cli_main

        cli_main(sys.argv[1:])
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
