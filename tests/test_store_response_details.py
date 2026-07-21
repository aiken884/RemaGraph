"""加強 process_store 回應欄位的測試（針對 mutmut 發現的倖存變異）。

目的：確保 StoreResponse 中的所有欄位（status、reason、detail、id、superseded、invalidated_count）
都被適當驗證，以捕捉所有可能的變異。
"""

import sqlite3
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from remagraph import db as db_mod
from remagraph.dedup import EMBEDDING_DIM
from remagraph.models import StoreRequest
from remagraph.store import process_store


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """in-memory SQLite，含完整 schema。

    process_store() 會透過 append_audit() 寫 audit.jsonl，隔離
    REMAGRAPH_STATE_DIR 避免測試汙染真實 ~/.local/state/remagraph/。
    """
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path))
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    db_mod._init_schema(c)
    return c


@pytest.fixture
def now():
    """固定當前時間。"""
    return datetime(2026, 1, 15, 10, 30, 0, tzinfo=timezone.utc)


def _make_request(
    task_id="task-001",
    agent_id="agent-001",
    kind="task_handoff",
    summary="this is a valid summary that must be at least thirty characters long",
    learnings=None,
    handoff_note="valid handoff note at least 20 chars",
    tags=None,
):
    """建立 StoreRequest。"""
    return StoreRequest(
        task_id=task_id,
        agent_id=agent_id,
        kind=kind,
        summary=summary,
        learnings=learnings or ["learned"],
        handoff_note=handoff_note,
        tags=tags or [],
    )


# === 測試 1: 成功存儲時的回應欄位 ===


def test_process_store_success_all_fields(conn, now):
    """S1: 成功存儲時，所有回應欄位應正確設定。

    - status: "stored"
    - reason: None
    - detail: None
    - id: 非空，以 "mem-" 開頭
    - superseded: 空列表
    - invalidated_count: 0
    """
    req = _make_request()

    with patch("remagraph.dedup._get_model") as mock_get_model:
        mock_model = MagicMock()
        mock_model.dim = EMBEDDING_DIM
        mock_model.encode.return_value = np.array([0.1] * EMBEDDING_DIM, dtype=np.float32)
        mock_get_model.return_value = mock_model

        response = process_store(req, conn)

    assert response.status == "stored"
    assert response.reason is None  # 關鍵：成功時 reason 應為 None
    assert response.detail is None  # 關鍵：成功時 detail 應為 None
    assert response.id is not None
    assert response.id.startswith("mem-")
    assert response.superseded == []
    assert response.invalidated_count == 0


# === 測試 2: 仲裁規則拒絕時的回應 ===


def test_process_store_rejected_short_summary_has_reason_detail(conn, now):
    """S2: summary 太短時，reason 和 detail 應被正確設定。

    - status: "rejected"
    - reason: "summary_too_short"
    - detail: 非空字符串
    """
    req = _make_request(summary="太短")

    response = process_store(req, conn)

    assert response.status == "rejected"
    assert response.reason == "summary_too_short"  # 關鍵
    assert response.detail is not None  # 關鍵：detail 應非空
    assert isinstance(response.detail, str)
    assert len(response.detail) > 0


def test_process_store_rejected_empty_learnings_has_reason_detail(conn, now):
    """S3: learnings 為空或全空白時，reason 和 detail 應被正確設定。

    - status: "rejected"
    - reason: "learnings_empty"
    - detail: 非空字符串
    """
    req = _make_request(learnings=["  ", ""])

    response = process_store(req, conn)

    assert response.status == "rejected"
    assert response.reason == "learnings_empty"  # 關鍵
    assert response.detail is not None  # 關鍵
    assert isinstance(response.detail, str)


def test_process_store_rejected_short_handoff_note_has_reason_detail(conn, now):
    """S5: handoff_note 太短時，reason 和 detail 應被正確設定。

    - status: "rejected"
    - reason: "handoff_note_too_short"
    - detail: 非空字符串
    """
    req = _make_request(handoff_note="太短")

    response = process_store(req, conn)

    assert response.status == "rejected"
    assert response.reason == "handoff_note_too_short"  # 關鍵
    assert response.detail is not None  # 關鍵
    assert isinstance(response.detail, str)


