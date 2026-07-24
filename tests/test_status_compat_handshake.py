# SPDX-License-Identifier: Apache-2.0
"""回歸測試 —— remagraph_status 擴充為版本相容性 handshake（PPLX 架構改善計畫 item 3）。

背景：item 1（tests/test_db_migrations.py）與 item 2
（tests/test_schema_compat_tiers.py）讓 db.connect() 在資料庫 schema
落後於程式碼時做出三層判斷（完全相容 / 唯讀降級 / 硬拒絕），但呼叫端
（agent）只有在真正嘗試 remagraph_store 寫入失敗後，才會第一次得知版本
落差 —— 即使唯讀降級的情況下，也是等寫入被拒絕才知道。

item 3 讓 remagraph_status（MCP tool 與 CLI `status` 子命令）在回應中
主動附加版本相容性資訊，讓 agent 可以在 session 一開始或定期呼叫
remagraph_status 時，就提早知道自己的相容性現況，而不必等到寫入失敗。

本檔驗證：
1. tier 1（完全相容）：新欄位皆正確回傳，"latest" 不受影響。
2. tier 2（唯讀降級）：connect() 不拋例外，read_only=True，版本欄位正確
   反映實際落差，"latest" 仍能看到先前寫入的真實資料。
3. 欄位缺漏（模擬 pre-item-1 資料庫，或 v4 資料庫未走過 item 1 migration）：
   min_reader_version / min_writer_version / upgrade_hint 皆為 None，
   不得 crash，其餘欄位與 "latest" 仍正常運作。
4. tier 3（硬拒絕）：remagraph_status 透過既有的 e4acc23 例外處理，仍回傳
   乾淨的 {"status": "error", "reason": ...}，且 reason 內含資料庫存下的
   upgrade_hint 文字（延伸既有覆蓋範圍，確認本次改動未破壞該路徑）。
5. CLI `remagraph status` 子命令輸出與 MCP tool 一致，包含相同的新欄位。
"""

from __future__ import annotations

import json
import sqlite3

import pytest

import remagraph.server as server
from remagraph import db
from remagraph.models import StoreRequest
from remagraph.store import process_store


def _reset_conn() -> None:
    """重置 server.py 模組層級 DB singleton（測試間互相隔離）。"""
    if server._conn is not None:
        server._db.close(server._conn)
    server._conn = None


@pytest.fixture(autouse=True)
def setup_test_env(tmp_path, monkeypatch):
    """每個 test 使用獨立的 state 目錄並重置連線。"""
    state_dir = str(tmp_path / "state")
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", state_dir)
    _reset_conn()
    yield
    _reset_conn()


def _set_meta(updates: dict[str, str | None]) -> None:
    """直接以底層 sqlite3 連線覆寫 _meta 表欄位（模擬各種資料庫狀態）。

    None 值代表「刪除該欄位」，用來模擬欄位缺漏。做法與
    tests/test_schema_compat_tiers.py 的 _set_meta 一致。
    """
    db_path = db.get_db_path()
    conn = sqlite3.connect(db_path)
    for key, value in updates.items():
        if value is None:
            conn.execute("DELETE FROM _meta WHERE key = ?", (key,))
        else:
            conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# 1. Tier 1：完全相容
# ---------------------------------------------------------------------------


def test_status_tier1_full_compat_returns_all_new_fields():
    """全新、完全相容的資料庫：remagraph_status 應回傳所有新欄位且值正確，
    "latest" 亦正確回傳先前寫入的真實資料。"""
    store_result = server.remagraph_store(
        project_id="testproj",
        task_id="task-compat-tier1-001",
        agent_id="oc-test",
        kind="status_update",
        summary="這是一段足夠長的狀態更新摘要，用來通過仲裁規則檢查，至少需要三十個字元才行",
        learnings=["進度確認"],
    )
    assert store_result["status"] == "stored"

    result = server.remagraph_status(project_id="testproj", limit=10)

    assert result["server_code_version"] == db.SCHEMA_VERSION
    assert isinstance(result["server_code_version"], int)
    assert result["db_schema_version"] == db.SCHEMA_VERSION
    assert isinstance(result["db_schema_version"], int)
    assert result["min_reader_version"] == 1
    assert result["min_writer_version"] == db.SCHEMA_VERSION
    assert isinstance(result["upgrade_hint"], str)
    assert result["upgrade_hint"].strip() != ""
    assert result["read_only"] is False

    assert len(result["latest"]) == 1
    assert result["latest"][0]["task_id"] == "task-compat-tier1-001"


# ---------------------------------------------------------------------------
# 2. Tier 2：唯讀降級
# ---------------------------------------------------------------------------


