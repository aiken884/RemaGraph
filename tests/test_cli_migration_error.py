# SPDX-License-Identifier: Apache-2.0
"""回歸測試 —— CLI 子命令對 MigrationError（tier-3 硬拒絕）的例外處理一致性。

背景：commit e4acc23 讓 MCP tools（remagraph_store/remagraph_search/
remagraph_status，見 tests/test_server.py 的
test_*_returns_clean_error_when_get_conn_raises_migration_error）在
_get_conn() 拋出 MigrationError 時回傳乾淨的
{"status": "error", "reason": str(e)} 結構，而非讓例外原樣往外傳。

但 src/remagraph/cli.py 對應的 CLI 子命令（cmd_store / cmd_search /
cmd_status）從未套用同等處理 —— _get_conn() 拋出的例外會以原始、未捕捉的
traceback 洩漏給終端使用者（追蹤事項 #19：CLI status 缺少 try/except，
tier-3 硬拒絕會顯示原始 traceback）。

本檔驗證修復後：
1. 三個受影響的 cmd_*（store/search/status）在 _get_conn() 拋出
   MigrationError 時，皆不再讓例外原樣往外傳（不 crash 成未捕捉的
   traceback）。
2. 皆將完整、不截斷的例外訊息印到 stderr（MigrationError 訊息內含
   多行、可操作的升級指引，必須完整送達使用者）。
3. 皆以非 0 狀態碼結束（SystemExit），與本檔既有 cmd_maintain 的錯誤
   處理慣例一致（`print(f"ERROR: ... - {e}", file=sys.stderr);
   sys.exit(1)`）。
4. 修復本身是純加法變更 —— 不影響任三個命令成功路徑的輸出格式（欄位、
   內容皆與修復前一致）。
"""

from __future__ import annotations

import json

import pytest

import remagraph.cli as cli
from remagraph.db import MigrationError
from remagraph.models import StoreRequest
from remagraph.store import process_store

_MIGRATION_ERROR_MESSAGE = (
    "資料庫 schema_version=99 比程式碼的 SCHEMA_VERSION=4 還新，無法降級。"
    "請選擇以下其一處理："
    "1) 更新已安裝的 remagraph 套件至相容此 schema 版本的版本；"
    "2) 設定 REMAGRAPH_STATE_DIR 指向另一個獨立目錄，改用全新資料庫；"
    "3) 若確認可捨棄此資料庫的既有資料，刪除該 state_dir 下的 "
    "remagraph.db 後重新初始化。"
)


def _raise_migration_error(project_id: str | None = None) -> None:
    raise MigrationError(_MIGRATION_ERROR_MESSAGE)


@pytest.fixture
def state_env(tmp_path, monkeypatch):
    """每個 test 使用獨立的 state 目錄，避免互相汙染。"""
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path / "state"))
    return tmp_path


# ---------------------------------------------------------------------------
# 失敗路徑：_get_conn() 拋出 MigrationError
# ---------------------------------------------------------------------------


def test_cmd_store_exits_nonzero_and_prints_full_error_on_migration_error(
    state_env, monkeypatch, capsys
):
    """cmd_store：_get_conn() 拋出 MigrationError 時不得 crash，須完整印出
    例外訊息到 stderr 並以非 0 狀態碼結束。"""
    monkeypatch.setattr(cli, "_get_conn", _raise_migration_error)
    args = cli.build_parser().parse_args(
        [
            "store",
            "--task-id",
            "task-mig-001",
            "--agent-id",
            "agent-a",
            "--kind",
            "status_update",
            "--summary",
            "這是一段足夠長的 summary 來通過仲裁規則檢查，至少需要三十個中文字元才能過關",
        ]
    )

    with pytest.raises(SystemExit) as ei:
        cli.cmd_store(args)

    assert ei.value.code != 0
    captured = capsys.readouterr()
    assert _MIGRATION_ERROR_MESSAGE in captured.err
    assert captured.out == ""


def test_cmd_search_exits_nonzero_and_prints_full_error_on_migration_error(
    state_env, monkeypatch, capsys
):
    """cmd_search：_get_conn() 拋出 MigrationError 時不得 crash，須完整印出
    例外訊息到 stderr 並以非 0 狀態碼結束。"""
    monkeypatch.setattr(cli, "_get_conn", _raise_migration_error)
    args = cli.build_parser().parse_args(["search", "--query", "測試查詢"])

    with pytest.raises(SystemExit) as ei:
        cli.cmd_search(args)

    assert ei.value.code != 0
    captured = capsys.readouterr()
    assert _MIGRATION_ERROR_MESSAGE in captured.err
    assert captured.out == ""


