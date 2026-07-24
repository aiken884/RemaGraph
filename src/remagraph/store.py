# SPDX-License-Identifier: Apache-2.0
"""SQLite + FTS5 讀寫。

本模組負責：
- 記憶 ID 生成（mem-YYYYMMDD-NNN）
- 記憶的 INSERT / UPDATE（supersede / invalidate）
- 查詢（單筆、embedding 批次、最新 status）
- process_store：完整 store 流程（仲裁 → dedup → 寫入）

注意：本模組不自行管理 transaction 邊界。process_store 內部使用單一 transaction。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone

import numpy as np

from remagraph.arbitration import (
    ArbitrationResult,
    invalidate_constraints,
    run_arbitration_rules_cheap,
    supersede_for_kind,
)
from remagraph.audit import append_audit
from remagraph.dedup import check_duplicate, encode_summary
from remagraph.models import Memory, MemoryKind, StoreRequest, StoreResponse

# ---------------------------------------------------------------------------
# 自訂例外
# ---------------------------------------------------------------------------


class MemoryIDGenerationError(RuntimeError):
    """記憶 ID 生成失敗（例如並發衝突超過重試次數）。"""


# ---------------------------------------------------------------------------
# generate_memory_id
# ---------------------------------------------------------------------------


def generate_memory_id(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
) -> str:
    """生成唯一記憶 ID，格式 mem-YYYYMMDD-NNN。

    應在 transaction 內呼叫以保證並發安全。
    """
    if now is None:
        now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y%m%d")
    prefix = f"mem-{date_str}-%"

    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(id, 14) AS INTEGER)) FROM memories WHERE id LIKE ?",
        (prefix,),
    ).fetchone()

    max_nnn = row[0] if row[0] is not None else 0
    nnn = max_nnn + 1
    return f"mem-{date_str}-{nnn:03d}"


# ---------------------------------------------------------------------------
# insert_memory
# ---------------------------------------------------------------------------


def insert_memory(
    conn: sqlite3.Connection,
    memory: Memory,
    embedding: np.ndarray | None,
) -> str:
    """插入一筆記憶記錄。

    應在 transaction 內呼叫。FTS5 trigger 會自動同步。
    回傳 memory.id。
    """
    learnings_json = json.dumps(memory.learnings, ensure_ascii=False)
    tags_json = json.dumps(memory.tags, ensure_ascii=False)
    ts = memory.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    ca = memory.created_at.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    ua = memory.updated_at.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    emb_bytes: bytes | None = None
    if embedding is not None:
        emb_bytes = embedding.astype(np.float32).tobytes()

    conn.execute(
        "INSERT INTO memories (id, project_id, kind, task_id, agent_id, timestamp, summary, "
        "learnings, handoff_note, tags, status, embedding, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            memory.id,
            memory.project_id,
            memory.kind,
            memory.task_id,
            memory.agent_id,
            ts,
            memory.summary,
            learnings_json,
            memory.handoff_note,
            tags_json,
            memory.status,
            emb_bytes,
            ca,
            ua,
        ),
    )
    return memory.id


# ---------------------------------------------------------------------------
# 查詢函式
# ---------------------------------------------------------------------------


def get_memory_by_id(
    conn: sqlite3.Connection,
    memory_id: str,
) -> Memory | None:
    """依 id 查詢單筆記憶。回傳 Memory 物件，若不存在回傳 None。"""
    row = conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
    if row is None:
        return None
    return _row_to_memory(row)


def get_active_embeddings(
    conn: sqlite3.Connection,
    kind: MemoryKind,
) -> list[tuple[str, bytes]]:
    """載入同 kind、status='active' 的所有記憶的 (id, embedding)。

    只回傳 embedding IS NOT NULL 的記錄。供 dedup.py 使用。
    """
    rows = conn.execute(
        "SELECT id, embedding FROM memories "
        "WHERE kind=? AND status='active' AND embedding IS NOT NULL "
        "ORDER BY created_at DESC",
        (kind,),
    ).fetchall()
    return [(r["id"], bytes(r["embedding"])) for r in rows]


def get_latest_status_updates(
    conn: sqlite3.Connection,
    limit: int = 20,
) -> list[Memory]:
    """回傳所有 active status_update，以 task_id 去重取最新。

    供 remagraph_status MCP tool 使用。
    """
    rows = conn.execute(
        "SELECT m.* FROM memories m "
        "INNER JOIN ("
        "  SELECT task_id, MAX(created_at) AS max_ts "
        "  FROM memories "
        "  WHERE kind='status_update' AND status='active' "
        "  GROUP BY task_id"
        ") latest ON m.task_id=latest.task_id AND m.created_at=latest.max_ts "
        "WHERE m.kind='status_update' "
        "ORDER BY m.created_at DESC "
        "LIMIT ?",
        (limit,),
    ).fetchall()
    return [_row_to_memory(r) for r in rows]


# ---------------------------------------------------------------------------
# _row_to_memory
# ---------------------------------------------------------------------------


def _row_to_memory(row: sqlite3.Row) -> Memory:
    """將 sqlite3.Row 轉換為 Memory Pydantic 物件。"""
    return Memory(
        id=row["id"],
        project_id=row["project_id"],
        task_id=row["task_id"],
        agent_id=row["agent_id"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        kind=row["kind"],
        summary=row["summary"],
        learnings=json.loads(row["learnings"]),
        handoff_note=row["handoff_note"],
        tags=json.loads(row["tags"]),
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


# ---------------------------------------------------------------------------
# process_store：完整 store 流程
# ---------------------------------------------------------------------------


def process_store(
    request: StoreRequest,
    conn: sqlite3.Connection,
    *,
    skip_safety_check: bool = False,
) -> StoreResponse:
    """執行完整的 remagraph_store 流程：

    1. 便宜仲裁規則（#1, #2, #3, #5）
    2. model2vec 去重（#4）
    3. supersede / invalidate（若適用）
    4. 生成 ID、編碼 embedding
    5. INSERT 寫入 + transaction commit

    回傳 StoreResponse。

    Args:
        skip_safety_check: 僅供 maintenance._record_violation 自身記錄違規時
            使用 —— 略過本函式開頭的 safety_validate_project 呼叫，避免
            「記錄違規」這個內部自我記錄路徑重新觸發同一個目前正在失敗的
            安全驗證，造成 safety_validate_project -> _record_violation ->
            process_store -> safety_validate_project 的無窮遞迴。一般外部
            呼叫者（CLI、MCP server、或任何帶明確 project_id 的呼叫）不得
            傳入，維持預設 False 以保留既有的安全閥門強制行為。
    """
    # 安全閥門（PPLX 共識版）：強制 project + state_dir 對映
    from remagraph.maintenance import safety_validate_project

    if request.project_id and not skip_safety_check:
        safety_validate_project(request.project_id)  # 違規直接 raise SafetyValveError

    # 規則 #1, #2, #3, #5: 便宜仲裁
    arb_result = run_arbitration_rules_cheap(request)
    if not arb_result.passed:
        return StoreResponse(
            status="rejected",
            reason=arb_result.reason,
            detail=arb_result.detail,
        )

    # 規則 #4: model2vec 去重
    dedup_result = check_duplicate(request.summary, request.kind, conn, request.project_id)
    if not dedup_result.passed:
        return StoreResponse(
            status="rejected",
            reason=dedup_result.reason,
            detail=dedup_result.detail,
        )

    now = datetime.now(timezone.utc)

    # 開始 transaction
    conn.execute("BEGIN")

    try:
        # guardrail: 跨 project 碰撞偵測
        if request.project_id and request.project_id != "default":
            other = conn.execute(
                "SELECT project_id FROM memories WHERE task_id=? AND project_id != ? LIMIT 1",
                (request.task_id, request.project_id),
            ).fetchone()
            if other:
                print(f"WARNING: task '{request.task_id}' in other project", file=sys.stderr)

        # supersede（status_update 或 fleet_member：同 task 保留最新 active）
        superseded_ids: list[str] = []
        if request.kind in ("status_update", "fleet_member"):
            result = supersede_for_kind(request.kind, request.project_id, request.task_id, conn)
            if result.superseded_count > 0:
                rows = conn.execute(
                    "SELECT id FROM memories WHERE project_id=? AND task_id=? "
                    "AND kind=? AND status='superseded' "
                    "ORDER BY created_at DESC LIMIT ?",
                    (request.project_id, request.task_id, request.kind, result.superseded_count),
                ).fetchall()
                superseded_ids = [r["id"] for r in rows]

        # invalidate（僅 discovered_constraint）
        invalidated_count = 0
        if request.kind == "discovered_constraint" and request.invalidates:
            inv_result = invalidate_constraints(request.invalidates, conn)
            if isinstance(inv_result, ArbitrationResult):
                conn.execute("ROLLBACK")
                return StoreResponse(
                    status="rejected",
                    reason=inv_result.reason,
                    detail=inv_result.detail,
                )
            invalidated_count = inv_result.invalidated_count

        # 生成 ID
        mem_id = generate_memory_id(conn, now=now)

        # 編碼 embedding
        try:
            emb_bytes = encode_summary(request.summary)
            emb_array: np.ndarray | None = np.frombuffer(emb_bytes, dtype="<f4").copy()
        except Exception:
            # 模型載入失敗應在 check_duplicate 時已觸發
            emb_array = None

        # 建立 Memory 物件
        memory = Memory(
            id=mem_id,
            project_id=request.project_id,
            task_id=request.task_id,
            agent_id=request.agent_id,
            timestamp=now,
            kind=request.kind,
            summary=request.summary,
            learnings=request.learnings,
            handoff_note=request.handoff_note,
            tags=request.tags,
            status="active",
            created_at=now,
            updated_at=now,
        )

        # INSERT
        insert_memory(conn, memory, emb_array)

        conn.execute("COMMIT")

        response = StoreResponse(
            status="stored",
            id=mem_id,
            superseded=superseded_ids,
            invalidated_count=invalidated_count,
        )
        append_audit(response, request)
        return response

    except Exception as e:
        conn.execute("ROLLBACK")
        response = StoreResponse(
            status="error",
            reason="db_error",
            detail=str(e),
        )
        append_audit(response, request)
        return response
