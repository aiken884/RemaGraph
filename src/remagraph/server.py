# SPDX-License-Identifier: Apache-2.0
"""MCP server entrypoint (stdio transport) — v1 以 stdio 為主要傳輸模式。

透過 FastMCP (mcp SDK) 註冊三個 tool：
- remagraph_store：寫入記憶（仲裁 → dedup → 寫入 SQLite + FTS5）
- remagraph_search：FTS5 BM25 全文檢索
- remagraph_status：查詢專案最新現況（active status_update 依 task_id 去重）

使用方式：
    remagraph serve          # stdio 模式（預設）
    REMAGRAPH_STATE_DIR=/tmp remagraph serve  # 自訂 state 目錄
"""

from __future__ import annotations

import atexit
import sqlite3
from typing import Any

from mcp.server.fastmcp import FastMCP

from remagraph import db as _db
from remagraph.models import SearchRequest, StatusRequest, StoreRequest
from remagraph.search import get_status, search_memories
from remagraph.store import process_store

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


def _get_conn() -> sqlite3.Connection:
    """取得 SQLite 連線（lazy init，首次呼叫時建立）。"""
    global _conn
    if _conn is None:
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
    "三種 kind：task_handoff（任務交接）、status_update（狀態更新，同 task_id 自動 supersede）、"
    "discovered_constraint（發現的限制，可 invalidate 既有記憶）。",
)
def remagraph_store(
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
    request = StoreRequest(
        task_id=task_id,
        agent_id=agent_id,
        kind=kind,  # type: ignore[arg-type]
        summary=summary,
        learnings=learnings,
        handoff_note=handoff_note,
        tags=tags or [],
        invalidates=invalidates,
    )
    response = process_store(request, _get_conn())
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
    agent_id: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """agent 查詢記憶（FTS5 BM25）。"""
    request = SearchRequest(
        query=query,
        top_k=top_k,
        kind=kind,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        tags=tags,
        agent_id=agent_id,
        task_id=task_id,
    )
    response = search_memories(_get_conn(), request)
    return {"results": response.results, "has_more": response.has_more}


@mcp.tool(
    name="remagraph_status",
    description="查詢專案最新現況。回傳所有 active 的 status_update 型記憶，"
    "以 task_id 去重（只留每 task_id 最新一筆）。limit 預設 20，最大 100。",
)
def remagraph_status(limit: int = 20) -> dict[str, Any]:
    """查詢所有 active status_update（依 task_id 去重取最新）。"""
    request = StatusRequest(limit=limit)
    response = get_status(_get_conn(), request)
    return {"latest": response.latest}


# ---------------------------------------------------------------------------
# 程式入口
# ---------------------------------------------------------------------------


def main() -> None:
    """程式入口：以 stdio transport 啟動 MCP server。"""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
