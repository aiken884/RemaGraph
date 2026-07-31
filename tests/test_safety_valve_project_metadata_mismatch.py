# SPDX-License-Identifier: Apache-2.0
"""Regression tests for CRITICAL BUG 1: 安全閥門的核心比較恆為 False，
從未真正攔下「REMAGRAPH_STATE_DIR 被設成別的 project 目錄」這個真實事故
（獨立對抗式審查發現）。

根因：maintenance.resolve_project_state_dir(project_id) 在
REMAGRAPH_STATE_DIR 有設定時，完全忽略傳入的 project_id，逐字回傳該 env
變數解析出的路徑。maintenance.safety_validate_project() 隨後拿
`configured = resolve_project_state_dir(project_id)` 去跟
`env_dir = resolve(REMAGRAPH_STATE_DIR)` 比較是否相等——但 configured
本來就是從 env_dir 算出來的，這是拿一個值跟自己比較，`env_dir != configured`
恆為 False。真實重現：
`REMAGRAPH_STATE_DIR=$SCRATCH/proj-A remagraph store --project B ...`
成功執行，且把資料寫進 proj-A 的資料庫，標記為 project B，完全沒有報錯。

修復：在 safety_validate_project() 內額外呼叫既有的
db.validate_project_metadata(expected_project, state_dir)——它會讀取
state_dir 底下實際存在的 project.json，若該目錄先前已合法用於另一個
project_id（非 DEFAULT_PROJECT_ID 佔位值）且與目前要求的 project_id 不同，
拋出 ValueError，safety_validate_project 將其轉換為 SafetyValveError +
`_record_violation(project_id, "project_metadata_mismatch")`，在任何寫入
發生之前就擋下。

本檔驗證：
1. 目錄從未被使用過（無 project.json）—— 任何 project_id 第一次使用必須成功
   （不能誤判為衝突）。
2. 目錄已合法用於 project "A" —— 用 project_id="A" 再次連線必須繼續成功
   （同一個 project，非衝突）。
3. 目錄已合法用於 project "A" —— 用 project_id="B" 連線必須被拒絕，reason
   為新的 "project_metadata_mismatch"。
4. 端到端：透過真正的 CLI 路徑（`remagraph store --project B`）重現審查者的
   原始重現步驟，確認現在會被拒絕，且該目錄的 SQLite 資料庫完全沒有被建立/
   寫入任何內容（包含既有 _record_violation best-effort 稽核記錄的
   discovered_constraint memory 寫入本身，也必須被這次修復一併堵住——見
   maintenance._record_violation 新增的 validate_project_metadata 二次檢查，
   否則「安全閥門擋下越權寫入」的修復會反過來自己在受害目錄留下一筆標記
   錯誤 project_id 的記錄，與修復目的矛盾）。
"""

from __future__ import annotations

import json
import sqlite3

import pytest

import remagraph.cli as cli_mod
from remagraph import db as db_mod
from remagraph import maintenance as maint_mod
from remagraph.maintenance import SafetyValveError


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)
    monkeypatch.delenv("REMAGRAPH_PROJECT", raising=False)
    monkeypatch.delenv("REMAGRAPH_RESTRICTED_PREFIXES", raising=False)
    home = tmp_path / "fake-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(db_mod, "DEFAULT_STATE_DIR", home / ".local" / "state" / "remagraph")
    return home


