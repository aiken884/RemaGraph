# SPDX-License-Identifier: Apache-2.0
"""測試 store.py — SQLite + FTS5 讀寫，涵蓋 D05 S1-S7。"""

import json
import sqlite3
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from remagraph import db as db_mod
from remagraph.dedup import EMBEDDING_DIM
from remagraph.models import Memory, StoreRequest
from remagraph.store import (
    _row_to_memory,
    generate_memory_id,
    get_active_embeddings,
    get_latest_status_updates,
    get_memory_by_id,
    insert_memory,
    process_store,
)


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
    return datetime(2026, 7, 21, 14, 30, 0, tzinfo=timezone.utc)


def _make_memory(**overrides) -> Memory:
    defaults = {
        "id": "mem-20260721-001",
        "project_id": "testproj",
        "task_id": "task-001",
        "agent_id": "test-agent",
        "timestamp": datetime(2026, 7, 21, 14, 30, 0, tzinfo=timezone.utc),
        "kind": "task_handoff",
        "summary": (
            "this is a test summary that must be at least thirty characters long to pass validation"
        ),
        "learnings": ["test learning item one"],
        "handoff_note": "test handoff note for the receiver",
        "tags": ["test", "example"],
        "status": "active",
        "created_at": datetime(2026, 7, 21, 14, 30, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 7, 21, 14, 30, 0, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    return Memory(**defaults)


# === S1: generate_memory_id ===


def test_generate_memory_id_first_of_day(conn, now):
    """S1: 當天第一筆記憶 ID 為 mem-YYYYMMDD-001。"""
    mid = generate_memory_id(conn, now=now)
    assert mid == "mem-20260721-001"


def test_generate_memory_id_increments(conn, now):
    """S1: 同一天內流水號遞增。"""
    emb = np.ones(EMBEDDING_DIM, dtype=np.float32)
    # 插入記憶讓流水號遞增
    mem1 = _make_memory(id="mem-20260721-001")
    insert_memory(conn, mem1, emb)
    assert generate_memory_id(conn, now=now) == "mem-20260721-002"
    mem2 = _make_memory(id="mem-20260721-002")
    insert_memory(conn, mem2, emb)
    assert generate_memory_id(conn, now=now) == "mem-20260721-003"


def test_generate_memory_id_resets_across_days(conn, now):
    """S1: 跨天重置流水號。"""
    assert generate_memory_id(conn, now=now) == "mem-20260721-001"

    next_day = datetime(2026, 7, 22, 0, 0, 0, tzinfo=timezone.utc)
    assert generate_memory_id(conn, now=next_day) == "mem-20260722-001"


# === S1: insert_memory ===


def test_insert_memory_writes_all_fields(conn, now):
    """S1: insert_memory 完整寫入 14 欄位。"""
    mem = _make_memory()
    emb = np.ones(EMBEDDING_DIM, dtype=np.float32)

    insert_memory(conn, mem, emb)

    row = conn.execute("SELECT * FROM memories WHERE id=?", (mem.id,)).fetchone()
    assert row is not None
    assert row["id"] == mem.id
    assert row["kind"] == mem.kind
    assert row["task_id"] == mem.task_id
    assert row["agent_id"] == mem.agent_id
    assert row["summary"] == mem.summary
    assert row["status"] == "active"
    # learnings / tags 是 JSON
    assert json.loads(row["learnings"]) == mem.learnings
    assert json.loads(row["tags"]) == mem.tags


# === S2: FTS5 自動同步 ===


def test_insert_memory_fts5_sync(conn, now):
    """S2: INSERT 後 FTS5 可查到 summary 內容。"""
    mem = _make_memory(summary="acpx connection error during subagent delegation fix attempt")
    emb = np.ones(EMBEDDING_DIM, dtype=np.float32)

    insert_memory(conn, mem, emb)

    rows = conn.execute("SELECT * FROM memories_fts WHERE memories_fts MATCH 'acpx'").fetchall()
    assert len(rows) >= 1


# === S3: status_update supersede ===


def test_process_store_supersedes_old_status_updates(conn, now):
    """S3: 寫入新 status_update 自動 supersede 同 task_id 舊記錄。"""
    # 先寫一筆舊的 status_update
    old_mem = _make_memory(
        id="mem-old-001",
        task_id="task-status-001",
        kind="status_update",
        summary="old status update that must be at least thirty characters long",
    )
    emb = np.ones(EMBEDDING_DIM, dtype=np.float32)
    insert_memory(conn, old_mem, emb)

    # 模擬新 status_update store 請求
    req = StoreRequest(
        project_id="testproj",
        task_id="task-status-001",
        agent_id="test-agent",
        kind="status_update",
        summary="new status update that must be at least thirty characters long too",
        learnings=["new learning"],
    )

    with patch("remagraph.dedup._get_model") as mock_get_model:
        mock_model = MagicMock()
        mock_model.dim = EMBEDDING_DIM
        mock_model.encode.return_value = np.array([-0.5] * EMBEDDING_DIM, dtype=np.float32)
        mock_get_model.return_value = mock_model

        response = process_store(req, conn)

    assert response.status == "stored"
    assert len(response.superseded) >= 1
    assert "mem-old-001" in response.superseded

    # 確認舊記錄已 superseded
    row = conn.execute("SELECT status FROM memories WHERE id='mem-old-001'").fetchone()
    assert row["status"] == "superseded"


def test_process_store_no_supersede_for_task_handoff(conn, now):
    """S3: task_handoff 不被 supersede。"""
    old_mem = _make_memory(
        id="mem-old-002",
        task_id="task-handoff-001",
        kind="task_handoff",
        summary="old task handoff that must be at least thirty characters long to be valid",
    )
    emb = np.ones(EMBEDDING_DIM, dtype=np.float32)
    insert_memory(conn, old_mem, emb)

    req = StoreRequest(
        project_id="testproj",
        task_id="task-handoff-001",
        agent_id="test-agent",
        kind="task_handoff",
        summary="new task handoff that must be at least thirty characters long to be stored",
        learnings=["new learning point"],
        handoff_note="handoff note that is at least twenty characters long for the test",
    )

    with patch("remagraph.dedup._get_model") as mock_get_model:
        mock_model = MagicMock()
        mock_model.dim = EMBEDDING_DIM
        mock_model.encode.return_value = np.array([-0.5] * EMBEDDING_DIM, dtype=np.float32)
        mock_get_model.return_value = mock_model

        response = process_store(req, conn)

    assert response.status == "stored"
    # task_handoff 不 supersede
    row = conn.execute("SELECT status FROM memories WHERE id='mem-old-002'").fetchone()
    assert row["status"] == "active"


# === S4: discovered_constraint invalidates ===


def test_process_store_invalidates_constraints(conn, now):
    """S4: discovered_constraint 寫入時 invalidate 既有記錄。"""
    old_mem = _make_memory(
        id="mem-old-003",
        task_id="task-dc-001",
        kind="discovered_constraint",
        summary="old discovered constraint that must be at least thirty characters long",
    )
    emb = np.ones(EMBEDDING_DIM, dtype=np.float32)
    insert_memory(conn, old_mem, emb)

    req = StoreRequest(
        project_id="testproj",
        task_id="task-dc-001",
        agent_id="test-agent",
        kind="discovered_constraint",
        summary="new discovered constraint that must be at least thirty characters long to pass",
        learnings=["updated finding"],
        invalidates=["mem-old-003"],
    )

    with patch("remagraph.dedup._get_model") as mock_get_model:
        mock_model = MagicMock()
        mock_model.dim = EMBEDDING_DIM
        mock_model.encode.return_value = np.array([-0.5] * EMBEDDING_DIM, dtype=np.float32)
        mock_get_model.return_value = mock_model

        response = process_store(req, conn)

    assert response.status == "stored"
    assert response.invalidated_count == 1

    row = conn.execute("SELECT status FROM memories WHERE id='mem-old-003'").fetchone()
    assert row["status"] == "invalidated"


# === S5: StoreResponse ===


def test_process_store_returns_correct_response(conn, now):
    """S5: 成功寫入回傳正確 StoreResponse。"""
    req = StoreRequest(
        project_id="testproj",
        task_id="task-resp-001",
        agent_id="test-agent",
        kind="task_handoff",
        summary="this is a test summary that must be at least thirty characters long for the check",
        learnings=["learned something useful"],
        handoff_note="handoff note that is at least twenty characters long for the test",
    )

    with patch("remagraph.dedup._get_model") as mock_get_model:
        mock_model = MagicMock()
        mock_model.dim = EMBEDDING_DIM
        mock_model.encode.return_value = np.array([0.1] * EMBEDDING_DIM, dtype=np.float32)
        mock_get_model.return_value = mock_model

        response = process_store(req, conn)

    assert response.status == "stored"
    assert response.id is not None
    assert response.id.startswith("mem-")
    assert response.superseded == []
    assert response.invalidated_count == 0


# === S5: 仲裁拒絕回傳 rejected ===


def test_process_store_rejects_short_summary(conn, now):
    """S5: summary 太短回傳 rejected。"""
    req = StoreRequest(
        project_id="testproj",
        task_id="task-rej-001",
        agent_id="test-agent",
        kind="task_handoff",
        summary="太短",
        learnings=["learned"],
        handoff_note="handoff note that is at least twenty characters long for the test",
    )

    response = process_store(req, conn)
    assert response.status == "rejected"
    assert response.reason == "summary_too_short"


# === S6: transaction 保證 ===


def test_insert_memory_rollback_on_failure(conn, now):
    """S6: transaction 失敗時 DB 無副作用。"""
    emb = np.ones(EMBEDDING_DIM, dtype=np.float32)

    conn.execute("BEGIN")
    try:
        valid_mem = _make_memory(id="mem-valid-001")
        insert_memory(conn, valid_mem, emb)
        # 插入一個會觸發 constraint violation 的記錄（重複 id）
        conn.execute(
            "INSERT INTO memories (id, project_id, kind, task_id, agent_id, timestamp, summary, "
            "learnings, handoff_note, tags, status, embedding, created_at, updated_at) "
            "VALUES ('mem-valid-001', 'default', 'task_handoff', 'task-x', 'test', '2026-01-01T00:00:00Z', "  # noqa: E501
            "'duplicate id should cause a constraint violation and rollback the transaction', "
            "'[]', '', '[]', 'active', NULL, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
        )
        conn.execute("COMMIT")
        pytest.fail("應觸發 IntegrityError")
    except sqlite3.IntegrityError:
        conn.execute("ROLLBACK")

    row = conn.execute("SELECT id FROM memories WHERE id='mem-valid-001'").fetchone()
    assert row is None


# === S7: embedding 存為 BLOB ===


def test_insert_memory_stores_embedding_as_blob(conn, now):
    """S7: embedding 以 BLOB 形式儲存，可讀回。"""
    mem = _make_memory()
    emb = np.array([0.1, 0.2, 0.3] + [0.0] * (EMBEDDING_DIM - 3), dtype=np.float32)

    insert_memory(conn, mem, emb)

    row = conn.execute("SELECT embedding FROM memories WHERE id=?", (mem.id,)).fetchone()
    assert row["embedding"] is not None
    restored = np.frombuffer(row["embedding"], dtype="<f4")
    assert restored.shape == (EMBEDDING_DIM,)
    assert restored[0] == pytest.approx(0.1)
    assert restored[1] == pytest.approx(0.2)


# === get_memory_by_id ===


def test_get_memory_by_id_found(conn, now):
    """get_memory_by_id：找到記錄。"""
    mem = _make_memory(id="mem-find-001")
    emb = np.ones(EMBEDDING_DIM, dtype=np.float32)
    insert_memory(conn, mem, emb)

    found = get_memory_by_id(conn, "mem-find-001")
    assert found is not None
    assert found.id == "mem-find-001"
    assert found.kind == "task_handoff"


def test_get_memory_by_id_not_found(conn):
    """get_memory_by_id：找不到回傳 None。"""
    assert get_memory_by_id(conn, "nonexistent") is None


# === get_active_embeddings ===


def test_get_active_embeddings(conn, now):
    """get_active_embeddings：回傳同 kind 的 active embedding。"""
    mem1 = _make_memory(id="mem-emb-001", kind="task_handoff")
    mem2 = _make_memory(id="mem-emb-002", kind="task_handoff")
    emb = np.ones(EMBEDDING_DIM, dtype=np.float32)
    insert_memory(conn, mem1, emb)
    insert_memory(conn, mem2, emb)

    results = get_active_embeddings(conn, "task_handoff")
    assert len(results) == 2
    ids = {r[0] for r in results}
    assert "mem-emb-001" in ids
    assert "mem-emb-002" in ids


# === get_latest_status_updates ===


def test_get_latest_status_updates_dedup_by_task_id(conn, now):
    """get_latest_status_updates：同 task_id 多筆只回最新。"""
    mem1 = _make_memory(
        id="mem-su-001",
        task_id="task-su-001",
        kind="status_update",
        summary="first status update that must be at least thirty characters long",
        created_at=datetime(2026, 7, 21, 10, 0, 0, tzinfo=timezone.utc),
    )
    mem2 = _make_memory(
        id="mem-su-002",
        task_id="task-su-001",
        kind="status_update",
        summary="second status update with at least thirty characters to pass",
        created_at=datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc),
    )
    emb = np.ones(EMBEDDING_DIM, dtype=np.float32)
    insert_memory(conn, mem1, emb)
    insert_memory(conn, mem2, emb)

    results = get_latest_status_updates(conn, limit=10)
    # 同 task_id 只應回傳一筆最新
    task_su_001 = [r for r in results if r.task_id == "task-su-001"]
    assert len(task_su_001) == 1
    assert task_su_001[0].id == "mem-su-002"


def test_get_latest_status_updates_respects_limit(conn, now):
    """get_latest_status_updates：遵守 limit。"""
    emb = np.ones(EMBEDDING_DIM, dtype=np.float32)
    for i in range(5):
        mem = _make_memory(
            id=f"mem-limit-{i:03d}",
            task_id=f"task-limit-{i:03d}",
            kind="status_update",
            summary=(
                f"status update number {i} that must be at least thirty characters long to pass"
            ),
            created_at=datetime(2026, 7, 21, i, 0, 0, tzinfo=timezone.utc),
        )
        insert_memory(conn, mem, emb)

    results = get_latest_status_updates(conn, limit=3)
    assert len(results) <= 3


# === _row_to_memory ===


# === labels（PPLX 架構改善計畫 item 4b） ===


def _patched_model():
    """回傳一個 patch context manager，讓 process_store 免下載真實模型。"""
    mock_model = MagicMock()
    mock_model.dim = EMBEDDING_DIM
    mock_model.encode.return_value = np.array([0.2] * EMBEDDING_DIM, dtype=np.float32)
    return patch("remagraph.dedup._get_model", return_value=mock_model)


def test_process_store_inserts_memory_labels_in_same_transaction(conn, now):
    """Regression: 提供合法 labels 時，process_store 須在同一個 transaction
    內把對應的 (memory_id, label) 列寫進 memory_labels。"""
    req = StoreRequest(
        project_id="testproj",
        task_id="task-labels-001",
        agent_id="test-agent",
        kind="task_handoff",
        summary="這是一段足夠長的摘要，用來測試 labels 是否正確寫入 memory_labels 資料表",
        learnings=["learned something about labels"],
        handoff_note="handoff note that is at least twenty characters long here",
        labels=["dep:opencode", "topic:auth"],
    )

    with _patched_model():
        response = process_store(req, conn)

    assert response.status == "stored"
    assert response.id is not None

    rows = conn.execute(
        "SELECT label FROM memory_labels WHERE memory_id = ? ORDER BY label", (response.id,)
    ).fetchall()
    labels = [r["label"] for r in rows]
    assert labels == ["dep:opencode", "topic:auth"]


def test_process_store_dedupes_repeated_labels(conn, now):
    """Regression: 重複的 label 不應撞上 (memory_id, label) 複合主鍵而拋出
    IntegrityError —— process_store 須自行去重後才寫入。"""
    req = StoreRequest(
        project_id="testproj",
        task_id="task-labels-002",
        agent_id="test-agent",
        kind="task_handoff",
        summary="這是一段足夠長的摘要，用來測試重複 labels 不會撞到複合主鍵約束",
        learnings=["learned something about duplicate labels"],
        handoff_note="handoff note that is at least twenty characters long too",
        labels=["dep:opencode", "dep:opencode", "topic:auth"],
    )

    with _patched_model():
        response = process_store(req, conn)

    assert response.status == "stored"
    rows = conn.execute(
        "SELECT label FROM memory_labels WHERE memory_id = ? ORDER BY label", (response.id,)
    ).fetchall()
    assert [r["label"] for r in rows] == ["dep:opencode", "topic:auth"]


def test_process_store_rejects_malformed_label(conn, now):
    """Regression: 不符合 namespace:value 格式的 label 須讓整個 store 請求
    被拒絕（status=rejected, reason=invalid_label），且不得寫入任何
    memories / memory_labels 記錄（不得只存一半）。"""
    req = StoreRequest(
        project_id="testproj",
        task_id="task-labels-003",
        agent_id="test-agent",
        kind="task_handoff",
        summary="這是一段足夠長的摘要，用來測試格式錯誤的 label 會被整批拒絕",
        learnings=["learned something"],
        handoff_note="handoff note that is at least twenty characters long yes",
        labels=["not-a-valid-label-without-colon"],
    )

    response = process_store(req, conn)

    assert response.status == "rejected"
    assert response.reason == "invalid_label"
    assert response.id is None

    # 不應有任何部分寫入殘留
    mem_rows = conn.execute(
        "SELECT id FROM memories WHERE task_id = 'task-labels-003'"
    ).fetchall()
    assert mem_rows == []
    label_rows = conn.execute("SELECT * FROM memory_labels").fetchall()
    assert label_rows == []


def test_process_store_rejects_when_any_label_in_list_is_malformed(conn, now):
    """Regression: labels 清單中只要有一個格式不符，整批（含其餘合法的）
    都不寫入 —— 驗證『整批拒絕』而非『靜默跳過壞的、留下好的』這個設計選擇。
    """
    req = StoreRequest(
        project_id="testproj",
        task_id="task-labels-004",
        agent_id="test-agent",
        kind="task_handoff",
        summary="這是一段足夠長的摘要，混合合法與不合法的 label 應整批被拒絕",
        learnings=["learned something"],
        handoff_note="handoff note that is at least twenty characters long ok",
        labels=["dep:opencode", "BADNAMESPACE:value"],
    )

    response = process_store(req, conn)

    assert response.status == "rejected"
    assert response.reason == "invalid_label"
    label_rows = conn.execute("SELECT * FROM memory_labels").fetchall()
    assert label_rows == [], "合法的 dep:opencode 也不該被單獨留下"


def test_process_store_labels_default_empty_is_backward_compatible(conn, now):
    """Regression: 未提供 labels 時（既有呼叫端行為）須維持完全不受影響 ——
    stored 成功、memory_labels 內沒有任何列。"""
    req = StoreRequest(
        project_id="testproj",
        task_id="task-labels-005",
        agent_id="test-agent",
        kind="task_handoff",
        summary="這是一段足夠長的摘要，完全不提供 labels 參數，測試向後相容性",
        learnings=["learned something"],
        handoff_note="handoff note that is at least twenty characters long fine",
    )

    with _patched_model():
        response = process_store(req, conn)

    assert response.status == "stored"
    label_rows = conn.execute(
        "SELECT * FROM memory_labels WHERE memory_id = ?", (response.id,)
    ).fetchall()
    assert label_rows == []


def test_row_to_memory(conn, now):
    """_row_to_memory：正確轉換 sqlite3.Row 為 Memory。"""
    mem = _make_memory()
    emb = np.ones(EMBEDDING_DIM, dtype=np.float32)
    insert_memory(conn, mem, emb)

    row = conn.execute("SELECT * FROM memories WHERE id=?", (mem.id,)).fetchone()
    result = _row_to_memory(row)
    assert isinstance(result, Memory)
    assert result.id == mem.id
    assert result.task_id == mem.task_id
    assert isinstance(result.learnings, list)
    assert isinstance(result.tags, list)
