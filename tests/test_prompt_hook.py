# SPDX-License-Identifier: Apache-2.0
"""`remagraph prompt-hook` —— Claude Code UserPromptSubmit 自動記憶召回。

需求（Aiken 經 Herdr Signal，2026-08-15）：比照 CodeGraph prompt-hook 模式，
讀取面全自動——以使用者提示對當前專案記憶庫檢索，把最相關記憶以
additionalContext 注入。硬性要求：
- 查無結果 / 無法推導專案 / 記憶庫不存在 → 靜默零輸出、exit 0
- 任何內部錯誤 → 乾淨降級（exit 0、零輸出），絕不干擾使用者
- 唯讀存取：絕不建立新資料庫、不觸發維護、不寫入
- CLI（search/store/status）比照 v2 post-commit hook 自動解析
  conventional state dir（~/.local/state/remagraph-<project>）
"""

from __future__ import annotations

import io
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from remagraph import db as db_mod
from remagraph.cli import main as cli_main
from remagraph.db import _init_schema
from remagraph.prompt_hook import (
    derive_project_from_cwd,
    resolve_conventional_state_dir,
    run_prompt_hook,
    slugify,
)

SUMMARY = "部署管線的記憶：deployment pipeline 需要先跑 staging 驗證才能上 production"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)
    monkeypatch.delenv("REMAGRAPH_PROJECT", raising=False)
    monkeypatch.setattr(
        db_mod, "DEFAULT_STATE_DIR", home / ".local" / "state" / "remagraph"
    )
    return home


def _make_project_db(home: Path, project: str, memories: list[tuple[str, str]]) -> Path:
    """建 conventional state dir + project.json + 含記憶的 DB。"""
    state_dir = home / ".local" / "state" / f"remagraph-{project}"
    state_dir.mkdir(parents=True)
    (state_dir / "project.json").write_text(
        json.dumps({"project_id": project, "state_dir": str(state_dir)}),
        encoding="utf-8",
    )
    conn = sqlite3.connect(str(state_dir / db_mod.DB_FILENAME))
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    now = "2026-08-15T00:00:00Z"
    for mem_id, summary in memories:
        conn.execute(
            "INSERT INTO memories (id, project_id, kind, task_id, agent_id, timestamp, "
            "summary, learnings, handoff_note, tags, status, created_at, updated_at) "
            "VALUES (?, ?, 'status_update', 'task-1', 'agent-1', ?, ?, "
            "'[\"先跑 staging\"]', '', '[]', 'active', ?, ?)",
            (mem_id, project, now, summary, now, now),
        )
    conn.commit()
    conn.close()
    return state_dir


def _make_git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    return path


# ---------------------------------------------------------------------------
# 基礎元件
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_matches_post_commit_hook_rules(self):
        assert slugify("MyRepo") == "myrepo"
        assert slugify("My Repo!") == "my-repo"
        assert slugify("ab") == "ab0"
        assert slugify("-lead") == "lead"
        assert slugify("") == "project"
        assert len(slugify("x" * 100)) <= 64


def test_derive_project_from_git_repo(tmp_path):
    repo = _make_git_repo(tmp_path / "My-Cool-Project")
    assert derive_project_from_cwd(str(repo)) == "my-cool-project"


def test_derive_project_outside_git_returns_none(tmp_path):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    assert derive_project_from_cwd(str(plain)) is None


def test_resolve_conventional_prefers_metadata_authoritative_name(isolated):
    _make_project_db(isolated, "MyRepo", [])
    resolved = resolve_conventional_state_dir("myrepo")
    assert resolved is not None
    state_dir, authoritative = resolved
    # 權威 project_id 一律以 project.json 為準（大小寫敏感）；state_dir
    # 在大小寫不敏感 FS 上可能以任一大小寫拼法命中同一實體目錄，只驗
    # casefold 相等。
    assert authoritative == "MyRepo"
    assert state_dir.name.casefold() == "remagraph-myrepo"


# ---------------------------------------------------------------------------
# run_prompt_hook 行為
# ---------------------------------------------------------------------------


