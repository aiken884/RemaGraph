# SPDX-License-Identifier: Apache-2.0
"""Regression tests for BUG 1 (P0), `remagraph serve` 專項：serve 啟動時必須
明確綁定單一 project（PPLX 架構審查共識）。

背景（已讀碼確認）：`remagraph serve` 修復前完全沒有參數解析——
`server.main()` 的 `cli_commands` tuple 不含 "serve"，任何呼叫（包括
`remagraph serve --project foo`）一律落入 else 分支直接
`mcp.run(transport="stdio")`，`--project foo` 被完全忽略、不做任何驗證，
且每個工具呼叫各自帶的 project_id 之間彼此毫無關聯的一致性檢查——這正是
真實事故的成因：一個 serve 行程在繼承了『別的專案』REMAGRAPH_STATE_DIR
的環境下啟動，之後每個工具呼叫都悄悄寫入錯誤的資料庫，且無法從行程本身
察覺。

本檔驗證修復後（PPLX 共識設計，明確拒絕動態多專案路由，見
server._run_serve/_bind_project docstring）：
1. 啟動時 --project 與 REMAGRAPH_PROJECT 環境變數皆缺席 → 快速失敗
   （非 0 exit code），且 MCP stdio 迴圈完全不會啟動。
2. 明確 --project 提供 → 成功綁定。
3. REMAGRAPH_PROJECT 環境變數提供（--project 缺席）→ 成功綁定。
4. 綁定後，工具呼叫帶入與綁定值不同的明確 project_id → 回傳結構化錯誤，
   不靜默沿用目前連線、不 crash。
5. 綁定後，工具呼叫省略 project_id（None）→ 不視為 mismatch，維持既有的
   all_projects/eff_project 語意（search/status）。
6. 唯讀降級模式下啟動 → 印出啟動警告，且 remagraph_store 主動、及早拒絕
   寫入（讀相容、可搜尋/查狀態）。
7. liveness check：連線建立後底層資料庫檔案被移除，下一次 _get_conn() 須
   拋出清楚的錯誤，而非讓某次寫入以難以理解的底層錯誤悄悄失敗。

MCP stdio 迴圈本身（mcp.run）在所有測試中一律 monkeypatch 成不會真的阻塞
的 stub，只記錄是否被呼叫過。
"""

from __future__ import annotations

import shutil
import sqlite3
from unittest.mock import MagicMock

import pytest

import remagraph.server as server
from remagraph import db as db_mod


def _reset_server_state():
    if server._conn is not None:
        try:
            server._db.close(server._conn)
        except Exception:
            pass
    server._conn = None
    server._bound_project_id = None
    server._bound_db_path = None


@pytest.fixture(autouse=True)
def setup_test_env(tmp_path, monkeypatch):
    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)
    monkeypatch.delenv("REMAGRAPH_PROJECT", raising=False)
    _reset_server_state()
    yield
    _reset_server_state()


@pytest.fixture
def no_op_mcp_run(monkeypatch):
    """避免真的進入 mcp.run(transport="stdio") 的阻塞 stdio 迴圈。"""
    stub = MagicMock()
    monkeypatch.setattr(server.mcp, "run", stub)
    return stub


_LONG_SUMMARY = "這是一段足夠長的 summary 來通過仲裁規則檢查，至少需要三十個中文字元才能過關"


# ---------------------------------------------------------------------------
# 1. 完全缺席 --project / REMAGRAPH_PROJECT → 快速失敗，MCP 迴圈不啟動
# ---------------------------------------------------------------------------


def test_run_serve_fails_fast_when_no_project_bound(no_op_mcp_run, capsys):
    with pytest.raises(SystemExit) as ei:
        server._run_serve([])
    assert ei.value.code != 0

    err = capsys.readouterr().err
    assert "project" in err.lower() or "--project" in err

    no_op_mcp_run.assert_not_called()
    assert server._bound_project_id is None
    assert server._conn is None


# ---------------------------------------------------------------------------
# 2. 明確 --project 提供 → 成功綁定
# ---------------------------------------------------------------------------


