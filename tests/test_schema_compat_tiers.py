# SPDX-License-Identifier: Apache-2.0
"""回歸測試 —— schema 版本相容性三層判斷（PPLX 架構改善計畫 item 2）。

背景：item 1（已提交，見 tests/test_db_migrations.py）在 _meta 表種下
min_reader_version / min_writer_version / upgrade_hint 三個欄位，但降級
拒絕行為本身仍是全有全無 —— 只要 schema_version 比程式碼的 SCHEMA_VERSION
新，就無條件 raise MigrationError，即使程式碼其實仍安全相容讀取（甚至寫入）。

本檔驗證 item 2 實作的三層判斷（比較程式碼的 SCHEMA_VERSION 與資料庫的
min_reader_version / min_writer_version）：

1. SCHEMA_VERSION >= 資料庫 min_writer_version
   → 完全相容：connect() 正常、不標記唯讀、一般寫入透過 process_store()
     照常成功（本檔測試）。
2. min_reader_version <= SCHEMA_VERSION < min_writer_version
   → 唯讀降級：connect() 不拋例外、回傳可用連線並標記唯讀；search/status
     完全不受影響、正常回傳先前寫入的資料；但 process_store() 必須在最
     前面就乾淨拒絕（status="rejected", reason="read_only_mode"），連
     model2vec 去重都不應被觸發（本檔測試）。
3. SCHEMA_VERSION < min_reader_version
   → 維持 item 1 既有的強制拒絕，不在本檔重複測試，見
     tests/test_db_migrations.py 既有測試（本檔僅新增防禦性 fallback
     測試，見下方）。

另外驗證防禦性 fallback：min_reader_version / min_writer_version 任一缺漏
時，一律視為兩者皆等於資料庫的 schema_version（退回嚴格全有全無行為），
不得意外變寬鬆 —— 例如「min_writer_version 缺漏就當作沒有寫入限制」這種
誤判方向。
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from remagraph import db
from remagraph.dedup import EMBEDDING_DIM
from remagraph.models import SearchRequest, StatusRequest, StoreRequest
from remagraph.search import get_status, search_memories
from remagraph.store import process_store


def _set_meta(updates: dict[str, str | None]) -> None:
    """直接以底層 sqlite3 連線覆寫 _meta 表欄位（模擬各種資料庫狀態）。

    None 值代表「刪除該欄位」，用來模擬欄位缺漏（例如結構不同的未來資料庫）。
    使用當下 REMAGRAPH_STATE_DIR 解析出的 db 路徑，與 db.connect() 一致。
    """
    db_path = db.get_db_path()
    conn = sqlite3.connect(db_path)
    for key, value in updates.items():
        if value is None:
            conn.execute("DELETE FROM _meta WHERE key = ?", (key,))
        else:
            conn.execute(
                "INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)", (key, value)
            )
    conn.commit()
    conn.close()


def _valid_store_request(**overrides: object) -> StoreRequest:
    defaults: dict[str, object] = dict(
        project_id="testproj",
        task_id="task-schema-compat-001",
        agent_id="test-agent",
        kind="status_update",
        summary="this is a normal status update summary long enough to pass the thirty char rule",
        learnings=["learning one"],
    )
    defaults.update(overrides)
    return StoreRequest(**defaults)  # type: ignore[arg-type]


def _mock_dedup_model() -> "patch":
    """回傳一個 patch context manager，讓 dedup._get_model 回傳假模型，
    避免測試觸發真實 model2vec 下載/載入。"""
    patcher = patch("remagraph.dedup._get_model")
    return patcher


# ---------------------------------------------------------------------------
# Tier 1：SCHEMA_VERSION >= min_writer_version → 完全相容
# ---------------------------------------------------------------------------


def test_tier1_writer_compatible_connects_normally_and_stores(tmp_path, monkeypatch):
    """SCHEMA_VERSION >= 資料庫 min_writer_version：即使資料庫的 schema_version
    本身已比程式碼新，只要 min_writer_version 沒有跟著提高，程式碼仍應視為
    完全相容 —— connect() 不拋例外、不標記唯讀，且透過 process_store() 的
    一般寫入照常成功。"""
    state_dir = tmp_path / "state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    # 先建立一個全新資料庫（走過 _init_schema + 全新 _meta 種子值）
    conn0 = db.connect()
    conn0.close()

    # 模擬「schema_version 已比程式碼新，但寫入相容性需求並未提高」：
    # min_writer_version 維持 <= SCHEMA_VERSION，schema_version 本身調高。
    _set_meta(
        {
            "schema_version": str(db.SCHEMA_VERSION + 1),
            "min_reader_version": "1",
            "min_writer_version": str(db.SCHEMA_VERSION),
        }
    )

    conn = db.connect()  # 不應拋出 MigrationError

    assert getattr(conn, "remagraph_read_only", False) is False

    request = _valid_store_request()
    with _mock_dedup_model() as mock_get_model:
        mock_model = MagicMock()
        mock_model.dim = EMBEDDING_DIM
        mock_model.encode.return_value = np.array([-0.5] * EMBEDDING_DIM, dtype=np.float32)
        mock_get_model.return_value = mock_model
        response = process_store(request, conn)

    assert response.status == "stored"
    assert response.id is not None
    conn.close()


# ---------------------------------------------------------------------------
# Tier 2：min_reader_version <= SCHEMA_VERSION < min_writer_version → 唯讀降級
# ---------------------------------------------------------------------------


def test_tier2_reader_compatible_only_marks_readonly_blocks_write_allows_read(
    tmp_path, monkeypatch
):
    """min_reader_version <= SCHEMA_VERSION < min_writer_version：

    - connect() 必須成功（不拋例外），回傳可用連線並標記唯讀
    - 透過該連線呼叫 process_store() 必須乾淨拒絕
      （status="rejected", reason 指出唯讀模式，detail 說明原因），
      且不得觸發 model2vec 去重（本測試刻意不 mock dedup._get_model，
      若實作把唯讀檢查放在 dedup 呼叫之後，測試會因為嘗試載入真實模型
      而失敗/報錯，而不是乾淨回傳 rejected —— 藉此鎖定「檢查必須在最
      前面」這個要求）
    - search_memories() / get_status() 對同一條連線必須完全不受影響，
      正常回傳先前（唯讀模式生效前）寫入的真實資料
    """
    state_dir = tmp_path / "state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    # 1. 一般連線先寫入 fixture 資料（tier 1，完全相容）
    conn_normal = db.connect()
    fixture_request = _valid_store_request(
        task_id="task-visible-001",
        summary="fixture status update that must be visible after reopening read only",
    )
    with _mock_dedup_model() as mock_get_model:
        mock_model = MagicMock()
        mock_model.dim = EMBEDDING_DIM
        mock_model.encode.return_value = np.array([0.1] * EMBEDDING_DIM, dtype=np.float32)
        mock_get_model.return_value = mock_model
        fixture_response = process_store(fixture_request, conn_normal)
    assert fixture_response.status == "stored"
    conn_normal.close()

    # 2. 模擬「資料庫已升級到比程式碼寫入相容版本還新，但讀取仍相容」：
    #    min_reader_version 維持 <= SCHEMA_VERSION，min_writer_version 提高
    #    到超過 SCHEMA_VERSION。
    _set_meta(
        {
            "schema_version": str(db.SCHEMA_VERSION + 1),
            "min_reader_version": "1",
            "min_writer_version": str(db.SCHEMA_VERSION + 1),
        }
    )

    # 3. 重新開連線：不應拋例外，且應標記唯讀
    conn_ro = db.connect()
    assert getattr(conn_ro, "remagraph_read_only", False) is True

    # 4. 對同一條唯讀連線嘗試寫入：必須乾淨拒絕，不得拋出例外
    new_request = _valid_store_request(
        task_id="task-should-not-write-001",
        summary="this write attempt must be cleanly rejected in read only mode not crash",
    )
    write_response = process_store(new_request, conn_ro)
    assert write_response.status == "rejected"
    assert write_response.reason == "read_only_mode"
    assert write_response.detail
    assert "唯讀" in write_response.detail or "read" in write_response.detail.lower()

    # 確認確實沒有寫入新記錄
    count_row = conn_ro.execute(
        "SELECT COUNT(*) FROM memories WHERE task_id = ?",
        ("task-should-not-write-001",),
    ).fetchone()
    assert count_row[0] == 0

    # 5. search_memories() 對同一條唯讀連線必須正常運作，看得到 fixture 資料
    search_response = search_memories(
        conn_ro,
        SearchRequest(project_id="testproj", task_id="task-visible-001"),
    )
    assert len(search_response.results) == 1
    assert search_response.results[0]["task_id"] == "task-visible-001"

    # 6. get_status() 對同一條唯讀連線必須正常運作，看得到 fixture 資料
    status_response = get_status(conn_ro, StatusRequest(project_id="testproj"))
    matching = [s for s in status_response.latest if s["task_id"] == "task-visible-001"]
    assert len(matching) == 1
    assert "fixture status update" in matching[0]["summary"]

    conn_ro.close()


# ---------------------------------------------------------------------------
# 防禦性 fallback：min_reader_version / min_writer_version 任一缺漏
# → 視為兩者皆等於 schema_version（退回嚴格全有全無行為）
# ---------------------------------------------------------------------------


def test_defensive_fallback_missing_both_version_fields_hard_rejects(tmp_path, monkeypatch):
    """結構性未來資料庫：min_reader_version / min_writer_version 兩個欄位都
    不存在（只有 schema_version）。必須退回 item 1 既有的嚴格全有全無行為
    —— raise MigrationError，訊息與既有靜態訊息完全一致，不得意外放行。"""
    state_dir = tmp_path / "state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    conn0 = db.connect()
    conn0.close()

    _set_meta(
        {
            "schema_version": str(db.SCHEMA_VERSION + 1),
            "min_reader_version": None,
            "min_writer_version": None,
        }
    )

    with pytest.raises(db.MigrationError) as exc_info:
        db.connect()

    message = str(exc_info.value)
    assert f"schema_version={db.SCHEMA_VERSION + 1}" in message
    assert f"SCHEMA_VERSION={db.SCHEMA_VERSION}" in message


def test_defensive_fallback_missing_min_writer_only_hard_rejects(tmp_path, monkeypatch):
    """只有 min_writer_version 缺漏（min_reader_version 仍存在且很寬鬆，例如
    "1"）。依規格：任一欄位缺漏就必須視為兩者皆等於 schema_version，而不是
    只讓缺漏的那個欄位變寬鬆預設值 —— 否則會被误判為 tier 2（唯讀）甚至
    tier 1，而非正確的 tier 3 強制拒絕。"""
    state_dir = tmp_path / "state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    conn0 = db.connect()
    conn0.close()

    _set_meta(
        {
            "schema_version": str(db.SCHEMA_VERSION + 1),
            "min_reader_version": "1",
            "min_writer_version": None,
        }
    )

    with pytest.raises(db.MigrationError):
        db.connect()


def test_defensive_fallback_missing_min_reader_only_hard_rejects(tmp_path, monkeypatch):
    """只有 min_reader_version 缺漏（min_writer_version 仍存在且等於新
    schema_version）。即使 min_writer_version 誠實地表明「需要更新版本才能
    寫入」，min_reader_version 缺漏本身依規格也必須讓兩者都退回
    schema_version，維持 tier 3 強制拒絕，而不是把缺漏的 min_reader_version
    當成寬鬆值（例如誤用 1）而意外判成 tier 2 唯讀放行。"""
    state_dir = tmp_path / "state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    conn0 = db.connect()
    conn0.close()

    _set_meta(
        {
            "schema_version": str(db.SCHEMA_VERSION + 1),
            "min_reader_version": None,
            "min_writer_version": str(db.SCHEMA_VERSION + 1),
        }
    )

    with pytest.raises(db.MigrationError):
        db.connect()
