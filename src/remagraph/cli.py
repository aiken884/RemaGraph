# SPDX-License-Identifier: Apache-2.0
"""CLI subcommands for headless agent integration (herdr bridge).

Usage:
    remagraph init [--project NAME]
    remagraph store --task-id STR --agent-id STR --kind STR --summary STR [options]
    remagraph search [--query STR] [--task-id STR] [options]
    remagraph status [options]
    remagraph auto --task-id STR --agent-id STR [--] CMD [ARGS...]
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
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
        print(
            "error: --tags/--learnings 必須是 JSON 陣列，例如 '[\"a\",\"b\"]'",
            file=sys.stderr,
        )
        sys.exit(1)


def _print_json(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


def _pad_summary(text: str, min_len: int = 30) -> str:
    """確保 summary 達仲裁下限（≥30 字元）。"""
    text = text.strip()
    if len(text) >= min_len:
        return text
    pad = "（自動補足長度以符合 RemaGraph 記錄規則）"
    return (text + pad)[: max(min_len, len(text) + len(pad))]


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
    _print_json(result)


# ---------------------------------------------------------------------------
# Subcommand: search
# ---------------------------------------------------------------------------


def cmd_search(args: argparse.Namespace) -> None:
    if not args.query and not args.task_id and not args.agent_id:
        print(
            "error: 請提供 --query，或至少提供 --task-id / --agent-id",
            file=sys.stderr,
        )
        sys.exit(1)
    request = SearchRequest(
        query=args.query or "",
        top_k=args.top_k,
        kind=args.kind,
        status=args.status,
        tags=_parse_json_list(args.tags),
        agent_id=args.agent_id,
        task_id=args.task_id,
    )
    response = search_memories(_get_conn(), request)
    _print_json({"results": response.results, "has_more": response.has_more})


# ---------------------------------------------------------------------------
# Subcommand: status
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> None:
    request = StatusRequest(limit=args.limit)
    response = get_status(_get_conn(), request)
    _print_json({"latest": response.latest})


# ---------------------------------------------------------------------------
# Subcommand: init
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> None:
    """極簡初始化 - 為非技術使用者設計，一行指令即可。"""
    project = args.project or "default"
    # 僅允許安全字元，避免路徑注入
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in project)
    if not safe:
        safe = "default"
    state_dir = Path.home() / ".local" / "state" / f"remagraph-{safe}"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_dir.chmod(0o700)

    # 寫入簡單的 env 提示檔，方便 source
    env_file = state_dir / "env.sh"
    env_file.write_text(
        f"# 由 remagraph init 產生 — source 此檔即可設定\n"
        f'export REMAGRAPH_STATE_DIR="{state_dir}"\n',
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    print("✅ RemaGraph 初始化完成！")
    print(f"記憶資料夾：{state_dir}")
    print("")
    print("【最簡單：三步驟】")
    print(f"  1. source {env_file}")
    print("  2. 下載包裝腳本（若尚未下載）：")
    print("     curl -O https://raw.githubusercontent.com/aiken884/RemaGraph/main/examples/simple/remagraph-task.sh")
    print("     chmod +x remagraph-task.sh")
    print("  3. 執行任務：")
    print('     TASK_ID=task-001 AGENT_ID=my-ai ./remagraph-task.sh echo "hello"')
    print("")
    print("或用內建一鍵指令（不需下載腳本）：")
    print('  remagraph auto --task-id task-001 --agent-id my-ai -- echo "hello"')
    print("")
    print("注意：task_id / agent_id 只能用英文、數字、底線、連字號（例如 fix-login-001）")
    print("")
    print("之後可直接：")
    print(f"  export REMAGRAPH_STATE_DIR={state_dir}")


# ---------------------------------------------------------------------------
# Subcommand: auto
# ---------------------------------------------------------------------------


def cmd_auto(args: argparse.Namespace) -> None:
    """一鍵：先 recall，可選執行外部指令，最後自動 store。"""
    task_id = args.task_id
    agent_id = args.agent_id
    top_k = args.top_k
    quiet = args.quiet
    cmd = list(args.cmd or [])

    def _log(msg: str) -> None:
        if not quiet:
            print(msg, file=sys.stderr)

    _log("=== RemaGraph auto 開始 ===")
    _log(f"任務：{task_id} / 執行者：{agent_id}")

    # 1. recall
    request = SearchRequest(query="", top_k=top_k, task_id=task_id)
    try:
        response = search_memories(_get_conn(), request)
        memories = response.results
    except Exception as exc:  # noqa: BLE001 — 記憶失敗不阻斷主任務
        _log(f"（讀取記憶失敗：{exc}，繼續）")
        memories = []

    if not quiet:
        if memories:
            _log(f">>> 找到 {len(memories)} 筆之前記憶：")
            for m in memories:
                _log(f"  - {m.get('summary', '')[:80]}")
        else:
            _log(">>> 目前沒有之前記憶")

    # 2. 執行外部指令（可選）
    exit_code = 0
    if cmd:
        _log(f">>> 執行：{' '.join(cmd)}")
        try:
            completed = subprocess.run(cmd, check=False)
            exit_code = int(completed.returncode)
        except FileNotFoundError:
            _log(f"error: 找不到指令 {cmd[0]!r}")
            sys.exit(127)
        except OSError as exc:
            _log(f"error: 無法執行指令：{exc}")
            sys.exit(126)
    else:
        _log(">>> 未提供外部指令，僅做 recall + store")

    # 3. 自動 store
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if args.summary:
        summary = args.summary
    elif cmd:
        summary = (
            f"auto 完成指令「{' '.join(cmd)}」，退出碼={exit_code}，時間={ts}"
        )
    else:
        summary = f"auto 完成（僅記錄），時間={ts}，task={task_id}"
    summary = _pad_summary(summary)

    kind = args.kind or "status_update"
    handoff_note = args.handoff_note or ""
    if kind == "task_handoff" and len(handoff_note.strip()) < 20:
        handoff_note = _pad_summary(
            handoff_note or f"auto handoff for task {task_id}",
            min_len=20,
        )
    try:
        store_req = StoreRequest(
            task_id=task_id,
            agent_id=agent_id,
            kind=kind,  # type: ignore[arg-type]
            summary=summary,
            learnings=["recorded by remagraph auto"],
            handoff_note=handoff_note,
            tags=_parse_json_list(args.tags) or ["auto"],
        )
        store_resp = process_store(store_req, _get_conn())
        store_out: dict[str, Any] = {
            "status": store_resp.status,
            "id": store_resp.id,
            "reason": store_resp.reason,
            "exit_code": exit_code,
            "recalled": len(memories),
        }
        if not quiet:
            _log(f">>> 已儲存記憶：{store_resp.status} id={store_resp.id}")
        _print_json(store_out)
    except Exception as exc:  # noqa: BLE001
        _log(f"（儲存記憶失敗：{exc}，不影響任務結果）")
        _print_json(
            {
                "status": "error",
                "exit_code": exit_code,
                "recalled": len(memories),
                "detail": str(exc),
            }
        )

    _log("=== RemaGraph auto 結束 ===")
    if cmd:
        sys.exit(exit_code)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="remagraph",
        description="RemaGraph — AI agent 記憶工具（極簡 CLI）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # store
    p_store = sub.add_parser("store", help="寫入記憶")
    p_store.add_argument("--task-id", required=True)
    p_store.add_argument("--agent-id", required=True)
    p_store.add_argument(
        "--kind",
        required=True,
        choices=["task_handoff", "status_update", "discovered_constraint"],
    )
    p_store.add_argument("--summary", required=True)
    p_store.add_argument("--learnings", help='JSON 陣列，例如 \'["a","b"]\'')
    p_store.add_argument("--handoff-note", default="")
    p_store.add_argument("--tags", help="JSON 陣列")
    p_store.add_argument("--invalidates", help="JSON 陣列")

    # search
    p_search = sub.add_parser("search", help="查詢記憶（可用 --task-id 不帶 --query）")
    p_search.add_argument("--query", default="", help="全文關鍵字（可省略，改用 --task-id）")
    p_search.add_argument("--top-k", type=int, default=20)
    p_search.add_argument(
        "--kind",
        choices=["task_handoff", "status_update", "discovered_constraint"],
    )
    p_search.add_argument("--status", choices=["active", "superseded", "invalidated"])
    p_search.add_argument("--tags", help="JSON 陣列")
    p_search.add_argument("--agent-id")
    p_search.add_argument("--task-id")

    # status
    p_status = sub.add_parser("status", help="查詢最新現況")
    p_status.add_argument("--limit", type=int, default=20)

    # init
    p_init = sub.add_parser("init", help="初始化 RemaGraph（一行搞定，適合新手）")
    p_init.add_argument("--project", default="default", help="專案名稱（用來區分不同任務）")

    # auto
    p_auto = sub.add_parser(
        "auto",
        help="一鍵：讀取記憶 → 執行指令 → 自動儲存（最推薦）",
    )
    p_auto.add_argument("--task-id", default=None, help="任務編號（省略則自動產生）")
    p_auto.add_argument(
        "--agent-id",
        default=None,
        help="執行者名稱（省略則用環境變數或 default-agent）",
    )
    p_auto.add_argument("--summary", default="", help="自訂結尾摘要（可省略，會自動產生）")
    p_auto.add_argument(
        "--kind",
        choices=["task_handoff", "status_update", "discovered_constraint"],
        default=None,
    )
    p_auto.add_argument("--handoff-note", default="")
    p_auto.add_argument("--tags", help="JSON 陣列")
    p_auto.add_argument("--top-k", type=int, default=5)
    p_auto.add_argument("--quiet", action="store_true", help="少印訊息，只輸出 JSON")
    p_auto.add_argument(
        "cmd",
        nargs=argparse.REMAINDER,
        help="要執行的指令（建議前面加 -- ）",
    )

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
    elif args.command == "init":
        cmd_init(args)
    elif args.command == "auto":
        # 預設值：環境變數 → 自動產生
        if not args.task_id:
            args.task_id = os.environ.get("TASK_ID") or (
                f"task-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            )
        if not args.agent_id:
            args.agent_id = os.environ.get("AGENT_ID") or "default-agent"
        # REMAINDER 可能帶前導 '--'
        if args.cmd and args.cmd[0] == "--":
            args.cmd = args.cmd[1:]
        cmd_auto(args)


if __name__ == "__main__":
    main()
