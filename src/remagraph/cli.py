# SPDX-License-Identifier: Apache-2.0
"""CLI subcommands for headless agent integration (herdr bridge).

Usage:
    remagraph store --task-id STR --agent-id STR --kind STR --summary STR [options]
    remagraph search --query STR [options]
    remagraph status [options]
"""

from __future__ import annotations

import argparse
import atexit
import json
import sqlite3
import sys
from typing import Any

from remagraph import db as _db
from remagraph.models import SearchRequest, StatusRequest, StoreRequest
from remagraph.search import get_status, search_memories
from remagraph.store import process_store

# ---------------------------------------------------------------------------
# DB 連線管理（CLI 專用，每次命令獨立連線）
# ---------------------------------------------------------------------------


def _get_conn() -> sqlite3.Connection:
    conn = _db.connect()
    atexit.register(_db.close, conn)
    return conn


# ---------------------------------------------------------------------------
# JSON 參數輔助
# ---------------------------------------------------------------------------


def _parse_json_list(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise ValueError
        return parsed
    except (json.JSONDecodeError, ValueError):
        print("error: --tags/--learnings 必須是 JSON 陣列，"
              "例如 '\"[\\\"a\\\",\\\"b\\\"]\"'", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Subcommand: store
# ---------------------------------------------------------------------------


def cmd_store(args: argparse.Namespace) -> None:
    request = StoreRequest(
        task_id=args.task_id,
        agent_id=args.agent_id,
        kind=args.kind,
        summary=args.summary,
        learnings=_parse_json_list(args.learnings) or [],
        handoff_note=args.handoff_note,
        tags=_parse_json_list(args.tags) or [],
        invalidates=_parse_json_list(args.invalidates),
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
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


# ---------------------------------------------------------------------------
# Subcommand: search
# ---------------------------------------------------------------------------


def cmd_search(args: argparse.Namespace) -> None:
    request = SearchRequest(
        query=args.query,
        top_k=args.top_k,
        kind=args.kind,
        status=args.status,
        tags=_parse_json_list(args.tags),
        agent_id=args.agent_id,
        task_id=args.task_id,
    )
    response = search_memories(_get_conn(), request)
    json.dump(
        {"results": response.results, "has_more": response.has_more},
        sys.stdout,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")


# ---------------------------------------------------------------------------
# Subcommand: status
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> None:
    request = StatusRequest(limit=args.limit)
    response = get_status(_get_conn(), request)
    json.dump({"latest": response.latest}, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="remagraph", description="RemaGraph — AI agent 記憶工具")
    sub = parser.add_subparsers(dest="command", required=True)

    # store
    p_store = sub.add_parser("store", help="寫入記憶")
    p_store.add_argument("--task-id", required=True)
    p_store.add_argument("--agent-id", required=True)
    p_store.add_argument(
        "--kind", required=True,
        choices=["task_handoff", "status_update", "discovered_constraint"],
    )
    p_store.add_argument("--summary", required=True)
    p_store.add_argument("--learnings", help='JSON 陣列，例如 \'["a","b"]\'')
    p_store.add_argument("--handoff-note", default="")
    p_store.add_argument("--tags", help='JSON 陣列')
    p_store.add_argument("--invalidates", help='JSON 陣列')

    # search
    p_search = sub.add_parser("search", help="查詢記憶")
    p_search.add_argument("--query", required=True)
    p_search.add_argument("--top-k", type=int, default=20)
    p_search.add_argument(
        "--kind", choices=["task_handoff", "status_update", "discovered_constraint"],
    )
    p_search.add_argument("--status", choices=["active", "superseded", "invalidated"])
    p_search.add_argument("--tags", help='JSON 陣列')
    p_search.add_argument("--agent-id")
    p_search.add_argument("--task-id")

    # status
    p_status = sub.add_parser("status", help="查詢最新現況")
    p_status.add_argument("--limit", type=int, default=20)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "store":
        cmd_store(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "status":
        cmd_status(args)


if __name__ == "__main__":
    main()