def test_recalls_relevant_memory(isolated, tmp_path, monkeypatch):
    repo = _make_git_repo(tmp_path / "deployproj")
    _make_project_db(isolated, "deployproj", [("mem-20260815-001", SUMMARY)])

    payload = json.dumps(
        {"prompt": "幫我調整 deployment pipeline 的設定", "cwd": str(repo)}
    )
    out = run_prompt_hook(payload)
    assert "deployment pipeline" in out
    assert "mem-20260815-001" in out or "task-1" in out


def test_no_match_produces_zero_output(isolated, tmp_path):
    repo = _make_git_repo(tmp_path / "deployproj")
    _make_project_db(isolated, "deployproj", [("mem-20260815-001", SUMMARY)])
    payload = json.dumps(
        {"prompt": "completely unrelated xyzzy quux topic", "cwd": str(repo)}
    )
    assert run_prompt_hook(payload) == ""


def test_uninitialized_project_produces_zero_output(isolated, tmp_path):
    repo = _make_git_repo(tmp_path / "neverinit")
    payload = json.dumps({"prompt": "anything at all here", "cwd": str(repo)})
    assert run_prompt_hook(payload) == ""


def test_garbage_stdin_produces_zero_output(isolated):
    assert run_prompt_hook("this is not json {{{") == ""
    assert run_prompt_hook("") == ""


def test_does_not_create_database(isolated, tmp_path):
    """唯讀承諾：對未 init 的專案執行絕不憑空建立資料庫檔案。"""
    repo = _make_git_repo(tmp_path / "ghostproj")
    payload = json.dumps({"prompt": "some prompt text here", "cwd": str(repo)})
    run_prompt_hook(payload)
    conv = isolated / ".local" / "state" / "remagraph-ghostproj"
    assert not conv.exists()


def test_cli_prompt_hook_subcommand_exits_zero_on_everything(
    isolated, monkeypatch, capsys
):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    with pytest.raises(SystemExit) as exc:
        cli_main(["prompt-hook"])
    assert exc.value.code in (0, None)
    assert capsys.readouterr().out == ""


def test_cli_prompt_hook_outputs_context(isolated, tmp_path, monkeypatch, capsys):
    repo = _make_git_repo(tmp_path / "deployproj")
    _make_project_db(isolated, "deployproj", [("mem-20260815-001", SUMMARY)])
    payload = json.dumps(
        {"prompt": "deployment pipeline 要怎麼改", "cwd": str(repo)}
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    with pytest.raises(SystemExit) as exc:
        cli_main(["prompt-hook"])
    assert exc.value.code in (0, None)
    assert "deployment pipeline" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# CLI conventional state dir 自動解析（坑 a）
# ---------------------------------------------------------------------------


def test_cli_search_auto_resolves_conventional_state_dir(isolated, capsys):
    """REMAGRAPH_STATE_DIR 未設定、--project 的 conventional 目錄存在時，
    search 自動採用該目錄（修復前：安全閥直接拒絕，hook/腳本得自己
    export env）。"""
    _make_project_db(isolated, "autoproj", [("mem-20260815-001", SUMMARY)])
    cli_main(["search", "--project", "autoproj", "--query", "deployment"])
    payload = json.loads(capsys.readouterr().out)
    assert [r["id"] for r in payload["results"]] == ["mem-20260815-001"]


def test_cli_search_case_insensitive_project_uses_authoritative_name(isolated, capsys):
    _make_project_db(isolated, "MyRepo", [("mem-20260815-001", SUMMARY)])
    cli_main(["search", "--project", "myrepo", "--query", "deployment"])
    payload = json.loads(capsys.readouterr().out)
    assert [r["id"] for r in payload["results"]] == ["mem-20260815-001"]


def test_cli_search_without_conventional_dir_still_fails_cleanly(isolated, capsys):
    with pytest.raises(SystemExit) as exc:
        cli_main(["search", "--project", "nowhere", "--query", "deployment"])
    assert exc.value.code == 1
