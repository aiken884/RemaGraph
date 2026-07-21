# SPDX-License-Identifier: Apache-2.0
"""五條仲裁規則。

本模組負責：
- 規則 #1: summary 長度門檻
- 規則 #2: learnings 非空
- 規則 #3: handoff_note 長度門檻（僅 task_handoff）
- 規則 #4: model2vec 去重（由 dedup.py 實作）
- 規則 #5: agent_id 格式 + Lazy Registration
- status_update supersede
- discovered_constraint invalidates
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from remagraph.models import MemoryKind, StoreRequest

# ---------------------------------------------------------------------------
# 型別
# ---------------------------------------------------------------------------

ArbitrationReason = Literal[
    "summary_too_short",
    "learnings_empty",
    "handoff_note_too_short",
    "duplicate_content",
    "invalid_agent_id",
    "invalidates_not_found",
    "invalidates_kind_mismatch",
]


@dataclass
class ArbitrationResult:
    """仲裁結果。"""
    passed: bool
    reason: ArbitrationReason | None = None
    detail: str | None = None
    closest_memory_id: str | None = None
    closest_similarity: float | None = None


@dataclass
class SupersedeResult:
    """status_update supersede 結果。"""
    superseded_count: int


@dataclass
class InvalidateResult:
    """discovered_constraint invalidates 結果。"""
    invalidated_count: int
    invalidated_ids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 常數
# ---------------------------------------------------------------------------

AGENT_ID_REGEX = re.compile(r"^[a-z0-9_-]+$")

# ---------------------------------------------------------------------------
# 規則 #1: summary 長度門檻
# ---------------------------------------------------------------------------


def validate_summary_length(summary: str) -> ArbitrationResult:
    """規則 #1：summary ≥ 30 Unicode codepoint（strip 後計數）。"""
    length = len(summary.strip())
    if length < 30:
        return ArbitrationResult(
            passed=False,
            reason="summary_too_short",
            detail=f"summary 需 ≥ 30 字，目前 {length} 字",
        )
    return ArbitrationResult(passed=True)


# ---------------------------------------------------------------------------
# 規則 #2: learnings 非空
# ---------------------------------------------------------------------------


def validate_learnings(learnings: list[str]) -> ArbitrationResult:
    """規則 #2：learnings 至少一筆非空白元素。"""
    valid = [s for s in learnings if s.strip()]
    if not valid:
        return ArbitrationResult(
            passed=False,
            reason="learnings_empty",
            detail="learnings 至少需要一筆非空內容",
        )
    return ArbitrationResult(passed=True)


# ---------------------------------------------------------------------------
# 規則 #3: handoff_note 長度門檻
# ---------------------------------------------------------------------------


def validate_handoff_note(kind: MemoryKind, handoff_note: str) -> ArbitrationResult:
    """規則 #3：kind == task_handoff 時 handoff_note ≥ 20 字。其他 kind 不檢查。"""
    if kind != "task_handoff":
        return ArbitrationResult(passed=True)

    length = len(handoff_note.strip())
    if length < 20:
        return ArbitrationResult(
            passed=False,
            reason="handoff_note_too_short",
            detail=f"handoff_note 需 ≥ 20 字，目前 {length} 字",
        )
    return ArbitrationResult(passed=True)


# ---------------------------------------------------------------------------
# 規則 #5: agent_id 格式
# ---------------------------------------------------------------------------


def validate_agent_id(agent_id: str) -> ArbitrationResult:
    """規則 #5：agent_id 格式 ^[a-z0-9_-]+$，長度 3–64。"""
    if not (3 <= len(agent_id) <= 64):
        return ArbitrationResult(
            passed=False,
            reason="invalid_agent_id",
            detail=f"agent_id 長度需在 3–64 之間，目前 {len(agent_id)}",
        )

    if not AGENT_ID_REGEX.match(agent_id):
        return ArbitrationResult(
            passed=False,
            reason="invalid_agent_id",
            detail="agent_id 格式不符，僅允許小寫英數字元、底線、連字號：^[a-z0-9_-]+$",
        )

    return ArbitrationResult(passed=True)


# ---------------------------------------------------------------------------
# 便宜規則組合（先執行規則 #1, #2, #3, #5，最後才 #4）
# ---------------------------------------------------------------------------


def run_arbitration_rules_cheap(request: StoreRequest) -> ArbitrationResult:
    """依序執行便宜仲裁規則（#1, #2, #3, #5），任一失敗即停止。

    規則 #4 (model2vec 去重) 由 dedup.py 負責，在呼叫此函式後執行。
    """
    # 規則 #1: summary 長度
    result = validate_summary_length(request.summary)
    if not result.passed:
        return result

    # 規則 #2: learnings 非空
    result = validate_learnings(request.learnings)
    if not result.passed:
        return result

    # 規則 #3: handoff_note 長度（僅 task_handoff）
    result = validate_handoff_note(request.kind, request.handoff_note)
    if not result.passed:
        return result

    # 規則 #5: agent_id 格式
    result = validate_agent_id(request.agent_id)
    if not result.passed:
        return result

    return ArbitrationResult(passed=True)


# ---------------------------------------------------------------------------
# supersede / invalidate（生命週期管理）
# ---------------------------------------------------------------------------


def supersede_status_updates(task_id: str, conn: sqlite3.Connection) -> SupersedeResult:
    """將同 task_id 的所有 active status_update 標記為 superseded。

    應在 transaction 內、INSERT 新 status_update 之前呼叫。
    回傳被影響的筆數。
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    cursor = conn.execute(
        "UPDATE memories SET status='superseded', updated_at=? "
        "WHERE task_id=? AND kind='status_update' AND status='active'",
        (now, task_id),
    )
    return SupersedeResult(superseded_count=cursor.rowcount)


def invalidate_constraints(
    invalidate_ids: list[str], conn: sqlite3.Connection
) -> InvalidateResult | ArbitrationResult:
    """驗證 invalidate_ids 都存在且 kind 都是 discovered_constraint。

    若驗證失敗回傳 ArbitrationResult(passed=False, ...)。
    若成功則標記為 invalidated 並回傳 InvalidateResult。
    """
    if not invalidate_ids:
        return InvalidateResult(invalidated_count=0)

    # 驗證所有 id 都存在
    placeholders = ",".join("?" for _ in invalidate_ids)
    rows = conn.execute(
        f"SELECT id, kind FROM memories WHERE id IN ({placeholders})",
        invalidate_ids,
    ).fetchall()

    found_ids = {r["id"] for r in rows}
    for mid in invalidate_ids:
        if mid not in found_ids:
            return ArbitrationResult(
                passed=False,
                reason="invalidates_not_found",
                detail=f"invalidates 指定的記憶不存在：{mid}",
            )

    # 驗證 kind 都是 discovered_constraint
    for r in rows:
        if r["kind"] != "discovered_constraint":
            return ArbitrationResult(
                passed=False,
                reason="invalidates_kind_mismatch",
                detail=(
                    f"只能 invalidate discovered_constraint 類型的記憶，"
                    f"{r['id']} 的 kind 是 {r['kind']}"
                ),
            )

    # 執行 invalidate
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    cursor = conn.execute(
        f"UPDATE memories SET status='invalidated', updated_at=? "
        f"WHERE id IN ({placeholders}) AND kind='discovered_constraint' AND status='active'",
        [now] + invalidate_ids,
    )

    return InvalidateResult(
        invalidated_count=cursor.rowcount,
        invalidated_ids=list(invalidate_ids),
    )