def test_run_serve_binds_via_explicit_project_flag(tmp_path, monkeypatch, no_op_mcp_run, capsys):
    state_dir = tmp_path / "proj-a-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    server._run_serve(["--project", "proj-a"])

    assert server._bound_project_id == "proj-a"
    assert server._conn is not None
    no_op_mcp_run.assert_called_once_with(transport="stdio")

    err = capsys.readouterr().err
    assert "proj-a" in err
    assert str(state_dir.resolve()) in err or "state_dir" in err


def test_run_serve_binds_via_project_flag_equals_form(tmp_path, monkeypatch, no_op_mcp_run):
    state_dir = tmp_path / "proj-eq-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    server._run_serve(["--project=proj-eq"])

    assert server._bound_project_id == "proj-eq"
    no_op_mcp_run.assert_called_once_with(transport="stdio")


# ---------------------------------------------------------------------------
# 3. REMAGRAPH_PROJECT 環境變數（--project 缺席）→ 成功綁定
# ---------------------------------------------------------------------------


def test_run_serve_binds_via_env_var(tmp_path, monkeypatch, no_op_mcp_run):
    state_dir = tmp_path / "proj-env-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "proj-env")

    server._run_serve([])

    assert server._bound_project_id == "proj-env"
    no_op_mcp_run.assert_called_once_with(transport="stdio")


def test_run_serve_explicit_flag_takes_precedence_over_env(tmp_path, monkeypatch, no_op_mcp_run):
    state_dir = tmp_path / "proj-flag-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "proj-env-should-lose")

    server._run_serve(["--project", "proj-flag-wins"])

    assert server._bound_project_id == "proj-flag-wins"


# ---------------------------------------------------------------------------
# 3b. 啟動時安全閥門失敗（例如 herdr-* 對 basename 'remagraph' 的目錄）→
#     _run_serve 快速失敗，MCP 迴圈不啟動（沿用既有 safety_validate_project）
# ---------------------------------------------------------------------------


def test_run_serve_fails_fast_when_safety_valve_rejects_at_startup(
    tmp_path, monkeypatch, no_op_mcp_run, capsys
):
    state_dir = tmp_path / "state" / "remagraph"  # basename 'remagraph'
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    with pytest.raises(SystemExit) as ei:
        server._run_serve(["--project", "herdr-foo"])
    assert ei.value.code != 0

    no_op_mcp_run.assert_not_called()
    assert server._conn is None
    err = capsys.readouterr().err
    assert "herdr" in err


def test_run_serve_prints_mismatch_warning_before_startup_failure(
    tmp_path, monkeypatch, no_op_mcp_run, capsys
):
    """PPLX 共識 edge case：若診斷邏輯發現目前 REMAGRAPH_STATE_DIR 與解析出的
    state_dir 不同，須在 safety_validate_project 的例外往外傳之前，先印出
    『為什麼』的警告。

    注意：目前 maintenance.resolve_project_state_dir() 在 REMAGRAPH_STATE_DIR
    已設定時，一律直接回傳該 env 值本身（見該函式實作），因此透過真實呼叫
    無法讓 resolve 出的值與 env 產生分歧——這裡直接 monkeypatch
    resolve_project_state_dir 本身，單獨驗證 _bind_project 的警告列印邏輯，
    不依賴 resolve_project_state_dir 未來是否會改變這個既有行為。
    """
    state_dir = tmp_path / "actual-env-dir"
    other_dir = tmp_path / "resolved-elsewhere"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    monkeypatch.setattr(server.maintenance, "resolve_project_state_dir", lambda pid: other_dir)

    with pytest.raises(SystemExit):
        server._run_serve(["--project", "proj-mismatch-diag"])

    err = capsys.readouterr().err
    assert "WARNING" in err or "警告" in err
    assert str(other_dir) in err or str(state_dir) in err


# ---------------------------------------------------------------------------
# 4. 綁定後，工具呼叫帶入不同的明確 project_id → 結構化錯誤，不靜默沿用連線
# ---------------------------------------------------------------------------