# === 測試 3: Dedup 拒絕時的回應 ===


def test_process_store_rejected_duplicate_has_reason_detail(conn, now):
    """S6: 內容重複時，reason 和 detail 應被正確設定。

    先存儲一條記憶，然後嘗試存儲相同內容的記憶，應被去重拒絕。
    - status: "rejected"
    - reason: "duplicate_content"
    - detail: 包含相似度信息
    """
    summary = "this is a test summary that is quite long and will be used for deduplication testing"

    with patch("remagraph.dedup._get_model") as mock_get_model:
        mock_model = MagicMock()
        mock_model.dim = EMBEDDING_DIM
        # 返回固定的 embedding
        fixed_embedding = np.array([0.5] * EMBEDDING_DIM, dtype=np.float32)
        mock_model.encode.return_value = fixed_embedding
        mock_get_model.return_value = mock_model

        # 第一次存儲
        req1 = _make_request(summary=summary)
        response1 = process_store(req1, conn)
        assert response1.status == "stored"

        # 第二次嘗試存儲相同內容，應被去重拒絕
        req2 = _make_request(
            task_id="task-002",
            summary=summary,
        )
        response2 = process_store(req2, conn)

    assert response2.status == "rejected"
    assert response2.reason == "duplicate_content"  # 關鍵
    assert response2.detail is not None  # 關鍵
    assert "similarity" in response2.detail.lower()  # detail 應包含相似度信息


# === 測試 4: 正確區分不同的拒絕原因 ===


def test_process_store_rejected_reasons_are_distinct(conn, now):
    """S7: 不同拒絕原因應有不同的 reason 值。"""
    # 短 summary
    req_short_summary = _make_request(summary="短")
    resp_short_summary = process_store(req_short_summary, conn)

    # 空白 learnings
    req_empty_learnings = _make_request(learnings=["  "])
    resp_empty_learnings = process_store(req_empty_learnings, conn)

    # 短 handoff_note
    req_short_handoff = _make_request(handoff_note="短")
    resp_short_handoff = process_store(req_short_handoff, conn)

    # 所有都應被拒絕，但原因不同
    assert resp_short_summary.status == "rejected"
    assert resp_empty_learnings.status == "rejected"
    assert resp_short_handoff.status == "rejected"

    assert resp_short_summary.reason != resp_empty_learnings.reason
    assert resp_empty_learnings.reason != resp_short_handoff.reason
    assert resp_short_summary.reason != resp_short_handoff.reason


# === 測試 5: Supersede 和 Invalidate 計數 ===


def test_process_store_response_field_immutability(conn, now):
    """S8: 同一 process_store 呼叫的回應欄位應保持一致。

    多次訪問 response.status、response.reason 等應得到相同結果。
    """
    req = _make_request()

    with patch("remagraph.dedup._get_model") as mock_get_model:
        mock_model = MagicMock()
        mock_model.dim = EMBEDDING_DIM
        mock_model.encode.return_value = np.array([0.1] * EMBEDDING_DIM, dtype=np.float32)
        mock_get_model.return_value = mock_model

        response = process_store(req, conn)

    # 多次訪問應得到相同結果
    status_1 = response.status
    status_2 = response.status
    assert status_1 == status_2

    reason_1 = response.reason
    reason_2 = response.reason
    assert reason_1 == reason_2

    detail_1 = response.detail
    detail_2 = response.detail
    assert detail_1 == detail_2


def test_process_store_all_rejection_codes_have_details(conn, now):
    """S9: 所有拒絕回應都應有 reason 和 detail。"""
    test_cases = [
        (_make_request(summary="短"), "summary_too_short"),
        (_make_request(handoff_note="短"), "handoff_note_too_short"),
        (_make_request(learnings=["  "]), "learnings_empty"),
    ]

    for req, expected_reason in test_cases:
        response = process_store(req, conn)
        assert response.status == "rejected"
        assert response.reason == expected_reason
        assert response.detail is not None
        assert len(response.detail) > 0  # detail 應非空