def test_status_tier2_read_only_reports_mismatch_and_still_returns_latest():
    """min_reader_version <= SCHEMA_VERSION < min_writer_version：connect() 不
    拋例外，remagraph_status 應正確回報 read_only=True 與實際的版本落差，
    "latest" 仍正確回傳唯讀降級之前寫入的真實資料。"""
    store_result = server.remagraph_store(
        project_id="testproj",
        task_id="task-compat-tier2-001",
        agent_id="oc-test",
        kind="status_update",
        summary="這是唯讀降級情境下必須仍可見的 fixture 狀態更新摘要文字內容三十字以上",
        learnings=["fixture"],
    )
    assert store_result["status"] == "stored"
    _reset_conn()

    # 模擬「資料庫已升級到比程式碼寫入相容版本還新，但讀取仍相容」：
    # min_reader_version 維持 <= SCHEMA_VERSION，min_writer_version 提高到超過
    # SCHEMA_VERSION（與 test_schema_compat_tiers.py 的 tier 2 情境一致）。
    _set_meta(
        {
            "schema_version": str(db.SCHEMA_VERSION + 1),
            "min_reader_version": "1",
            "min_writer_version": str(db.SCHEMA_VERSION + 1),
        }
    )

    result = server.remagraph_status(project_id="testproj", limit=10)

    assert result["read_only"] is True
    assert result["server_code_version"] == db.SCHEMA_VERSION
    assert result["db_schema_version"] == db.SCHEMA_VERSION + 1
    assert result["db_schema_version"] > result["server_code_version"]
    assert result["min_reader_version"] == 1
    assert result["min_writer_version"] == db.SCHEMA_VERSION + 1

    matching = [s for s in result["latest"] if s["task_id"] == "task-compat-tier2-001"]
    assert len(matching) == 1
    assert "fixture 狀態更新" in matching[0]["summary"]


# ---------------------------------------------------------------------------
# 3. 欄位缺漏（模擬 pre-item-1 資料庫，或 v4 資料庫未走過 item 1 migration）
# ---------------------------------------------------------------------------


def test_status_missing_forward_compat_fields_does_not_crash():
    """min_reader_version / min_writer_version / upgrade_hint 皆缺漏時（例如
    schema_version 本身相容，但資料庫是 item 1 之前建立、從未走過
    _migrate_v4_to_v5 補欄位的路徑）：remagraph_status 不得 crash，缺漏欄位
    一律回傳 None，其餘欄位與 "latest" 仍正常運作。"""
    server.remagraph_store(
        project_id="testproj",
        task_id="task-compat-missing-001",
        agent_id="oc-test",
        kind="status_update",
        summary="這是欄位缺漏情境下必須仍可見的 fixture 狀態更新摘要文字內容三十字以上",
        learnings=["fixture"],
    )
    _reset_conn()

    _set_meta(
        {
            "min_reader_version": None,
            "min_writer_version": None,
            "upgrade_hint": None,
        }
    )

    result = server.remagraph_status(project_id="testproj", limit=10)

    assert result["server_code_version"] == db.SCHEMA_VERSION
    assert result["db_schema_version"] == db.SCHEMA_VERSION
    assert result["min_reader_version"] is None
    assert result["min_writer_version"] is None
    assert result["upgrade_hint"] is None
    assert result["read_only"] is False

    assert len(result["latest"]) == 1
    assert result["latest"][0]["task_id"] == "task-compat-missing-001"


# ---------------------------------------------------------------------------
# 4. Tier 3：硬拒絕（延伸 e4acc23 既有覆蓋範圍，確認本次改動未破壞該路徑）
# ---------------------------------------------------------------------------


def test_status_tier3_hard_reject_returns_clean_error_with_upgrade_hint():
    """SCHEMA_VERSION < min_reader_version：connect() 應拋出 MigrationError，
    remagraph_status 透過既有的例外處理（commit e4acc23）回傳乾淨的
    {"status": "error", "reason": ...} 結構，reason 內含資料庫存下的
    upgrade_hint 文字，不受本次新增欄位的改動影響。"""
    conn0 = db.connect()
    conn0.close()

    stored_hint = "TEST-ONLY-TIER3-UPGRADE-HINT-請升級 remagraph 套件版本後再重新連線"
    _set_meta(
        {
            "schema_version": str(db.SCHEMA_VERSION + 1),
            "min_reader_version": str(db.SCHEMA_VERSION + 1),
            "min_writer_version": str(db.SCHEMA_VERSION + 1),
            "upgrade_hint": stored_hint,
        }
    )

    result = server.remagraph_status(project_id="testproj", limit=10)

    assert result["status"] == "error"
    assert stored_hint in result["reason"]
    # 硬拒絕路徑不應含任何本次新增的相容性欄位（回應結構維持既有的錯誤格式）。
    assert "server_code_version" not in result
    assert "read_only" not in result


# ---------------------------------------------------------------------------
# 5. CLI `remagraph status` 子命令與 MCP tool 一致
# ---------------------------------------------------------------------------


def test_cli_status_subcommand_includes_same_compat_fields(capsys):
    """CLI `remagraph status` 子命令輸出應包含與 MCP tool 相同的新欄位。"""
    from remagraph.cli import build_parser, cmd_status

    conn = db.connect()
    process_store(
        StoreRequest(
            project_id="testproj",
            task_id="task-cli-status-compat-001",
            agent_id="agent-a",
            kind="status_update",
            summary="這是一段足夠長的狀態更新摘要文字，用來通過仲裁規則的三十字元門檻檢查",
            learnings=["x"],
        ),
        conn,
    )
    db.close(conn)

    args = build_parser().parse_args(
        ["status", "--project", "testproj", "--limit", "10"]
    )
    cmd_status(args)
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)

    assert payload["server_code_version"] == db.SCHEMA_VERSION
    assert payload["db_schema_version"] == db.SCHEMA_VERSION
    assert payload["min_reader_version"] == 1
    assert payload["min_writer_version"] == db.SCHEMA_VERSION
    assert isinstance(payload["upgrade_hint"], str)
    assert payload["upgrade_hint"].strip() != ""
    assert payload["read_only"] is False

    assert len(payload["latest"]) == 1
    assert payload["latest"][0]["task_id"] == "task-cli-status-compat-001"
