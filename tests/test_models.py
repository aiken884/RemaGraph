"""測試 models.py — Pydantic schema 驗證，涵蓋 D05 M1-M7。"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from remagraph.models import (
    Memory,
    SearchRequest,
    SearchResponse,
    StoreRequest,
    StoreResponse,
)


# ---------------------------------------------------------------------------
# M1: Memory 包含全部 14 個欄位（含 created_at、updated_at）
# ---------------------------------------------------------------------------
def test_memory_has_all_fields():
    """M1: Memory 應包含 DESIGN.md 定義的全部欄位。"""
    now = datetime.now(timezone.utc)
    m = Memory(
        project_id="testproj",
        id="mem-20260721-001",
        task_id="task-2026-07-21-003",
        agent_id="oc-dspro",
        timestamp=now,
        kind="task_handoff",
        summary=(
            "嘗試修復 subagent 委派 + deny-all 時的 acpx 連線錯誤，這是一個需要深入調查的複雜問題"
        ),
        learnings=["錯誤發生在 opencode task tool 生成 child session 之後"],
        handoff_note="接手者請注意：此錯誤與 G1 不同，G1 是 child session 未被註冊",
        tags=["acpx", "subagent", "bug"],
        status="active",
        created_at=now,
        updated_at=now,
    )
    # 所有必要欄位
    assert m.id == "mem-20260721-001"
    assert m.task_id == "task-2026-07-21-003"
    assert m.agent_id == "oc-dspro"
    assert m.timestamp == now
    assert m.kind == "task_handoff"
    assert m.summary.startswith("嘗試修復")
    assert len(m.learnings) == 1
    assert m.handoff_note.startswith("接手者請注意")
    assert m.tags == ["acpx", "subagent", "bug"]
    assert m.status == "active"
    assert m.created_at == now
    assert m.updated_at == now


# ---------------------------------------------------------------------------
# M2: StoreRequest 定義
# ---------------------------------------------------------------------------
def test_store_request_fields():
    """M2: StoreRequest 應包含 task_id, agent_id, kind, summary, learnings, handoff_note, tags,
    invalidates。"""
    req = StoreRequest(
        project_id="testproj",
        task_id="task-2026-07-21-003",
        agent_id="oc-dspro",
        kind="task_handoff",
        summary="嘗試修復 subagent 委派 + deny-all 時的 acpx 連線錯誤，這是一個需要深入調查的問題",
        learnings=["錯誤發生在 opencode task tool 生成 child session 之後"],
        handoff_note="接手者請注意：此錯誤與 G1 不同，G1 是 child session 未被註冊",
        tags=["acpx", "subagent", "bug"],
    )
    assert req.task_id == "task-2026-07-21-003"
    assert req.kind == "task_handoff"
    assert req.invalidates is None


def test_store_request_defaults():
    """M2: learnings/tags 預設空 list、handoff_note 預設空字串。"""
    req = StoreRequest(
        project_id="testproj",
        task_id="task-1",
        agent_id="test-agent",
        kind="status_update",
        summary="這是一個測試用的 summary 字串，長度必須超過三十個字元以上",
    )
    assert req.learnings == []
    assert req.tags == []
    assert req.handoff_note == ""


def test_store_request_with_invalidates():
    """M2: invalidates 欄位可選。"""
    req = StoreRequest(
        project_id="testproj",
        task_id="task-1",
        agent_id="test-agent",
        kind="discovered_constraint",
        summary="這是一個測試用的 summary 字串，長度必須超過三十個字元以上",
        invalidates=["mem-001", "mem-002"],
    )
    assert req.invalidates == ["mem-001", "mem-002"]


# ---------------------------------------------------------------------------
# M3: StoreResponse — 成功與拒絕回應
# ---------------------------------------------------------------------------
def test_store_response_stored():
    """M3: 成功回應 — status=stored, id, superseded。"""
    resp = StoreResponse(status="stored", id="mem-20260721-001", superseded=[], invalidated_count=0)
    assert resp.status == "stored"
    assert resp.id == "mem-20260721-001"
    assert resp.superseded == []
    assert resp.invalidated_count == 0


def test_store_response_rejected():
    """M3: 拒絕回應 — status=rejected, reason, detail。"""
    resp = StoreResponse(
        status="rejected",
        reason="summary_too_short",
        detail="summary 需 ≥ 30 字，目前 12 字",
    )
    assert resp.status == "rejected"
    assert resp.reason == "summary_too_short"
    assert resp.detail.startswith("summary 需")


def test_store_response_with_superseded():
    """M3: 成功回應含 superseded 清單。"""
    resp = StoreResponse(
        status="stored",
        id="mem-20260721-005",
        superseded=["mem-20260721-001", "mem-20260721-003"],
        invalidated_count=0,
    )
    assert len(resp.superseded) == 2


# ---------------------------------------------------------------------------
# M4: SearchRequest / SearchResponse
# ---------------------------------------------------------------------------
def test_search_request():
    """M4: SearchRequest 包含 query, top_k, kind 等過濾欄位。"""
    req = SearchRequest(query="acpx 連線錯誤", top_k=20, kind="task_handoff", status="active")
    assert req.query == "acpx 連線錯誤"
    assert req.top_k == 20
    assert req.kind == "task_handoff"
    assert req.status == "active"


def test_search_request_defaults():
    """M4: SearchRequest 預設值。"""
    req = SearchRequest(query="test")
    assert req.top_k == 20
    assert req.kind is None
    assert req.status is None


def test_search_request_top_k_bounds():
    """M4: top_k 應在 1-100 範圍內。"""
    # top_k=0 應被拒絕
    with pytest.raises(ValidationError):
        SearchRequest(query="test", top_k=0)
    # top_k=101 應被拒絕
    with pytest.raises(ValidationError):
        SearchRequest(query="test", top_k=101)
    # top_k=1 合法
    req = SearchRequest(query="test", top_k=1)
    assert req.top_k == 1
    # top_k=100 合法
    req = SearchRequest(query="test", top_k=100)
    assert req.top_k == 100


def test_search_response():
    """M4: SearchResponse 包含 results 與 has_more。"""
    resp = SearchResponse(results=[], has_more=False)
    assert resp.results == []
    assert resp.has_more is False


def test_search_response_with_results():
    """M4: SearchResponse 有結果時。"""
    now = datetime.now(timezone.utc)
    resp = SearchResponse(
        results=[
            {
                "id": "mem-001",
                "summary": "測試摘要",
                "agent_id": "test-agent",
                "timestamp": now.isoformat(),
                "score": 0.87,
            }
        ],
        has_more=True,
    )
    assert len(resp.results) == 1
    assert resp.results[0]["id"] == "mem-001"
    assert resp.results[0]["score"] == 0.87
    assert resp.has_more is True


# ---------------------------------------------------------------------------
# M5: MemoryKind / MemoryStatus literals 限制
# ---------------------------------------------------------------------------
def test_memory_kind_literals():
    """M5: MemoryKind 限制四值（含 fleet_member for tower）。"""
    valid = ["task_handoff", "status_update", "discovered_constraint", "fleet_member"]
    for k in valid:
        req = StoreRequest(
            project_id="testproj",
            task_id="task-1",
            agent_id="test-agent",
            kind=k,  # type: ignore[arg-type]
            summary="這是一個測試用的 summary 字串，長度必須超過三十個字元以上",
        )
        assert req.kind == k


def test_memory_status_literals():
    """M5: MemoryStatus 限制三值。"""
    now = datetime.now(timezone.utc)
    valid = ["active", "superseded", "invalidated"]
    for s in valid:
        m = Memory(
            project_id="testproj",
            id="mem-001",
            task_id="task-1",
            agent_id="test",
            timestamp=now,
            kind="task_handoff",
            summary="這是一個測試用的 summary 字串，長度必須超過三十個字元，這是為了通過長度檢查",
            status=s,  # type: ignore[arg-type]
            created_at=now,
            updated_at=now,
        )
        assert m.status == s


# ---------------------------------------------------------------------------
# M6: Pydantic validation — 不合法的 kind 拋 ValidationError
# ---------------------------------------------------------------------------
def test_invalid_kind_raises_validation_error():
    """M6: 不合法的 kind 拋 ValidationError。"""
    with pytest.raises(ValidationError):
        StoreRequest(
            task_id="task-1",
            agent_id="test",
            kind="invalid_kind",  # type: ignore[arg-type]
            summary="這是一個測試用的 summary 字串，長度必須超過三十個字元以上",
        )


def test_invalid_status_raises_validation_error():
    """M6: 不合法的 status 拋 ValidationError。"""
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        Memory(
            id="mem-001",
            task_id="task-1",
            agent_id="test",
            timestamp=now,
            kind="task_handoff",
            summary="這是一個測試用的 summary 字串，長度必須超過三十個字元以上",
            status="deleted",  # type: ignore[arg-type]
            created_at=now,
            updated_at=now,
        )


def test_missing_required_field_raises():
    """M6: 缺少必要欄位拋 ValidationError。"""
    with pytest.raises(ValidationError):
        StoreRequest(
            task_id="task-1",
            # agent_id 缺少
            kind="task_handoff",
            summary="這是一個測試用的 summary 字串，長度必須超過三十個字元以上",
        )


# ---------------------------------------------------------------------------
# M7: learnings / tags 預設空 list、handoff_note 預設空字串
# ---------------------------------------------------------------------------
def test_learnings_defaults_to_empty_list():
    """M7: learnings 預設空 list。"""
    req = StoreRequest(
        project_id="testproj",
        task_id="task-1",
        agent_id="test",
        kind="task_handoff",
        summary="這是一個測試用的 summary 字串，長度必須超過三十個字元以上",
    )
    assert req.learnings == []


def test_tags_defaults_to_empty_list():
    """M7: tags 預設空 list。"""
    now = datetime.now(timezone.utc)
    m = Memory(
        project_id="testproj",
        id="mem-001",
        task_id="task-1",
        agent_id="test",
        timestamp=now,
        kind="task_handoff",
        summary="這是一個測試用的 summary 字串，長度必須超過三十個字元以上",
        created_at=now,
        updated_at=now,
    )
    assert m.tags == []


def test_handoff_note_defaults_to_empty_string():
    """M7: handoff_note 預設空字串。"""
    now = datetime.now(timezone.utc)
    m = Memory(
        project_id="testproj",
        id="mem-001",
        task_id="task-1",
        agent_id="test",
        timestamp=now,
        kind="status_update",
        summary="這是一個測試用的 summary 字串，長度必須超過三十個字元以上",
        created_at=now,
        updated_at=now,
    )
    assert m.handoff_note == ""


# ---------------------------------------------------------------------------
# 額外：搜尋回應包含 kind 資訊
# ---------------------------------------------------------------------------
def test_search_response_result_includes_kind():
    """SearchResponse.results 的每一筆應包含 kind 欄位。"""
    now = datetime.now(timezone.utc)
    resp = SearchResponse(
        results=[
            {
                "id": "mem-001",
                "summary": "測試摘要",
                "agent_id": "test-agent",
                "kind": "task_handoff",
                "timestamp": now.isoformat(),
                "score": 0.85,
            }
        ],
        has_more=False,
    )
    assert resp.results[0]["kind"] == "task_handoff"


# ---------------------------------------------------------------------------
# 額外：remagraph_status 回傳模型
# ---------------------------------------------------------------------------
def test_status_response():
    """StatusResponse 應包含 latest 清單。"""
    from remagraph.models import StatusResponse

    resp = StatusResponse(latest=[])
    assert resp.latest == []

    now = datetime.now(timezone.utc)
    resp = StatusResponse(
        latest=[
            {
                "task_id": "task-2026-07-21-003",
                "summary": "subagent 委派 bug 正在修",
                "agent_id": "oc-dspro",
                "timestamp": now.isoformat(),
            }
        ]
    )
    assert len(resp.latest) == 1
    assert resp.latest[0]["task_id"] == "task-2026-07-21-003"
