# SPDX-License-Identifier: Apache-2.0
"""Regression tests for BUG 1 (P0): project_id 未曾真正驅動 CLI 連線解析
（PPLX 架構審查共識）。

根因（已讀碼確認）：db.connect(project_id=...) 早已內建正確的安全閥門
（maintenance.safety_validate_project），但 cli.py 的 _get_conn() 呼叫
_db.connect() 時從未傳入 project_id —— CLI 子命令（store/search/status）
即使呼叫端明確帶了 --project foo，實際連到哪一個實體 DB 檔案，完全只取決於
呼叫當下 process 環境裡 REMAGRAPH_STATE_DIR/REMAGRAPH_PROJECT 剛好是什麼，
與 --project foo 這個值完全脫鉤，安全閥門從未真正被觸發。

本檔驗證修復後：
1. --project 明確指定為非 'default' 值、但 REMAGRAPH_STATE_DIR 未設定時，
   store/search/status 三個子命令一律經由 SafetyValveError 快速失敗
   （exit 1），而非悄悄改用 ambient default state dir 寫入/讀取 ——
   並額外驗證『沒有任何實際記憶資料悄悄寫進 default state dir』，證明
   修復前『安全閥門從未觸發』的缺口確實已被堵住。
   注意：resolve_project_state_dir() 本身有一個獨立、pre-existing、與本次
   修復無關的 best-effort 副作用——任何解析都會把 (project_id, state_dir)
   upsert 進共用的 project_registry（見 db.register_known_project），即使
   該次解析最終導致 SafetyValveError。這會在 default state dir 底下建立
   一個只含 project_registry/project_edges 兩張表的輕量 remagraph.db，
   純粹是登記簿，與『memories 表被寫入實際記憶資料』完全是两回事，因此下方
   驗證的是 memories 表是否存在（不存在代表沒有任何完整的 db.connect()/
   process_store 曾經對這個檔案執行過 schema 初始化或寫入），而不是整個
   db 檔案是否存在。
2. herdr-* project 搭配 basename 為 'remagraph' 的 state dir（既有的
   herdr_using_default_db 檢查）現在也能經由 CLI 觸發。
3. 完全不指定 project（沿用既有的『default』回退語意）時，行為完全不受
   影響 —— 這是 db.connect() 對 REMAGRAPH_PROJECT env 相容分支既有的
   'default 例外'，本次修復刻意延續，避免大量既有測試/既有合法用法出現
   regression。
4. 明確指定非 default project、且 REMAGRAPH_STATE_DIR 已設定並與其相符時，
   安全閥門通過，行為與修復前一致（無 regression）。

隔離注意事項（比照 tests/test_safety_violation_dir_consistency.py 的
fake_home fixture）：「missing_remagraph_state_dir」這個違規原因的定義就是
REMAGRAPH_STATE_DIR 未設定，此時 maintenance.resolve_project_state_dir()
的 fallback 分支會用『真正的』pathlib.Path.home() 組出一個 per-project 目錄
（~/.local/state/remagraph-<project_id>）並透過 _record_violation 寫入
audit event + discovered_constraint 記憶 —— 若只 monkeypatch
db_mod.DEFAULT_STATE_DIR 而不同時隔離 HOME 環境變數，這些寫入會外洩到
執行測試這台機器上真正的 home 目錄（已於開發過程中實際重現並清除過一次
洩漏的 ~/.local/state/remagraph-myproj，見本次修復報告）。因此本檔的
autouse fixture 額外把 HOME 導向 tmp_path 底下的假目錄。
"""

from __future__ import annotations

import json
import sqlite3

import pytest

import remagraph.cli as cli_mod
from remagraph import db as db_mod
from remagraph.maintenance import SafetyValveError


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)
    monkeypatch.delenv("REMAGRAPH_PROJECT", raising=False)
    home = tmp_path / "fake-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(db_mod, "DEFAULT_STATE_DIR", home / ".local" / "state" / "remagraph")
    return home


def _default_dir_has_memories_table(fake_home) -> bool:
    """判斷 default state dir 底下的 remagraph.db（若存在）是否真的含有
    'memories' 表 —— 用來區分『resolve_project_state_dir 的 registry
    best-effort 副作用建立的輕量登記簿檔案』（只含 project_registry/
    project_edges，見模組頂端說明）與『真的有一次完整的 db.connect()/
    process_store 對這個檔案執行過 schema 初始化或寫入』。"""
    db_path = fake_home / ".local" / "state" / "remagraph" / "remagraph.db"
    if not db_path.exists():
        return False
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memories'"
        ).fetchone()
        return row is not None
    finally:
        conn.close()


_LONG_SUMMARY = "這是一段足夠長的 summary 來通過仲裁規則檢查，至少需要三十個中文字元才能過關"


def _store_args(project: str, task_id: str) -> list[str]:
    return [
        "store",
        "--project",
        project,
        "--task-id",
        task_id,
        "--agent-id",
        "agent-1",
        "--kind",
        "status_update",
        "--summary",
        _LONG_SUMMARY,
        "--learnings",
        '["a"]',
    ]


# ---------------------------------------------------------------------------
# 1. 明確 --project + REMAGRAPH_STATE_DIR 未設定 → 快速失敗，不悄悄改用
#    ambient default state dir
# ---------------------------------------------------------------------------