def _write_project_json(state_dir, project_id: str) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "project.json").write_text(
        json.dumps({"project_id": project_id}), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# 1. 直接單元測試 safety_validate_project()
# ---------------------------------------------------------------------------


def test_first_ever_use_of_directory_succeeds_for_any_project_id(tmp_path, monkeypatch):
    """目錄從未被使用過（無 project.json）—— 任何 project_id 第一次使用都
    必須成功，不能被新檢查誤判為衝突。"""
    state_dir = tmp_path / "brand-new-dir"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    resolved = maint_mod.safety_validate_project("anything")
    assert resolved == state_dir.resolve()


def test_directory_with_default_placeholder_metadata_succeeds(tmp_path, monkeypatch):
    """project.json 存在但內容仍是 DEFAULT_PROJECT_ID 佔位值（例如目錄剛被
    建立、尚未真正跑過 init）—— 視為未衝突，任何 project_id 都能使用。"""
    state_dir = tmp_path / "placeholder-dir"
    _write_project_json(state_dir, db_mod.DEFAULT_PROJECT_ID)
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    resolved = maint_mod.safety_validate_project("some-project")
    assert resolved == state_dir.resolve()


def test_same_project_reconnecting_to_its_own_directory_succeeds(tmp_path, monkeypatch):
    """目錄已合法用於 project "A" —— 用 project_id="A" 再次連線必須繼續
    成功（同一個 project，非衝突）。"""
    state_dir = tmp_path / "proj-a-dir"
    _write_project_json(state_dir, "A")
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    resolved = maint_mod.safety_validate_project("A")
    assert resolved == state_dir.resolve()


def test_different_project_reusing_another_projects_directory_is_rejected(
    tmp_path, monkeypatch
):
    """核心回歸測試：目錄已合法用於 project "A"，REMAGRAPH_STATE_DIR 指向
    該目錄，卻用 project_id="B" 呼叫 —— 這正是審查者重現的真實事故形狀
    （serve process 繼承了另一個 project 的 env var）。修復前：
    env_dir != configured 恆為 False，完全不會拋錯。修復後：必須被拒絕，
    reason 為新的 "project_metadata_mismatch"。"""
    state_dir = tmp_path / "proj-a-dir"
    _write_project_json(state_dir, "A")
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    with pytest.raises(SafetyValveError, match="project.json"):
        maint_mod.safety_validate_project("B")


def test_metadata_mismatch_violation_recorded_with_distinct_reason(tmp_path, monkeypatch):
    """違規記錄使用新的、可區分的 reason 字串（而非既有的
    missing_remagraph_state_dir / state_dir_mismatch /
    restricted_prefix_using_default_db
    任何一個），讓稽核記錄可明確辨識是哪一種違規。"""
    state_dir = tmp_path / "proj-a-dir"
    _write_project_json(state_dir, "A")
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    with pytest.raises(SafetyValveError):
        maint_mod.safety_validate_project("B")

    audit_files = list(state_dir.glob("audit-*.jsonl"))
    assert audit_files, "應該要有 audit log 檔案記錄這次違規"
    content = audit_files[0].read_text(encoding="utf-8")
    assert "project_metadata_mismatch" in content


def test_metadata_mismatch_does_not_write_anything_into_the_foreign_database(
    tmp_path, monkeypatch
):
    """即使 _record_violation 的 best-effort 稽核記錄路徑會嘗試寫入
    discovered_constraint memory，也絕不能真的把它寫進「屬於另一個 project」
    的 SQLite 資料庫——否則安全閥門的修復本身就會在受害目錄留下一筆標記
    錯誤 project_id 的記錄，與修復目的矛盾。允許寫入 audit-*.jsonl 純文字
    稽核日誌檔（非資料庫本身）。"""
    state_dir = tmp_path / "proj-a-dir"
    _write_project_json(state_dir, "A")
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    with pytest.raises(SafetyValveError):
        maint_mod.safety_validate_project("B")

    db_path = state_dir / "remagraph.db"
    assert not db_path.exists(), (
        "違規記錄的 best-effort memory 寫入不應該建立/寫入該目錄的 SQLite 資料庫"
    )


# ---------------------------------------------------------------------------
# 2. 端到端：透過真正的 CLI 路徑重現審查者的原始重現步驟
# ---------------------------------------------------------------------------


_LONG_SUMMARY = "這是一段足夠長的 summary 來通過仲裁規則檢查，至少需要三十個中文字元才能過關"


def test_cli_store_with_env_pointing_at_another_projects_dir_is_rejected(
    tmp_path, monkeypatch, capsys
):
    """審查者的原始重現：REMAGRAPH_STATE_DIR 指向 proj-A 的真實目錄，
    `remagraph store --project B ...` 修復前會成功執行、把資料寫進 proj-A
    標記為 project B。修復後必須被拒絕（exit 1），且 proj-A 的 SQLite
    資料庫完全沒有被建立/寫入任何內容。

    第二輪對抗式複審發現的缺口：cli.py main() 裡有一道更早的頂層守門
    （8edb739e 引入），會搶在 cmd_store 內部已修復的
    safety_validate_project() -> _record_violation() 路徑之前就攔截並
    sys.exit(1)——這代表寫入安全確實生效，但這個違規完全沒有留下 audit
    記錄（不像 `remagraph serve` 遇到同一種違規會正確寫下
    safety_violation/project_metadata_mismatch 稽核記錄）。只斷言錯誤字串
    無法區分「舊的、會 shadow 掉稽核記錄的頂層守門」跟「新的、正確走
    _record_violation 的路徑」哪一個真正觸發——因此這裡改為直接檢查
    audit-*.jsonl 是否存在且內容含有 safety_violation +
    project_metadata_mismatch，才能確認補上的稽核記錄真的到位。"""
    proj_a_dir = tmp_path / "proj-A-real-dir"
    _write_project_json(proj_a_dir, "A")
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(proj_a_dir))

    with pytest.raises(SystemExit) as ei:
        cli_mod.main(
            [
                "store",
                "--project",
                "B",
                "--task-id",
                "B-task-001",
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
    assert ei.value.code == 1

    err = capsys.readouterr().err
    assert "project.json" in err or "mismatch" in err.lower()

    db_path = proj_a_dir / "remagraph.db"
    assert not db_path.exists(), (
        "修復前的缺口：資料會悄悄寫進 proj-A 的資料庫、標記為 project B。"
        "修復後該資料庫完全不該被建立。"
    )

    audit_files = list(proj_a_dir.glob("audit-*.jsonl"))
    assert audit_files, (
        "缺口：CLI 層級的拒絕沒有留下任何 audit 記錄——跟 `remagraph serve` "
        "遇到同一種違規時會正確寫下 audit 記錄不一致。"
    )
    content = "\n".join(f.read_text(encoding="utf-8") for f in audit_files)
    assert '"action": "safety_violation"' in content
    assert "project_metadata_mismatch" in content
    assert '"project_id": "B"' in content


def test_cli_store_same_project_matching_its_own_directory_still_succeeds(
    tmp_path, monkeypatch, capsys
):
    """無 regression：REMAGRAPH_STATE_DIR 指向的目錄已合法用於 project
    "foo"，用 project_id="foo" 呼叫必須繼續成功。"""
    state_dir = tmp_path / "proj-foo-dir"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    # 第一次呼叫：目錄尚未被使用過，建立 foo 專案（模擬合法使用）。
    cli_mod.main(
        [
            "store",
            "--project",
            "foo",
            "--task-id",
            "foo-task-001",
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
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["status"] == "stored"

    conn = sqlite3.connect(str(state_dir / "remagraph.db"))
    rows = conn.execute(
        "SELECT project_id FROM memories WHERE task_id = ?", ("foo-task-001",)
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "foo"
