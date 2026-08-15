# SPDX-License-Identifier: Apache-2.0
"""全專案診斷（批次 4，Python 部分）的回歸測試。

涵蓋六個診斷發現：
1. 受限前綴安全閥用 `configured.name == "remagraph"` 純字串比較判斷
   default DB：macOS 大小寫不敏感 FS 上 `REMAGRAPH` 變體物理上就是同一個
   目錄卻通過閥門；反之任何 basename 恰為 remagraph 的自訂目錄被誤殺。
2. safety_validate_project 第一行 resolve_project_state_dir 的副作用會把
   (project_id, env_dir) 無條件 upsert 進共用 registry——然後才做 metadata
   檢查；env 繼承錯誤的違規呼叫雖被 raise 擋下寫入，registry 裡該 project
   的正確映射已被污染，後續 cross-project fan-out 會打開錯誤專案的 DB。
3. 裸環境（無 REMAGRAPH_* env）下 `remagraph store` 的 SafetyValveError
   以未捕捉的完整 Python traceback 外洩（cmd_store 對 process_store 沒有
   try/except）。
4. append_audit 用 json.dump 直接串流寫檔：序列化中途失敗時已寫入 buffer
   的半殘 JSON 片段仍會落檔，且 TypeError/ValueError 不在 except 內、往外
   拋，違反「audit 寫入失敗不中斷主流程」的契約（append_event 已為同一
   問題改為先 json.dumps，append_audit 沒有套用同一修法）。
5. remagraph_maintain MCP tool 未做 project binding 檢查——綁定 A 的
   serve 行程可以 B 的名義對 A 的資料庫執行 prune/vacuum。
6. invalidate_constraints 的 UPDATE 帶 status='active' 條件，驗證卻不查
   status：對已 superseded/invalidated 的 constraint 請求會通過驗證、
   更新 0 筆，回傳的 invalidated_ids 卻列出全部請求 id。
"""

from __future__ import annotations

import json
import sqlite3

import pytest

import remagraph.audit as audit_mod
import remagraph.server as server
from remagraph import db as db_mod
from remagraph.arbitration import ArbitrationResult, invalidate_constraints
from remagraph.cli import main as cli_main
from remagraph.db import _init_schema
from remagraph.maintenance import SafetyValveError, safety_validate_project
from remagraph.models import StoreRequest, StoreResponse


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)
    monkeypatch.delenv("REMAGRAPH_PROJECT", raising=False)
    monkeypatch.delenv("REMAGRAPH_RESTRICTED_PREFIXES", raising=False)
    home = tmp_path / "fake-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        db_mod, "DEFAULT_STATE_DIR", home / ".local" / "state" / "remagraph"
    )
    return home


# ---------------------------------------------------------------------------
# 1. 受限前綴閥門的 default DB 判斷
# ---------------------------------------------------------------------------


class TestRestrictedPrefixDefaultDbDetection:
    def test_case_variant_of_default_dir_is_blocked(self, monkeypatch, tmp_path):
        """REMAGRAPH_STATE_DIR 指向 default dir 的大小寫變體時，受限前綴
        專案必須被擋下（macOS 上該變體物理上就是同一個目錄）。"""
        default_dir = db_mod.DEFAULT_STATE_DIR
        default_dir.mkdir(parents=True)
        variant = default_dir.parent / "REMAGRAPH"
        monkeypatch.setenv("REMAGRAPH_RESTRICTED_PREFIXES", "sec-")
        monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(variant))
        with pytest.raises(SafetyValveError):
            safety_validate_project("sec-proj")

    def test_physically_same_dir_with_different_name_is_blocked(
        self, monkeypatch, tmp_path
    ):
        """名稱完全不同、但（透過 symlink）物理上就是 default dir 的路徑，
        也必須被攔下（samefile 防線）。"""
        default_dir = db_mod.DEFAULT_STATE_DIR
        default_dir.mkdir(parents=True)
        alias = tmp_path / "innocent-looking-dir"
        alias.symlink_to(default_dir)
        monkeypatch.setenv("REMAGRAPH_RESTRICTED_PREFIXES", "sec-")
        monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(alias))
        with pytest.raises(SafetyValveError):
            safety_validate_project("sec-proj")


# ---------------------------------------------------------------------------
# 2. registry 污染
# ---------------------------------------------------------------------------