def test_cmd_store_rejects_missing_state_dir_for_explicit_project(fake_home, capsys):
    with pytest.raises(SystemExit) as ei:
        cli_mod.main(_store_args("myproj", "myproj-task-001"))
    assert ei.value.code == 1

    err = capsys.readouterr().err
    assert "REMAGRAPH_STATE_DIR" in err

    # 修復前的缺口：--project 完全被忽略，會悄悄在 ambient default state
    # dir 建立/寫入資料庫。修復後必須連 DB 檔案都不該被建立。
    assert not _default_dir_has_memories_table(fake_home)


def test_cmd_search_rejects_missing_state_dir_for_explicit_project(fake_home, capsys):
    with pytest.raises(SystemExit) as ei:
        cli_mod.main(["search", "--project", "myproj", "--query", "hello world"])
    assert ei.value.code == 1

    err = capsys.readouterr().err
    assert "REMAGRAPH_STATE_DIR" in err
    assert not _default_dir_has_memories_table(fake_home)


def test_cmd_status_rejects_missing_state_dir_for_explicit_project(fake_home, capsys):
    with pytest.raises(SystemExit) as ei:
        cli_mod.main(["status", "--project", "myproj"])
    assert ei.value.code == 1

    err = capsys.readouterr().err
    assert "REMAGRAPH_STATE_DIR" in err
    assert not _default_dir_has_memories_table(fake_home)


# ---------------------------------------------------------------------------
# 2. herdr-* project + basename 'remagraph' 的 state dir → 現在也能經由
#    CLI 觸發既有的 herdr_using_default_db 檢查
# ---------------------------------------------------------------------------


def test_cmd_store_rejects_herdr_project_against_default_named_dir(
    tmp_path, fake_home, monkeypatch, capsys
):
    state_dir = tmp_path / "state" / "remagraph"  # basename 就是 'remagraph'
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    with pytest.raises(SystemExit) as ei:
        cli_mod.main(_store_args("herdr-foo", "herdr-foo-task-001"))
    assert ei.value.code == 1

    err = capsys.readouterr().err
    assert "herdr" in err and ("default DB" in err or "獨立 state_dir" in err)


# ---------------------------------------------------------------------------
# 3. 完全不指定 project（沿用既有的 'default' 回退語意）→ 行為不受影響
# ---------------------------------------------------------------------------


def test_cmd_store_default_project_without_state_dir_pre_existing_behavior_unchanged(
    fake_home, capsys
):
    """注意（重要的既有行為說明，非本次修復範圍）：store.process_store()
    自己早已（與本次 CLI 連線 threading 修復完全無關、pre-existing）對任何
    truthy 的 request.project_id ——包括 cmd_store 的『default』回退值——
    無條件呼叫 safety_validate_project()，沒有 db.connect() 那種對
    'default' 的例外。因此即使本次修復刻意讓 _get_conn() 對『default』
    回退值略過 project_id 連線層驗證（見 test_cmd_store_explicit_project_*
    與模組頂端說明），process_store 自己的既有檢查仍會獨立擋下沒有設定
    REMAGRAPH_STATE_DIR 的 store 呼叫。這在修復前後皆一致（已用
    `git stash` 對照確認：修復前同樣拋出 SafetyValveError），因此不是本次
    修復造成的 regression，維持既有行為，僅在此明確釘住、避免日後誤以為
    是新引入的問題。process_store 對此例外並未 try/except（cmd_store 只包住
    _get_conn()，process_store 呼叫本身不在 try 區塊內），因此例外會直接
    往外傳，而非乾淨的 SystemExit(1)——與 cmd_store 對 _get_conn() 失敗
    的既有處理方式不同，但同樣屬於 pre-existing、非本次修復範圍。"""
    with pytest.raises(SafetyValveError, match="REMAGRAPH_STATE_DIR"):
        cli_mod.main(
            [
                "store",
                "--task-id",
                "default-task-001",
                "--agent-id",
                "agent-1",
                "--kind",
                "status_update",
                "--summary",
                _LONG_SUMMARY,
                "--learnings",
                '["a"]',
            ]
        )


def test_cmd_search_default_project_without_state_dir_still_works(fake_home, capsys):
    cli_mod.main(["search", "--task-id", "no-such-task"])
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["results"] == []


def test_cmd_status_default_project_without_state_dir_still_works(fake_home, capsys):
    cli_mod.main(["status"])
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["latest"] == []


# ---------------------------------------------------------------------------
# 4. 明確指定非 default project、且 REMAGRAPH_STATE_DIR 已設定並相符時，
#    安全閥門通過 —— 行為與修復前一致（無 regression）
# ---------------------------------------------------------------------------


def test_cmd_store_explicit_project_with_matching_state_dir_succeeds(
    tmp_path, fake_home, monkeypatch, capsys
):
    state_dir = tmp_path / "proj-foo-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    cli_mod.main(_store_args("foo", "foo-task-001"))
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["status"] == "stored"
    assert (state_dir / "remagraph.db").exists()


def test_cmd_search_explicit_project_with_matching_state_dir_succeeds(
    tmp_path, fake_home, monkeypatch, capsys
):
    state_dir = tmp_path / "proj-foo-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    cli_mod.main(_store_args("foo", "foo-task-002"))
    capsys.readouterr()

    cli_mod.main(["search", "--project", "foo", "--task-id", "foo-task-002"])
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert any(r["task_id"] == "foo-task-002" for r in payload["results"])


def test_cmd_status_explicit_project_with_matching_state_dir_succeeds(
    tmp_path, fake_home, monkeypatch, capsys
):
    state_dir = tmp_path / "proj-foo-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    cli_mod.main(_store_args("foo", "foo-task-003"))
    capsys.readouterr()

    cli_mod.main(["status", "--project", "foo"])
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert any(r["task_id"] == "foo-task-003" for r in payload["latest"])