def test_store_rejects_call_with_mismatched_project_id_after_binding(
    tmp_path, monkeypatch, no_op_mcp_run
):
    state_dir = tmp_path / "bound-proj-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    server._run_serve(["--project", "bound-proj"])

    result = server.remagraph_store(
        project_id="other-proj",
        task_id="task-mismatch-001",
        agent_id="agent-1",
        kind="status_update",
        summary=_LONG_SUMMARY,
        learnings=["a"],
    )

    assert result["status"] == "error"
    assert result["reason"] == "project_mismatch"
    assert "bound-proj" in result["detail"]
    assert "other-proj" in result["detail"]

    # 沒有任何資料被寫入目前綁定的連線
    count = server._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    assert count == 0


def test_search_rejects_call_with_mismatched_project_id_after_binding(
    tmp_path, monkeypatch, no_op_mcp_run
):
    state_dir = tmp_path / "bound-proj-search-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    server._run_serve(["--project", "bound-proj-search"])

    result = server.remagraph_search(query="hello", project_id="other-proj")

    assert result["status"] == "error"
    assert result["reason"] == "project_mismatch"


def test_status_rejects_call_with_mismatched_project_id_after_binding(
    tmp_path, monkeypatch, no_op_mcp_run
):
    state_dir = tmp_path / "bound-proj-status-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    server._run_serve(["--project", "bound-proj-status"])

    result = server.remagraph_status(project_id="other-proj")

    assert result["status"] == "error"
    assert result["reason"] == "project_mismatch"


def test_store_succeeds_with_matching_project_id_after_binding(
    tmp_path, monkeypatch, no_op_mcp_run
):
    state_dir = tmp_path / "bound-proj-match-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    server._run_serve(["--project", "bound-proj-match"])

    result = server.remagraph_store(
        project_id="bound-proj-match",
        task_id="task-match-001",
        agent_id="agent-1",
        kind="status_update",
        summary=_LONG_SUMMARY,
        learnings=["a"],
    )

    assert result["status"] == "stored"


# ---------------------------------------------------------------------------
# 5. 綁定後，工具呼叫省略 project_id（None）→ 不視為 mismatch
# ---------------------------------------------------------------------------


def test_search_with_none_project_id_after_binding_is_not_a_mismatch(
    tmp_path, monkeypatch, no_op_mcp_run
):
    state_dir = tmp_path / "bound-proj-none-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    server._run_serve(["--project", "bound-proj-none"])

    result = server.remagraph_search(query="AB")

    # 短查詢正常空結果路徑（非 mismatch 錯誤），證明 project_id=None 完全
    # 沒有觸發 mismatch 檢查。
    assert "results" in result
    assert "reason" not in result or result.get("reason") != "project_mismatch"


def test_status_with_none_project_id_after_binding_is_not_a_mismatch(
    tmp_path, monkeypatch, no_op_mcp_run
):
    state_dir = tmp_path / "bound-proj-status-none-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    server._run_serve(["--project", "bound-proj-status-none"])

    result = server.remagraph_status()

    assert "latest" in result


# ---------------------------------------------------------------------------
# 6. 唯讀降級模式下啟動 → 印出啟動警告，remagraph_store 主動、及早拒絕寫入
# ---------------------------------------------------------------------------


def _force_read_only_upgrade(state_dir) -> None:
    """比照 tests/test_schema_compat_tiers.py 的手法：直接覆寫 _meta 表，
    模擬『資料庫已升級到超出本程式碼寫入相容版本，但讀取仍相容』的狀態。
    """
    db_path = db_mod.get_db_path(state_dir)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', ?)",
        (str(db_mod.SCHEMA_VERSION + 1),),
    )
    conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES ('min_reader_version', '1')")
    conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('min_writer_version', ?)",
        (str(db_mod.SCHEMA_VERSION + 1),),
    )
    conn.commit()
    conn.close()