def test_failed_validation_does_not_clobber_registry(monkeypatch, tmp_path):
    """env 繼承錯誤（指向別的專案目錄）的違規呼叫被 raise 擋下後，registry
    裡該 project 原本正確的 state_dir 映射不得被覆寫。"""
    dir_a = tmp_path / "state-a"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(dir_a))
    conn = db_mod.connect(project_id="proj-a")  # 合法登記 proj-a → dir_a
    conn.close()
    assert db_mod.get_registered_state_dir("proj-a") == str(dir_a.resolve())

    # 另一個專案 proj-b 的目錄（有自己的 project.json）
    dir_b = tmp_path / "state-b"
    dir_b.mkdir()
    (dir_b / "project.json").write_text(
        json.dumps({"project_id": "proj-b", "state_dir": str(dir_b)}),
        encoding="utf-8",
    )

    # 違規情境：process 繼承了 proj-b 的 REMAGRAPH_STATE_DIR，卻帶著
    # proj-a 的 project_id 呼叫——必須 raise，且不得污染 registry。
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(dir_b))
    with pytest.raises(SafetyValveError):
        safety_validate_project("proj-a")

    assert db_mod.get_registered_state_dir("proj-a") == str(dir_a.resolve()), (
        "違規呼叫污染了 registry：proj-a 的映射被覆寫成 proj-b 的目錄，"
        "後續 cross-project fan-out 會打開錯誤專案的資料庫"
    )


# ---------------------------------------------------------------------------
# 3. 裸環境 cmd_store 的乾淨錯誤
# ---------------------------------------------------------------------------


def test_bare_env_store_exits_cleanly_without_traceback(capsys):
    with pytest.raises(SystemExit) as exc:
        cli_main(
            [
                "store",
                "--task-id", "t-1",
                "--agent-id", "agent-x",
                "--kind", "status_update",
                "--summary", "一筆長度足夠通過仲裁下限的測試 summary 內容填充填充",
            ]
        )
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "ERROR:" in err
    assert "Traceback" not in err


# ---------------------------------------------------------------------------
# 4. append_audit 序列化保護
# ---------------------------------------------------------------------------


def test_append_audit_swallows_serialization_failure(monkeypatch, tmp_path):
    state_dir = tmp_path / "audit-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    def broken_dumps(*args, **kwargs):
        raise TypeError("not serializable")

    monkeypatch.setattr(audit_mod.json, "dumps", broken_dumps)

    request = StoreRequest(
        project_id="p", task_id="t-1", agent_id="agent-x", kind="status_update",
        summary="一筆長度足夠通過仲裁下限的測試 summary 內容填充填充",
        learnings=["x"], handoff_note="", tags=[],
    )
    response = StoreResponse(status="stored", id="mem-1")

    audit_mod.append_audit(response, request)  # 修復前：json.dump 照樣寫檔

    audit_files = list(state_dir.glob("audit-*.jsonl")) if state_dir.exists() else []
    contents = "".join(p.read_text(encoding="utf-8") for p in audit_files)
    assert contents == "", (
        "append_audit 未套用 append_event 的先序列化保護（序列化失敗仍寫檔）"
    )


# ---------------------------------------------------------------------------
# 5. remagraph_maintain 的 project binding 檢查
# ---------------------------------------------------------------------------


def test_remagraph_maintain_rejects_mismatched_project(monkeypatch):
    monkeypatch.setattr(server, "_bound_project_id", "proj-a")
    result = server.remagraph_maintain(project_id="proj-b")
    assert result["status"] == "error"
    assert result["reason"] == "project_mismatch"


# ---------------------------------------------------------------------------
# 6. invalidate_constraints 回報一致性
# ---------------------------------------------------------------------------


def test_invalidate_non_active_constraint_is_rejected_not_misreported():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    now = "2026-07-24T00:00:00Z"
    conn.execute(
        "INSERT INTO memories (id, project_id, kind, task_id, agent_id, timestamp, "
        "summary, learnings, handoff_note, tags, status, created_at, updated_at) "
        "VALUES ('mem-c1', 'default', 'discovered_constraint', 't', 'a', ?, "
        "'constraint summary long enough for arbitration', '[]', '', '[]', "
        "'invalidated', ?, ?)",
        (now, now, now),
    )
    result = invalidate_constraints(["mem-c1"], conn)
    # 已非 active 的 constraint：必須明確拒絕（ArbitrationResult），而不是
    # 「驗證通過、更新 0 筆、invalidated_ids 卻列出它」的矛盾回報。
    assert isinstance(result, ArbitrationResult)
    assert result.passed is False
    conn.close()