def test_cmd_status_exits_nonzero_and_prints_full_error_on_migration_error(
    state_env, monkeypatch, capsys
):
    """cmd_status：_get_conn() 拋出 MigrationError 時不得 crash，須完整印出
    例外訊息到 stderr 並以非 0 狀態碼結束（此為追蹤事項 #19 描述的原始
    bug：修復前這裡會讓 MigrationError 原樣往外傳，顯示未捕捉的
    traceback）。"""
    monkeypatch.setattr(cli, "_get_conn", _raise_migration_error)
    args = cli.build_parser().parse_args(["status", "--limit", "10"])

    with pytest.raises(SystemExit) as ei:
        cli.cmd_status(args)

    assert ei.value.code != 0
    captured = capsys.readouterr()
    assert _MIGRATION_ERROR_MESSAGE in captured.err
    assert captured.out == ""


# ---------------------------------------------------------------------------
# 成功路徑：修復不得改變既有輸出格式（純加法變更，只補失敗路徑）
# ---------------------------------------------------------------------------


def test_cmd_store_success_path_unchanged(state_env, capsys):
    """修復後，cmd_store 成功路徑的輸出欄位與內容仍與修復前一致。"""
    args = cli.build_parser().parse_args(
        [
            "store",
            "--task-id",
            "task-mig-ok-001",
            "--agent-id",
            "agent-a",
            "--kind",
            "task_handoff",
            "--summary",
            "這是一段足夠長的 summary 來通過仲裁規則檢查，至少需要三十個中文字元才能過關",
            "--learnings",
            '["學到了重要的事情"]',
            "--handoff-note",
            "這是一段給接手者的交接筆記，至少要二十個字以上才算夠長",
        ]
    )

    cli.cmd_store(args)

    captured = capsys.readouterr()
    # 註：不斷言 stderr 完全為空 —— 首次呼叫可能觸發 embedding 模型下載進度
    # 訊息（與本次修復無關的環境雜訊），此處只關心成功路徑的輸出格式不變。
    payload = json.loads(captured.out.strip())
    assert payload["status"] == "stored"
    assert payload["id"].startswith("mem-")
    assert payload["superseded"] == []
    assert payload["invalidated_count"] == 0


def test_cmd_search_success_path_unchanged(state_env, capsys):
    """修復後，cmd_search 成功路徑的輸出欄位與內容仍與修復前一致。"""
    from remagraph import db as db_mod

    conn = db_mod.connect()
    process_store(
        StoreRequest(
            project_id="default",
            task_id="task-mig-ok-002",
            agent_id="agent-a",
            kind="status_update",
            summary="這是一段足夠長的摘要，用來通過仲裁規則，至少需要三十個字元以上才行",
            learnings=["x"],
        ),
        conn,
    )
    db_mod.close(conn)

    args = cli.build_parser().parse_args(["search", "--task-id", "task-mig-ok-002"])

    cli.cmd_search(args)

    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip())
    assert "results" in payload
    assert "has_more" in payload
    assert any(r["task_id"] == "task-mig-ok-002" for r in payload["results"])


def test_cmd_status_success_path_unchanged(state_env, capsys):
    """修復後，cmd_status 成功路徑的輸出欄位與內容仍與修復前一致（含既有的
    版本相容性 handshake 欄位）。"""
    from remagraph import db as db_mod

    conn = db_mod.connect()
    process_store(
        StoreRequest(
            project_id="default",
            task_id="task-mig-ok-003",
            agent_id="agent-a",
            kind="status_update",
            summary="這是一段足夠長的摘要，用來通過仲裁規則，至少需要三十個字元以上才行",
            learnings=["x"],
        ),
        conn,
    )
    db_mod.close(conn)

    args = cli.build_parser().parse_args(["status", "--limit", "10"])

    cli.cmd_status(args)

    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip())
    assert "latest" in payload
    assert any(item["task_id"] == "task-mig-ok-003" for item in payload["latest"])
    assert "server_code_version" in payload
    assert "read_only" in payload
    assert payload["read_only"] is False