def test_run_serve_warns_on_startup_when_connection_is_read_only(
    tmp_path, monkeypatch, no_op_mcp_run, capsys
):
    state_dir = tmp_path / "ro-proj-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    # 先用一般連線建立 schema，再升版模擬唯讀降級。
    bootstrap_conn = db_mod.connect(project_id="ro-proj")
    bootstrap_conn.close()
    _force_read_only_upgrade(state_dir)
    _reset_server_state()

    server._run_serve(["--project", "ro-proj"])

    assert getattr(server._conn, db_mod.READ_ONLY_ATTR, False) is True
    err = capsys.readouterr().err
    assert "read" in err.lower() or "唯讀" in err

    result = server.remagraph_store(
        project_id="ro-proj",
        task_id="task-ro-001",
        agent_id="agent-1",
        kind="status_update",
        summary=_LONG_SUMMARY,
        learnings=["a"],
    )
    assert result["status"] == "rejected"
    assert result["reason"] == "read_only_mode"

    # search/status 仍應正常運作（唯讀只擋寫入）。
    search_result = server.remagraph_search(query="", project_id="ro-proj")
    assert "results" in search_result
    status_result = server.remagraph_status(project_id="ro-proj")
    assert "latest" in status_result


# ---------------------------------------------------------------------------
# 7. liveness check：連線建立後底層資料庫檔案被移除
# ---------------------------------------------------------------------------


def test_get_conn_raises_clear_error_when_underlying_connection_is_dead(
    tmp_path, monkeypatch, no_op_mcp_run
):
    state_dir = tmp_path / "dead-conn-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    server._run_serve(["--project", "dead-conn-proj"])

    # 模擬連線失效（底層檔案控制代碼被關閉，而非透過 server._safe_close）。
    server._conn.close()

    with pytest.raises(RuntimeError, match="重新啟動|restart"):
        server._get_conn()


def test_get_conn_detects_state_dir_deleted_out_from_under_a_live_connection(
    tmp_path, monkeypatch, no_op_mcp_run
):
    """獨立對抗式審查發現：單靠 `SELECT 1` 無法偵測『資料庫檔案所在目錄被
    整個移除/搬移』這個情境——POSIX 對已被 unlink 的 inode 仍保留有效的
    file descriptor，SELECT 1 對一個孤兒 inode 依然會成功執行。修復前，
    這個情境下 `_get_conn()`/`remagraph_store()` 都會悄悄『成功』，寫入
    一個已經找不到路徑的檔案。

    與上一個測試的差異（審查者的重點）：上一個測試只是關閉 Python 的
    sqlite3.Connection 物件本身，並未真的移除底層檔案，不代表這裡要測的
    真實情境；本測試改用 shutil.rmtree() 真的把整個 state_dir 目錄砍掉。
    """
    state_dir = tmp_path / "deleted-state-dir"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    server._run_serve(["--project", "deleted-proj"])

    # sanity check：連線目前確實活著、檔案確實存在。
    server._get_conn().execute("SELECT 1")
    assert server._bound_db_path is not None
    assert server._bound_db_path.exists()

    # 真正重現：把整個 state_dir 目錄搬移/刪除掉（而非只是關閉 Python 連線
    # 物件）——連線本身的 Python 物件與底層 fd 完全沒有被關閉。
    shutil.rmtree(state_dir)

    with pytest.raises(RuntimeError, match="移除|搬移|removed|moved"):
        server._get_conn()

    # 透過實際會呼叫 _get_conn() 的工具 handler 驗證同一件事：修復前這裡
    # 會悄悄回傳 status="stored"（寫入一個已經找不到路徑的孤兒 inode）；
    # 修復後必須回傳結構化的 error，而不是假裝成功。
    result = server.remagraph_store(
        project_id="deleted-proj",
        task_id="deleted-proj-task-001",
        agent_id="agent-1",
        kind="status_update",
        summary="這是一段足夠長的 summary 來通過仲裁規則檢查，至少需要三十個字元",
        learnings=["a"],
    )
    assert result["status"] == "error"
    assert "移除" in result["reason"] or "removed" in result["reason"].lower()
