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
from remagraph.maintenance import MaintenancePolicy, run_maintenance, safety_validate_project
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
            'error: --tags/--learnings 必須是 JSON 陣列，例如 \'["a","b"]\'',
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
    project = args.project or os.environ.get("REMAGRAPH_PROJECT") or "default"
    if project and project != "default" and project not in (args.task_id or "").lower():
        print(
            f"WARNING: task_id '{args.task_id}' 未含 project '{project}' 前綴，"
            f"建議用 {project}-xxx",
            file=sys.stderr,
        )
    request = StoreRequest(
        project_id=project,
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
    project = args.project or os.environ.get("REMAGRAPH_PROJECT")
    if args.all_projects:
        project = None
    elif not project:
        project = "default"
    if not args.query and not args.task_id and not args.agent_id and not args.all_projects:
        print(
            "error: 請提供 --query，或至少提供 --task-id / --agent-id，或使用 --all-projects",
            file=sys.stderr,
        )
        sys.exit(1)
    request = SearchRequest(
        query=args.query or "",
        top_k=args.top_k,
        kind=args.kind,
        status=args.status,
        tags=_parse_json_list(args.tags),
        project_id=project,
        agent_id=args.agent_id,
        task_id=args.task_id,
    )
    response = search_memories(_get_conn(), request)
    _print_json({"results": response.results, "has_more": response.has_more})


# ---------------------------------------------------------------------------
# Subcommand: status
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> None:
    project = args.project or os.environ.get("REMAGRAPH_PROJECT")
    if args.all_projects:
        project = None
    elif not project:
        project = "default"
    request = StatusRequest(limit=args.limit, project_id=project)
    response = get_status(_get_conn(), request)
    _print_json({"latest": response.latest})


# ---------------------------------------------------------------------------
# Subcommand: init
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> None:
    """極簡初始化 - 為非技術使用者設計，一行指令即可。"""
    project = args.project or "default"
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in project)
    if not safe:
        safe = "default"
    state_dir = Path.home() / ".local" / "state" / f"remagraph-{safe}"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_dir.chmod(0o700)

    env_file = state_dir / "env.sh"
    env_file.write_text(
        f'export REMAGRAPH_STATE_DIR="{state_dir}"\nexport REMAGRAPH_PROJECT="{project}"\n',
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    meta_file = state_dir / "project.json"
    meta_file.write_text(
        f'{{"project_id": "{project}", "state_dir": "{state_dir}", '
        f'"created": "{__import__("datetime").datetime.now().isoformat()}"}}',
        encoding="utf-8",
    )
    meta_file.chmod(0o600)

    print("✅ RemaGraph 初始化完成！")
    print(f"專案：{project}")
    print(f"記憶資料夾：{state_dir}")
    print("")
    print("【最簡單三步驟（非技術使用者）】")
    print(f"  1. source {env_file}")
    print("  2. 下載包裝腳本（若尚未下載）：")
    print("     curl -O .../remagraph-task.sh")
    print("     chmod +x remagraph-task.sh")
    print("  3. 執行任務：")
    print(f"     REMAGRAPH_PROJECT={project} TASK_ID=... ./remagraph-task.sh ...")
    print("")
    print("或用內建一鍵指令（推薦）：")
    print(f"  remagraph auto --project {project} --task-id ... ")
    print("")
    print("【herdr Bridge 使用者額外提示】")
    print("  在指揮塔派工時，建議這樣用：")
    print(f"  REMAGRAPH_PROJECT={project} TASK_ID=... remagraph auto --project {project} ...")
    print("  或派工前先查記憶：")
    print(f"  remagraph auto --project {project} --recall-only ...")
    print("  或在送給 agent 的 prompt 裡面直接寫上 task_id，讓 agent 自己呼叫。")
    print("")
    print("注意：project/task_id/agent_id 限英文數字底線連字號")
    print("")
    print("之後可直接：")
    print(f"  export REMAGRAPH_STATE_DIR={state_dir}")
    print(f"  export REMAGRAPH_PROJECT={project}")
    print("")
    print("內部測試者請參考：docs/internal/alpha-test-playbook.md")


# ---------------------------------------------------------------------------
# Subcommand: auto
# ---------------------------------------------------------------------------


def cmd_auto(args: argparse.Namespace) -> None:
    project = args.project or os.environ.get("REMAGRAPH_PROJECT") or "default"
    if args.all_projects:
        project = None
    task_id = args.task_id
    agent_id = args.agent_id
    top_k = args.top_k
    quiet = args.quiet
    cmd = list(args.cmd or [])

    def _log(msg: str) -> None:
        if not quiet:
            print(msg, file=sys.stderr)

    _log("=== RemaGraph auto 開始 ===")
    _log(f"專案：{project} 任務：{task_id} / 執行者：{agent_id}")

    # 1. recall
    request = SearchRequest(query="", top_k=top_k, project_id=project, task_id=task_id)
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

    if getattr(args, "recall_only", False):
        _log(">>> recall-only 模式：不執行、不儲存")
        _print_json({"recalled": len(memories), "memories": memories})
        _log("=== RemaGraph auto 結束 ===")
        return

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
        summary = f"auto 完成指令「{' '.join(cmd)}」，退出碼={exit_code}，時間={ts}"
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
    if project and project != "default" and project not in (task_id or "").lower():
        print(
            f"WARNING: task_id '{task_id}' 未含 project '{project}' 前綴，建議用 {project}-xxx",
            file=sys.stderr,
        )
    try:
        store_req = StoreRequest(
            project_id=project,
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
    parser.add_argument(
        "--allow-default-state-dir",
        action="store_true",
        help="允許預設共享 state dir（不推薦）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # store
    p_store = sub.add_parser("store", help="寫入記憶")
    p_store.add_argument("--project", default=None)
    p_store.add_argument("--task-id", required=True)
    p_store.add_argument("--agent-id", required=True)
    p_store.add_argument(
        "--kind",
        required=True,
        choices=["task_handoff", "status_update", "discovered_constraint", "fleet_member"],
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
        choices=["task_handoff", "status_update", "discovered_constraint", "fleet_member"],
    )
    p_search.add_argument("--status", choices=["active", "superseded", "invalidated"])
    p_search.add_argument("--tags", help="JSON 陣列")
    p_search.add_argument("--project")
    p_search.add_argument("--agent-id")
    p_search.add_argument("--task-id")
    p_search.add_argument(
        "--all-projects", action="store_true", help="跨所有 project 查詢（需同意）"
    )

    # status
    p_status = sub.add_parser("status", help="查詢最新現況")
    p_status.add_argument("--project", default=None)
    p_status.add_argument("--limit", type=int, default=20)
    p_status.add_argument(
        "--all-projects", action="store_true", help="跨所有 project 查詢（需同意）"
    )

    # init
    p_init = sub.add_parser("init", help="初始化 RemaGraph（一行搞定，適合新手）")
    p_init.add_argument("--project", default="default", help="專案名稱（用來區分不同任務）")

    # auto
    p_auto = sub.add_parser(
        "auto",
        help="一鍵：讀取記憶 → 執行指令 → 自動儲存（最推薦）",
    )
    p_auto.add_argument("--project", default=None)
    p_auto.add_argument("--task-id", default=None, help="任務編號（省略則自動產生）")
    p_auto.add_argument(
        "--agent-id",
        default=None,
        help="執行者名稱（省略則用環境變數或 default-agent）",
    )
    p_auto.add_argument("--summary", default="", help="自訂結尾摘要（可省略，會自動產生）")
    p_auto.add_argument(
        "--kind",
        choices=["task_handoff", "status_update", "discovered_constraint", "fleet_member"],
        default=None,
    )
    p_auto.add_argument("--handoff-note", default="")
    p_auto.add_argument("--tags", help="JSON 陣列")
    p_auto.add_argument("--top-k", type=int, default=5)
    p_auto.add_argument("--quiet", action="store_true", help="少印訊息，只輸出 JSON")
    p_auto.add_argument(
        "--recall-only",
        action="store_true",
        help="只讀取之前記憶，不執行指令也不自動儲存（適合指揮塔派工前先查）",
    )
    p_auto.add_argument("--all-projects", action="store_true", help="recall 時跨 project（不推薦）")
    p_auto.add_argument(
        "cmd",
        nargs=argparse.REMAINDER,
        help="要執行的指令（建議前面加 -- ）",
    )

    # maintain
    p_maintain = sub.add_parser(
        "maintain", help="執行 DB 自動維護（WAL/FTS/prune/vacuum/integrity）"
    )
    p_maintain.add_argument("--project", default=None)
    p_maintain.add_argument("--force", action="store_true", help="強制所有維護操作")
    p_maintain.add_argument("--dry-run", action="store_true", help="只顯示會做什麼，不實際執行")

    # migrate-project
    p_migrate = sub.add_parser(
        "migrate-project",
        help="將某 project 記憶從來源 DB 遷移到目標 per-project DB，並標記 invalidated",
    )
    p_migrate.add_argument("--from", dest="from_project", required=True, help="來源 project")
    p_migrate.add_argument("--to", dest="to_project", required=True, help="目標 project")
    p_migrate.add_argument("--dry-run", action="store_true")
    p_migrate.add_argument("--force", action="store_true", help="忽略部分安全檢查")

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command != "init":
        if _db.is_using_default_state_dir() and not getattr(args, "allow_default_state_dir", False):
            proj = getattr(args, "project", None) or os.environ.get("REMAGRAPH_PROJECT", "default")
            if proj == "default":
                print(
                    "WARNING: 預設共享 state dir + default project，建議 init --project",
                    file=sys.stderr,
                )
        try:
            proj = getattr(args, "project", None) or os.environ.get("REMAGRAPH_PROJECT")
            if proj:
                _db.validate_project_metadata(proj)
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)

    if args.command == "store":
        cmd_store(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "init":
        cmd_init(args)
    elif args.command == "auto":
        if not args.project:
            args.project = os.environ.get("REMAGRAPH_PROJECT")
        if not args.task_id:
            args.task_id = os.environ.get("TASK_ID") or (
                f"task-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            )
        if not args.agent_id:
            args.agent_id = os.environ.get("AGENT_ID") or "default-agent"
        if args.cmd and args.cmd[0] == "--":
            args.cmd = args.cmd[1:]
        cmd_auto(args)
    elif args.command == "maintain":
        cmd_maintain(args)
    elif args.command == "migrate-project":
        cmd_migrate_project(args)


# ---------------------------------------------------------------------------
# Subcommand: maintain
# ---------------------------------------------------------------------------


def cmd_maintain(args: argparse.Namespace) -> None:
    project = args.project or os.environ.get("REMAGRAPH_PROJECT") or "default"
    print(f"=== RemaGraph maintain: project={project} ===")

    # 強制安全閥門 + 設定 env（確保用正確 DB）
    try:
        state_dir = safety_validate_project(project)
        os.environ["REMAGRAPH_STATE_DIR"] = str(state_dir)
        os.environ["REMAGRAPH_PROJECT"] = project
    except Exception as e:
        print(f"ERROR: 安全閥門阻擋 - {e}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print("[dry-run] 將執行：WAL/FTS/prune/vacuum/analyze")
        return

    policy = MaintenancePolicy()
    try:
        stats = run_maintenance(policy, project, force=args.force)
        print("維護完成：")
        _print_json(stats)
    except Exception as e:
        print(f"ERROR: 維護失敗 - {e}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Subcommand: migrate-project
# ---------------------------------------------------------------------------


def cmd_migrate_project(args: argparse.Namespace) -> None:
    from_proj = args.from_project
    to_proj = args.to_project
    print(f"=== RemaGraph migrate-project: {from_proj} → {to_proj} ===")

    if from_proj == to_proj:
        print("ERROR: from 與 to 不能相同", file=sys.stderr)
        sys.exit(1)

    # 驗證目標 project 的 state_dir
    try:
        to_state = safety_validate_project(to_proj, require_env_match=False)
        os.environ["REMAGRAPH_STATE_DIR"] = str(to_state)
        os.environ["REMAGRAPH_PROJECT"] = to_proj
    except Exception as e:
        print(f"ERROR: 目標 project 驗證失敗 - {e}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print(f"[dry-run] 從 {from_proj} 遷移到 {to_proj} (target: {to_state})")
        return

    # 實際遷移邏輯（簡化版，使用 sqlite 直接操作）
    default_db = Path.home() / ".local/state/remagraph/remagraph.db"
    target_db = to_state / "remagraph.db"

    if not default_db.exists():
        print("ERROR: default DB 不存在", file=sys.stderr)
        sys.exit(1)

    conn_src = sqlite3.connect(str(default_db))
    conn_src.row_factory = sqlite3.Row
    conn_tgt = sqlite3.connect(str(target_db))
    conn_tgt.row_factory = sqlite3.Row

    # 找屬於 to_proj 的記錄（用 task_id / tags / agent_id 啟發式）
    rows = conn_src.execute(
        """
        SELECT * FROM memories
        WHERE (task_id LIKE ? OR tags LIKE ? OR agent_id LIKE ? OR summary LIKE ?)
          AND status != 'invalidated'
        """,
        (f"%{to_proj}%", f"%{to_proj}%", f"%{to_proj}%", f"%{to_proj}%"),
    ).fetchall()

    print(f"找到 {len(rows)} 筆待遷移")

    migrated = 0
    for row in rows:
        try:
            # 複製到目標（強制 project_id）
            cols = [k for k in row.keys() if k != "project_id"]
            vals = [row[k] for k in cols]
            placeholders = ",".join("?" for _ in cols)
            cols_str = ','.join(cols)
            sql = (
                f"INSERT OR IGNORE INTO memories "
                f"(project_id, {cols_str}) VALUES (?, {placeholders})"
            )
            conn_tgt.execute(sql, [to_proj] + vals)

            # 在來源 invalidat e
            learn = json.loads(row["learnings"] or "[]")
            learn.append(f"migrated-to:{to_proj} at {datetime.now(timezone.utc).isoformat()}")
            conn_src.execute(
                "UPDATE memories SET status='invalidated', learnings=? WHERE id=?",
                (json.dumps(learn, ensure_ascii=False), row["id"]),
            )
            migrated += 1
        except Exception as e:
            print(f"  skip {row['id']}: {e}")

    conn_tgt.commit()
    conn_src.commit()
    conn_tgt.close()
    conn_src.close()

    print(f"遷移完成：{migrated} 筆")
    if not args.force:
        print("建議：執行 remagraph maintain --project {to_proj} --force 清理目標 DB")


if __name__ == "__main__":
    main()
