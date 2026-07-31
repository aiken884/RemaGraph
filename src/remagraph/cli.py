# SPDX-License-Identifier: Apache-2.0
"""CLI subcommands for headless/automated agent integration.

Usage:
    remagraph init [--project NAME]
    remagraph store --task-id STR --agent-id STR --kind STR --summary STR [options]
    remagraph search [--query STR] [--task-id STR] [options]
    remagraph status [options]
    remagraph auto --task-id STR --agent-id STR [--] CMD [ARGS...]
    remagraph install-hooks [--global] [--force]
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
from remagraph.maintenance import (
    MaintenancePolicy,
    SafetyValveError,
    _record_violation,
    run_maintenance,
    safety_validate_project,
)
from remagraph.models import SearchRequest, StatusRequest, StoreRequest
from remagraph.search import get_status, search_memories
from remagraph.store import (
    MigrationReadOnlyError,
    ProjectNotRegisteredError,
    migrate_project_memories,
    process_store,
)

# ---------------------------------------------------------------------------
# DB 連線管理（CLI 專用，每次命令獨立連線）
# ---------------------------------------------------------------------------


def _get_conn(project_id: str | None = None) -> sqlite3.Connection:
    """開啟 CLI 子命令使用的連線。

    BUG 1 修復（PPLX 架構審查共識）：_db.connect() 早已內建
    maintenance.safety_validate_project 這道安全閥門（見該函式），但呼叫端
    必須明確傳入 project_id 才會觸發——修復前 cli.py 一律以零參數呼叫
    _db.connect()，導致實際連到哪一個實體 DB 檔案完全只取決於呼叫當下
    process 環境裡 REMAGRAPH_STATE_DIR/REMAGRAPH_PROJECT 剛好是什麼，與
    呼叫端明確指定的 --project 值完全脫鉤，安全閥門從未真正被觸發。

    現在由各 cmd_* 子命令透過 _project_id_for_conn() 決定是否傳入
    project_id（見該函式對『default』回退值的例外說明），本函式單純原樣
    轉呼叫，不重複判斷。
    """
    conn = _db.connect(project_id=project_id)
    atexit.register(_db.close, conn)
    return conn


def _project_id_for_conn(project: str | None) -> str | None:
    """決定要不要把 project 往下傳給 _get_conn()/db.connect() 以觸發
    safety_validate_project 強制驗證（BUG 1 修復）。

    刻意排除『default』回退值（以及 None，即 --all-projects 等完全未指定
    project 的情境）——這與 db.connect() 自身既有的 REMAGRAPH_PROJECT
    env 相容分支語意一致（`os.environ.get("REMAGRAPH_PROJECT", "default")
    != "default"` 才驗證），也是刻意的設計選擇：若連『沒有指定任何 project、
    退回 default』這種既有的合法用法都強制觸發安全閥門，會是一次不必要的
    regression（讓大量現有、從未指定 --project 的既有測試/既有用法無故
    開始失敗）。只有呼叫端明確指定了一個非 'default' 的 project 時，才
    真的有『project_id 與目前連線是否對映』這個問題需要驗證。
    """
    if project and project != "default":
        return project
    return None


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
            'ERROR: --tags/--learnings must be a JSON array, e.g. \'["a","b"]\'',
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
    pad = " (auto-padded by RemaGraph to meet the minimum summary length)"
    return (text + pad)[: max(min_len, len(text) + len(pad))]


# ---------------------------------------------------------------------------
# Subcommand: store
# ---------------------------------------------------------------------------


def cmd_store(args: argparse.Namespace) -> None:
    project = args.project or os.environ.get("REMAGRAPH_PROJECT") or "default"
    if project and project != "default" and project not in (args.task_id or "").lower():
        print(
            f"WARNING: task_id '{args.task_id}' does not include the project "
            f"'{project}' prefix; consider using '{project}-xxx'",
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
        labels=_parse_json_list(args.labels) or [],
    )
    try:
        conn = _get_conn(_project_id_for_conn(project))
    except Exception as e:
        print(f"ERROR: failed to connect to database - {e}", file=sys.stderr)
        sys.exit(1)
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
    if (
        not args.query
        and not args.task_id
        and not args.agent_id
        and not args.all_projects
        and not args.cross_project_label
    ):
        print(
            "ERROR: provide --query, or at least --task-id / --agent-id, "
            "or use --all-projects / --cross-project-label",
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
        cross_project_label=args.cross_project_label,
        include_related=args.include_related,
        related_hops=args.related_hops,
        fanout_cap=args.fanout_cap,
    )
    try:
        conn = _get_conn(_project_id_for_conn(project))
    except Exception as e:
        print(f"ERROR: failed to connect to database - {e}", file=sys.stderr)
        sys.exit(1)
    response = search_memories(conn, request)
    _print_json(
        {
            "results": response.results,
            "has_more": response.has_more,
            "cross_project_fanout_capped": response.cross_project_fanout_capped,
            "candidates_total": response.candidates_total,
            "candidates_searched": response.candidates_searched,
            "candidates_skipped": response.candidates_skipped,
        }
    )
    # BUG 2 修復（PPLX 架構審查共識）：cross_project_fanout_capped 時，只看
    # exit code 的呼叫端也必須能區分「完整結果」(0) 與「結果可能不完整」
    # (2)，而非誤判為與「真正的執行錯誤」(1) 同一等級，也不是誤判為
    # 「完全成功」(0)。
    if response.cross_project_fanout_capped:
        sys.exit(2)


# ---------------------------------------------------------------------------
# Subcommand: status
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> None:
    project = args.project or os.environ.get("REMAGRAPH_PROJECT")
    if args.all_projects:
        project = None
    elif not project:
        project = "default"
    try:
        conn = _get_conn(_project_id_for_conn(project))
    except Exception as e:
        print(f"ERROR: failed to connect to database - {e}", file=sys.stderr)
        sys.exit(1)
    request = StatusRequest(limit=args.limit, project_id=project)
    response = get_status(conn, request)
    result: dict[str, Any] = {"latest": response.latest}
    result.update(_db.get_compat_status(conn))
    _print_json(result)


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

    print("RemaGraph initialization complete!")
    print(f"Project: {project}")
    print(f"Memory folder: {state_dir}")
    print("")
    print("[Quick start in 3 steps, for non-technical users]")
    print(f"  1. source {env_file}")
    print("  2. Download the wrapper script if you haven't already:")
    print("     curl -O .../remagraph-task.sh")
    print("     chmod +x remagraph-task.sh")
    print("  3. Run your task:")
    print(f"     REMAGRAPH_PROJECT={project} TASK_ID=... ./remagraph-task.sh ...")
    print("")
    print("Or use the built-in one-shot command (recommended):")
    print(f"  remagraph auto --project {project} --task-id ... ")
    print("")
    print("[Extra tip if dispatched by an automated task-orchestration system]")
    print("  When dispatching from such a system, this is the recommended usage:")
    print(f"  REMAGRAPH_PROJECT={project} TASK_ID=... remagraph auto --project {project} ...")
    print("  Or check memory before dispatching:")
    print(f"  remagraph auto --project {project} --recall-only ...")
    print(
        "  Or write task_id directly into the agent's prompt so the agent can call it itself."
    )
    print("")
    print(
        "Note: project/task_id/agent_id may only contain letters, digits, "
        "underscores, and hyphens"
    )
    print("")
    print("You can now use these directly:")
    print(f"  export REMAGRAPH_STATE_DIR={state_dir}")
    print(f"  export REMAGRAPH_PROJECT={project}")
    print("")
    print("Internal testers, see: docs/internal/alpha-test-playbook.md")


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

    _log("=== RemaGraph auto starting ===")
    _log(f"Project: {project}  Task: {task_id}  Agent: {agent_id}")

    # 1. recall
    request = SearchRequest(query="", top_k=top_k, project_id=project, task_id=task_id)
    try:
        response = search_memories(_get_conn(_project_id_for_conn(project)), request)
        memories = response.results
    except Exception as exc:  # noqa: BLE001 — memory failures must not block the main task
        _log(f"(Failed to recall memory: {exc}, continuing anyway)")
        memories = []

    if not quiet:
        if memories:
            _log(f">>> Found {len(memories)} prior memory record(s):")
            for m in memories:
                _log(f"  - {m.get('summary', '')[:80]}")
        else:
            _log(">>> No prior memories found")

    if getattr(args, "recall_only", False):
        _log(">>> recall-only mode: skipping execution and storage")
        _print_json({"recalled": len(memories), "memories": memories})
        _log("=== RemaGraph auto finished ===")
        return

    # 2. Run the external command (optional)
    exit_code = 0
    if cmd:
        _log(f">>> Running: {' '.join(cmd)}")
        try:
            completed = subprocess.run(cmd, check=False)
            exit_code = int(completed.returncode)
        except FileNotFoundError:
            _log(f"ERROR: command not found: {cmd[0]!r}")
            sys.exit(127)
        except OSError as exc:
            _log(f"ERROR: failed to execute command: {exc}")
            sys.exit(126)
    else:
        _log(">>> No external command provided, doing recall + store only")

    # 3. Auto store
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if args.summary:
        summary = args.summary
    elif cmd:
        summary = f'auto completed command "{" ".join(cmd)}", exit_code={exit_code}, time={ts}'
    else:
        summary = f"auto completed (record only), time={ts}, task={task_id}"
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
            f"WARNING: task_id '{task_id}' does not include the project "
            f"'{project}' prefix; consider using '{project}-xxx'",
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
        store_resp = process_store(store_req, _get_conn(_project_id_for_conn(project)))
        store_out: dict[str, Any] = {
            "status": store_resp.status,
            "id": store_resp.id,
            "reason": store_resp.reason,
            "exit_code": exit_code,
            "recalled": len(memories),
        }
        if not quiet:
            _log(f">>> Memory stored: {store_resp.status} id={store_resp.id}")
        _print_json(store_out)
    except Exception as exc:  # noqa: BLE001
        _log(f"(Failed to store memory: {exc}, does not affect the task result)")
        _print_json(
            {
                "status": "error",
                "exit_code": exit_code,
                "recalled": len(memories),
                "detail": str(exc),
            }
        )

    _log("=== RemaGraph auto finished ===")
    if cmd:
        sys.exit(exit_code)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="remagraph",
        description="RemaGraph -- a minimalist memory CLI for AI agents",
    )
    parser.add_argument(
        "--allow-default-state-dir",
        action="store_true",
        help="Allow the default shared state dir (not recommended)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # store
    p_store = sub.add_parser("store", help="Store a memory")
    p_store.add_argument("--project", default=None)
    p_store.add_argument("--task-id", required=True)
    p_store.add_argument("--agent-id", required=True)
    p_store.add_argument(
        "--kind",
        required=True,
        choices=["task_handoff", "status_update", "discovered_constraint", "fleet_member"],
    )
    p_store.add_argument("--summary", required=True)
    p_store.add_argument("--learnings", help='JSON array, e.g. \'["a","b"]\'')
    p_store.add_argument("--handoff-note", default="")
    p_store.add_argument("--tags", help="JSON array")
    p_store.add_argument("--invalidates", help="JSON array")
    p_store.add_argument(
        "--labels",
        help=(
            "JSON array of namespaced labels, e.g. "
            "'[\"dep:opencode\",\"topic:auth\"]'. A different concept from "
            "--tags: labels require a namespace:value format and are used "
            "for cross-project label search (see search --cross-project-label)"
        ),
    )

    # search
    p_search = sub.add_parser(
        "search", help="Search memories (--task-id can be used without --query)"
    )
    p_search.add_argument(
        "--query", default="", help="Full-text keyword query (optional; use --task-id instead)"
    )
    p_search.add_argument("--top-k", type=int, default=20)
    p_search.add_argument(
        "--kind",
        choices=["task_handoff", "status_update", "discovered_constraint", "fleet_member"],
    )
    p_search.add_argument("--status", choices=["active", "superseded", "invalidated"])
    p_search.add_argument("--tags", help="JSON array")
    p_search.add_argument("--project")
    p_search.add_argument("--agent-id")
    p_search.add_argument("--task-id")
    p_search.add_argument(
        "--all-projects", action="store_true", help="Search across all projects (opt-in)"
    )
    p_search.add_argument(
        "--cross-project-label",
        default=None,
        help=(
            "Search across each known project's own separate database file "
            "by a namespaced label (e.g. 'dep:opencode'; see the item 4a "
            "registry). This is a completely different mechanism from "
            "--all-projects, which only removes the project filter within "
            "the current database file"
        ),
    )
    p_search.add_argument(
        "--include-related",
        action="store_true",
        help=(
            "Additionally fan out to projects explicitly declared as "
            "graph-linked via `remagraph link`, within --related-hops (see "
            "project_edges/recall_related, item 5). This is a fully "
            "independent dimension from --cross-project-label (indiscriminate "
            "fan-out to all known projects) and --all-projects. Requires "
            "--project (or REMAGRAPH_PROJECT) as the traversal starting "
            "point; gracefully falls back to a normal search if not provided"
        ),
    )
    p_search.add_argument(
        "--related-hops",
        type=int,
        default=1,
        help="BFS traversal depth for --include-related (default 1, direct relations only)",
    )
    p_search.add_argument(
        "--fanout-cap",
        type=int,
        default=None,
        help=(
            "Override the max number of 'other' project database connections "
            "opened per cross-project fan-out (--cross-project-label / "
            "--include-related) call (default 50; can also be set via the "
            "REMAGRAPH_FANOUT_CAP env var, this flag takes precedence). "
            "Always clamped to a hard cap of 200 (raise only via explicit "
            "opt-in through the REMAGRAPH_FANOUT_HARD_CAP env var); 0 does "
            "not mean unlimited"
        ),
    )

    # status
    p_status = sub.add_parser("status", help="Show latest status")
    p_status.add_argument("--project", default=None)
    p_status.add_argument("--limit", type=int, default=20)
    p_status.add_argument(
        "--all-projects", action="store_true", help="Search across all projects (opt-in)"
    )

    # init
    p_init = sub.add_parser(
        "init", help="Initialize RemaGraph (one command, beginner-friendly)"
    )
    p_init.add_argument(
        "--project", default="default", help="Project name (used to distinguish different tasks)"
    )

    # auto
    p_auto = sub.add_parser(
        "auto",
        help="One-shot: recall memory -> run command -> auto-store (recommended)",
    )
    p_auto.add_argument("--project", default=None)
    p_auto.add_argument(
        "--task-id", default=None, help="Task ID (auto-generated if omitted)"
    )
    p_auto.add_argument(
        "--agent-id",
        default=None,
        help="Agent name (falls back to an env var or 'default-agent' if omitted)",
    )
    p_auto.add_argument(
        "--summary", default="", help="Custom closing summary (optional, auto-generated if omitted)"
    )
    p_auto.add_argument(
        "--kind",
        choices=["task_handoff", "status_update", "discovered_constraint", "fleet_member"],
        default=None,
    )
    p_auto.add_argument("--handoff-note", default="")
    p_auto.add_argument("--tags", help="JSON array")
    p_auto.add_argument("--top-k", type=int, default=5)
    p_auto.add_argument(
        "--quiet", action="store_true", help="Print fewer messages, output JSON only"
    )
    p_auto.add_argument(
        "--recall-only",
        action="store_true",
        help=(
            "Only recall prior memories; do not run a command or auto-store "
            "(useful for checking memory before dispatching from a command tower)"
        ),
    )
    p_auto.add_argument(
        "--all-projects", action="store_true", help="Cross projects during recall (not recommended)"
    )
    p_auto.add_argument(
        "cmd",
        nargs=argparse.REMAINDER,
        help="Command to run (prefixing it with -- is recommended)",
    )

    # maintain
    p_maintain = sub.add_parser(
        "maintain", help="Run automatic DB maintenance (WAL/FTS/prune/vacuum/integrity)"
    )
    p_maintain.add_argument("--project", default=None)
    p_maintain.add_argument(
        "--force", action="store_true", help="Force all maintenance operations"
    )
    p_maintain.add_argument(
        "--dry-run", action="store_true", help="Only show what would be done, without executing"
    )

    # link
    p_link = sub.add_parser(
        "link",
        help="Declare a relation edge between two projects (used by --include-related recall)",
    )
    p_link.add_argument(
        "--from", dest="from_project", required=True, help="Source project_id"
    )
    p_link.add_argument(
        "--to", dest="to_project", required=True, help="Target project_id"
    )
    p_link.add_argument(
        "--relation",
        required=True,
        choices=["depends_on", "sibling", "shares_upstream", "monorepo_member"],
        help=(
            "Relation type (always treated as symmetric/bidirectional during "
            "traversal, see db.recall_related)"
        ),
    )

    # migrate-project
    p_migrate = sub.add_parser(
        "migrate-project",
        help=(
            "Migrate a project's memories from the source DB to a target "
            "per-project DB, marking the originals invalidated"
        ),
    )
    p_migrate.add_argument(
        "--from", dest="from_project", required=True, help="Source project"
    )
    p_migrate.add_argument(
        "--to", dest="to_project", required=True, help="Target project"
    )
    p_migrate.add_argument("--dry-run", action="store_true")
    p_migrate.add_argument(
        "--force", action="store_true", help="Ignore some safety checks"
    )

    # install-hooks
    p_install_hooks = sub.add_parser(
        "install-hooks",
        help="Install a git post-commit hook that auto-writes commit summaries to RemaGraph",
        description=(
            "Install a git post-commit hook that automatically writes commit "
            "summaries back to RemaGraph, without manually copying files from "
            "another project. By default installs into the current repo "
            "(auto-detects worktrees and core.hooksPath). --global additionally "
            "sets git's native init.templateDir so that repos created "
            "afterwards automatically get this hook -- but with two "
            "limitations: (1) it only affects repos created after this "
            "command runs; existing repos still need to run a non-global "
            "install-hooks individually; (2) using --global in CI is not "
            "recommended (a CI runner's $HOME may be ephemeral or shared "
            "across jobs/repos, especially on self-hosted runners, which is a "
            "security concern -- CI pipelines should instead run a non-global "
            "install-hooks explicitly for each repo/job)."
        ),
    )
    p_install_hooks.add_argument(
        "--global",
        dest="global_install",
        action="store_true",
        help=(
            "Additionally set git's native init.templateDir so repos created "
            "afterwards automatically get this hook (see the two limitations "
            "in the description above)"
        ),
    )
    p_install_hooks.add_argument(
        "--force",
        action="store_true",
        help=(
            "Overwrite an existing hook file or symlink not managed by "
            "remagraph (backs it up first)"
        ),
    )

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command != "init":
        if _db.is_using_default_state_dir() and not getattr(args, "allow_default_state_dir", False):
            proj = getattr(args, "project", None) or os.environ.get("REMAGRAPH_PROJECT", "default")
            if proj == "default":
                print(
                    "WARNING: using the default shared state dir with the "
                    "default project; consider running init --project",
                    file=sys.stderr,
                )
        try:
            proj = getattr(args, "project", None) or os.environ.get("REMAGRAPH_PROJECT")
            if proj:
                _db.validate_project_metadata(proj)
        except ValueError as e:
            # 這道頂層守門（8edb739e 引入）搶在任何 cmd_* 子命令被呼叫之前就
            # 攔截 project.json metadata 不符的情況——這代表它會先於
            # cmd_store/cmd_search/... 內部透過 _get_conn() ->
            # db.connect(project_id=...) -> safety_validate_project() 才會
            # 走到的、已修復的 project_metadata_mismatch 稽核記錄路徑動作，
            # 導致這個違規完全沒有留下 audit 記錄（獨立對抗式複審發現的缺口：
            # `remagraph store --project B` 在此情境下正確拒絕寫入，但
            # audit-*.jsonl 是空的，跟 `remagraph serve` 的同一種違規會寫下
            # safety_violation/project_metadata_mismatch 稽核記錄不一致）。
            #
            # 不能直接刪掉這道守門、改由各 cmd_* 自己的
            # _get_conn()/safety_validate_project() 頂替：cmd_auto 的
            # recall（第一次 _get_conn 呼叫）與 store（第二次 _get_conn 呼叫）
            # 兩處都各自包在會吞掉任意 Exception（含 SafetyValveError）的
            # try/except 裡（既有設計：記憶讀寫失敗不應阻斷主任務），若拿掉
            # 這裡的頂層守門，`remagraph auto --project B ...` 在同樣的
            # mismatch 情境下會變成「照常執行外部指令、只在讀寫記憶時安靜吞掉
            # 錯誤、且無 sys.exit(1)」——反而是真正的 regression。
            #
            # 因此這裡改為額外呼叫與 safety_validate_project() 完全相同的
            # maintenance._record_violation()，補上稽核記錄，不重複驗證邏輯
            # 本身（validate_project_metadata 仍只呼叫一次）。
            _record_violation(proj, "project_metadata_mismatch")
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
    elif args.command == "link":
        cmd_link(args)
    elif args.command == "install-hooks":
        cmd_install_hooks(args)


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
        print(f"ERROR: blocked by the safety valve - {e}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print("[dry-run] would run: WAL/FTS/prune/vacuum/analyze")
        return

    policy = MaintenancePolicy()
    try:
        stats = run_maintenance(policy, project, force=args.force)
        print("Maintenance complete:")
        _print_json(stats)
    except Exception as e:
        print(f"ERROR: maintenance failed - {e}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Subcommand: migrate-project
# ---------------------------------------------------------------------------


def cmd_migrate_project(args: argparse.Namespace) -> None:
    """CLI 端薄 wrapper：把 store.migrate_project_memories() 的結構化結果
    轉換成既有的 print()/sys.exit(1) 使用者體驗。

    真正的遷移邏輯（來源/目標 state_dir 解析、啟發式比對、逐筆搬移、唯讀
    降級檢查）全部在 store.migrate_project_memories() 裡，供本函式與 MCP
    的 server.remagraph_migrate_project 共用，不在此重複實作。
    """
    from_proj = args.from_project
    to_proj = args.to_project
    print(f"=== RemaGraph migrate-project: {from_proj} → {to_proj} ===")

    if from_proj == to_proj:
        print("ERROR: --from and --to cannot be the same", file=sys.stderr)
        sys.exit(1)

    try:
        result = migrate_project_memories(from_proj, to_proj, dry_run=args.dry_run)
    except SafetyValveError as e:
        print(f"ERROR: target project validation failed - {e}", file=sys.stderr)
        sys.exit(1)
    except ProjectNotRegisteredError as e:
        print(f"ERROR: source project validation failed - {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except MigrationReadOnlyError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if result.dry_run:
        print(
            f"[dry-run] migrating from {from_proj} to {to_proj}: "
            f"{result.migrated_count} record(s) would be migrated"
        )
        return

    total_attempted = result.migrated_count + len(result.skipped_ids)
    print(f"Found {total_attempted} record(s) to migrate")
    for skip_id in result.skipped_ids:
        print(f"  skip {skip_id}")

    print(f"Migration complete: {result.migrated_count} record(s)")
    if not args.force:
        print(
            f"Suggestion: run 'remagraph maintain --project {to_proj} --force' "
            "to clean up the target DB"
        )


# ---------------------------------------------------------------------------
# Subcommand: link（PPLX 架構改善計畫 item 5）
# ---------------------------------------------------------------------------


def cmd_link(args: argparse.Namespace) -> None:
    """宣告兩個 project 之間的關聯 edge，供之後 `search --include-related`
    使用（見 db.declare_project_edge / db.get_project_edges /
    db.recall_related）。

    edge 本身落在共用的 DEFAULT_STATE_DIR registry（與 item 4a 的
    project_registry 同一份檔案），與『目前這個 CLI 呼叫端當下所在的
    project 情境』無關——因此本子命令刻意不像 store/search/status 那樣走
    _get_conn()/safety_validate_project，不需要開啟任何一個特定 project
    自己的記憶資料庫。
    """
    try:
        _db.declare_project_edge(args.from_project, args.to_project, args.relation)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Relation declared: {args.from_project} --{args.relation}--> {args.to_project}")


# ---------------------------------------------------------------------------
# Subcommand: install-hooks
# ---------------------------------------------------------------------------


def cmd_install_hooks(args: argparse.Namespace) -> None:
    """安裝／升級 git post-commit hook 的薄 wrapper。

    實際邏輯（衝突偵測、symlink 處理、core.hooksPath/init.templateDir 解析）
    全部放在 remagraph.hooks_installer，這裡只負責：呼叫對應函式、把
    HooksInstallerError 轉成使用者看得懂的乾淨錯誤訊息（絕不讓原始
    subprocess stderr 或 Python traceback 外洩）、印出結果。
    """
    from remagraph.hooks_installer import HooksInstallerError, install_global, install_local

    try:
        if args.global_install:
            outcome = install_global(force=args.force)
        else:
            outcome = install_local(cwd=Path.cwd(), force=args.force)
    except HooksInstallerError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    for message in outcome.messages:
        print(message)
    print(f"post-commit hook install path: {outcome.path}")


if __name__ == "__main__":
    main()
