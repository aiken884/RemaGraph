# SPDX-License-Identifier: Apache-2.0
"""CLI init / auto / task-id-only search 測試。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from remagraph.cli import build_parser, cmd_auto, cmd_init, main
from remagraph.models import SearchRequest, StoreRequest
from remagraph.search import search_memories
from remagraph.store import process_store


@pytest.fixture
def state_env(tmp_path, monkeypatch):
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path / "state"))
    return tmp_path


def test_init_creates_dir_and_env_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    args = build_parser().parse_args(["init", "--project", "demo-proj"])
    cmd_init(args)
    out = capsys.readouterr().out
    assert "initialization complete" in out
    state = tmp_path / ".local" / "state" / "remagraph-demo-proj"
    assert state.is_dir()
    assert (state / "env.sh").is_file()
    env_text = (state / "env.sh").read_text(encoding="utf-8")
    assert "REMAGRAPH_STATE_DIR" in env_text


def test_search_by_task_id_without_query(state_env):
    from remagraph import db as db_mod

    conn = db_mod.connect()
    process_store(
        StoreRequest(
            project_id="testproj",
            task_id="task-cli-001",
            agent_id="agent-a",
            kind="status_update",
            summary="這是一段足夠長的摘要，用來通過仲裁規則，至少需要三十個字元以上才行",
            learnings=["x"],
        ),
        conn,
    )
    resp = search_memories(
        conn,
        SearchRequest(query="", project_id="testproj", task_id="task-cli-001", top_k=5),
    )
    assert len(resp.results) >= 1
    assert resp.results[0]["task_id"] == "task-cli-001"
    db_mod.close(conn)


def test_auto_recall_and_store_without_cmd(state_env, capsys):
    args = build_parser().parse_args(
        [
            "auto",
            "--task-id",
            "task-auto-001",
            "--agent-id",
            "bot-1",
            "--quiet",
        ]
    )
    # simulate main defaults
    args.cmd = []
    cmd_auto(args)
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["status"] == "stored"
    assert payload["recalled"] == 0
    assert payload.get("id")


def test_auto_runs_command_and_stores(state_env, capsys):
    args = build_parser().parse_args(
        [
            "auto",
            "--task-id",
            "task-auto-002",
            "--agent-id",
            "bot-2",
            "--quiet",
            "--",
            sys.executable,
            "-c",
            "print('ok')",
        ]
    )
    if args.cmd and args.cmd[0] == "--":
        args.cmd = args.cmd[1:]
    with pytest.raises(SystemExit) as ei:
        cmd_auto(args)
    assert ei.value.code == 0
    out = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(out)
    assert payload["status"] == "stored"
    assert payload["exit_code"] == 0


def test_main_search_task_id_cli(state_env, capsys):
    from remagraph import db as db_mod

    conn = db_mod.connect()
    process_store(
        StoreRequest(
            project_id="testproj",
            task_id="task-main-1",
            agent_id="agent-1",
            kind="status_update",
            summary="主流程 CLI 搜尋測試摘要必須超過三十個字元長度，這裡再補一些字",
            learnings=["cli-search"],
        ),
        conn,
    )
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] >= 1
    db_mod.close(conn)

    main(["search", "--project", "testproj", "--task-id", "task-main-1", "--top-k", "3"])
    out = capsys.readouterr().out.strip()
    data = json.loads(out)
    assert "results" in data
    assert any(r["task_id"] == "task-main-1" for r in data["results"])


def test_auto_command_not_found_exits_127(state_env):
    args = build_parser().parse_args(
        [
            "auto",
            "--task-id",
            "task-missing-cmd",
            "--agent-id",
            "bot",
            "--quiet",
            "--",
            "this-command-should-not-exist-xyz",
        ]
    )
    if args.cmd and args.cmd[0] == "--":
        args.cmd = args.cmd[1:]
    with pytest.raises(SystemExit) as ei:
        cmd_auto(args)
    assert ei.value.code == 127
