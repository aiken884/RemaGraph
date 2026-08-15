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
    "invalidates_not_active",
    "invalid_label",
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

# PPLX 架構改善計畫 item 4b：labels 為命名空間化字串（namespace:value），
# 例如 dep:opencode、topic:auth、kind:bug。namespace 一律小寫字母（刻意
# 是一組小、受控的字首集合，避免標籤本身隨時間演變成破碎、不一致的自由
# 格式字串——這是 PPLX 研究對這類跨專案共用標籤的明確建議）；value 允許
# 大小寫英數字、底線、連字號，與既有 project_id/task_id/agent_id 慣例
# （models._TASK_ID_RE）的字元集一致，方便沿用既有的合理字元範圍認知。
#
# 尾端刻意用 \Z 而非 $：Python re 的 `$`（非 MULTILINE 模式）除了匹配
# 字串結尾，也會匹配『結尾前恰有一個換行字元』的位置——也就是說，即使
# `\n` 並不在上面宣告的 value 字元類別 [a-zA-Z0-9_-] 之內，
# `^[a-z]+:[a-zA-Z0-9_-]+$` 仍會誤判 "dep:foo\n" 為格式合法（`match()`
# 只驗證字串開頭，不要求整個字串被消耗，`$` 又額外放行結尾前一個換行）。
# `\Z` 只匹配『絕對的字串結尾』，沒有這個換行例外，是這裡唯一正確的錨點。
LABEL_REGEX = re.compile(r"^[a-z]+:[a-zA-Z0-9_-]+\Z")

# labels 長度上限（PPLX 架構改善計畫 item 4b 硬化項目）：與 models.py
# _TASK_ID_RE 對 project_id/task_id/agent_id 既有的 64 字元上限慣例一致
# （見 models.py 的 `{0,63}` → 總長 64）。LABEL_REGEX 本身的字元類別
# （[a-zA-Z0-9_-]+）對長度沒有上限，若不額外檢查，一個數十/數百 KB 的
# 字串會被判定為合法 label 並寫入 memory_labels 表——套用與既有欄位一致
# 的合理上限，避免這個縫隙。
LABEL_MAX_LENGTH = 64

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
            detail=f"summary must be >= 30 characters, currently {length}",
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
            detail="learnings requires at least one non-empty entry",
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
            detail=f"handoff_note must be >= 20 characters, currently {length}",
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
            detail=f"agent_id length must be between 3 and 64, currently {len(agent_id)}",
        )

    if not AGENT_ID_REGEX.match(agent_id):
        return ArbitrationResult(
            passed=False,
            reason="invalid_agent_id",
            detail=(
                "agent_id format is invalid; only lowercase alphanumeric "
                "characters, underscores, and hyphens are allowed: ^[a-z0-9_-]+$"
            ),
        )

    return ArbitrationResult(passed=True)


# ---------------------------------------------------------------------------
# labels 格式驗證（PPLX 架構改善計畫 item 4b，非 D02 原始五條仲裁規則之一，
# 但沿用同一套 ArbitrationResult 優雅拒絕慣例）
# ---------------------------------------------------------------------------


def validate_labels(labels: list[str]) -> ArbitrationResult:
    """驗證 labels 清單內每個字串是否符合 namespace:value 格式
    （見 LABEL_REGEX，例如 dep:opencode、topic:auth、kind:bug），且長度不超過
    LABEL_MAX_LENGTH。

    長度檢查沿用同一個 reason="invalid_label"（而非另立
    "label_too_long"）：對呼叫端而言，兩者都是『這個 label 不符合本系統對
    labels 的格式要求』這同一件事的兩種呈現方式，detail 欄位已足以說明
    究竟是格式不符還是超長；沒有必要為此新增一個 ArbitrationReason
    Literal 成員，讓呼叫端多處理一種 reason 值。

    設計決策 —— 整批拒絕 vs. 靜默跳過單一格式不符的 label：
    本函式選擇只要有任一 label 不符格式，就讓整個 store 請求被拒絕（透過
    ArbitrationResult(passed=False, reason="invalid_label", ...)，與其餘
    仲裁規則走同一條 StoreResponse(status="rejected") 路徑），而不是悄悄
    跳過那一個壞掉的 label、只留下合法的繼續寫入。理由：

    1. labels 存在的價值就是『受控詞彙』（一小組固定 namespace，例如
       dep/topic/kind），目的是避免長期演變成破碎、不一致的自由格式標籤
       （PPLX 研究的核心建議）。若允許格式錯誤的 label 被悄悄丟棄，呼叫端
       永遠不會得知自己用錯了格式，久而久之會出現『以為存進去了、其實沒有』
       的標籤，這正是『受控詞彙』想避免的破碎化問題本身，靜默跳過反而在
       長期上助長了它。
    2. 與本模組既有慣例一致：project_id/task_id/agent_id 等有明確格式
       要求的欄位，本模組（models._TASK_ID_RE 的 field_validator、本檔的
       validate_agent_id）一律硬性拒絕格式不符的輸入，而非略過或靜默改寫。
       labels 既然也有明確的格式要求（不同於完全自由格式的 tags 欄位），
       延續『格式有要求 → 硬性拒絕』這條既有慣例最一致。
    3. 與 arbitration.py 既有規則的失敗模式一致（即整個 store 操作失敗、
       但以乾淨的 StoreResponse 呈現，不是未捕捉例外——呼叫端拿到清楚的
       reason="invalid_label" + detail 說明哪個 label、期待什麼格式，可
       立即修正重試，不會有任何資料被『部分寫入』的曖昧狀態）。
    """
    for label in labels:
        if len(label) > LABEL_MAX_LENGTH:
            return ArbitrationResult(
                passed=False,
                reason="invalid_label",
                detail=(
                    f"label length must be <= {LABEL_MAX_LENGTH} characters, "
                    f"currently {len(label)} characters: {label[:80]!r}..."
                ),
            )
        if not LABEL_REGEX.match(label):
            return ArbitrationResult(
                passed=False,
                reason="invalid_label",
                detail=(
                    f"label {label!r} does not match the namespace format "
                    f"{LABEL_REGEX.pattern} (e.g. 'dep:opencode', 'topic:auth', "
                    "'kind:bug')"
                ),
            )
    return ArbitrationResult(passed=True)


# ---------------------------------------------------------------------------
# 便宜規則組合（先執行規則 #1, #2, #3, #5，最後才 #4）
# ---------------------------------------------------------------------------


def run_arbitration_rules_cheap(request: StoreRequest) -> ArbitrationResult:
    """依序執行便宜仲裁規則（#1, #2, #3, #5 + labels 格式），任一失敗即停止。

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

    # labels 格式（item 4b，非原始五條規則之一，見上方 validate_labels 說明）
    result = validate_labels(request.labels)
    if not result.passed:
        return result

    return ArbitrationResult(passed=True)


# ---------------------------------------------------------------------------
# supersede / invalidate（生命週期管理）
# ---------------------------------------------------------------------------


def supersede_for_kind(
    kind: str, project_id: str, task_id: str, conn: sqlite3.Connection
) -> SupersedeResult:
    """將同 project+task_id 的該 kind 舊 active 記錄 supersede。
    用於 status_update / fleet_member。
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    cursor = conn.execute(
        "UPDATE memories SET status='superseded', updated_at=? "
        "WHERE project_id=? AND task_id=? AND kind=? AND status='active'",
        (now, project_id, task_id, kind),
    )
    return SupersedeResult(superseded_count=cursor.rowcount)


def supersede_status_updates(
    project_id: str, task_id: str, conn: sqlite3.Connection
) -> SupersedeResult:
    """向後相容別名：僅 supersede status_update。"""
    return supersede_for_kind("status_update", project_id, task_id, conn)


def cleanup_superseded(conn: sqlite3.Connection, max_age_days: int = 90) -> int:
    """清理超過 max_age_days 的 superseded 記錄。

    回傳被刪除的筆數。僅作用於非 active 狀態且建立時間超過指定天數的記錄。
    """
    cursor = conn.execute(
        "DELETE FROM memories WHERE status != 'active' AND created_at < datetime('now', ?)",
        (f"-{max_age_days} days",),
    )
    return cursor.rowcount


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
        f"SELECT id, kind, status FROM memories WHERE id IN ({placeholders})",
        invalidate_ids,
    ).fetchall()

    found_ids = {r["id"] for r in rows}
    for mid in invalidate_ids:
        if mid not in found_ids:
            return ArbitrationResult(
                passed=False,
                reason="invalidates_not_found",
                detail=f"the memory specified by invalidates does not exist: {mid}",
            )

    # 驗證 kind 都是 discovered_constraint
    for r in rows:
        if r["kind"] != "discovered_constraint":
            return ArbitrationResult(
                passed=False,
                reason="invalidates_kind_mismatch",
                detail=(
                    "only memories of kind discovered_constraint can be "
                    f"invalidated; {r['id']} has kind {r['kind']}"
                ),
            )

    # 已非 active 的 constraint：不擋整筆 store（維持並行/重放冪等——兩個
    # agent 平行發現同一過時 constraint、各自帶 invalidates 寫入時，後到者
    # 的記憶本體不該因此被拒），也不列進回報。invalidated_ids 只包含本次
    # 「實際」被更新的 id，與下方 UPDATE 的 status='active' 條件完全對齊
    # ——修復診斷發現的矛盾回報（修復前 invalidated_ids 列出全部請求 id、
    # 實際更新 0 筆，呼叫端誤信已完成失效）。整筆拒絕的第一版修法經
    # 對抗式審查指出對跨塔並行寫入是可用性退步，改為此冪等版本。
    active_ids = [r["id"] for r in rows if r["status"] == "active"]

    # 執行 invalidate
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    cursor = conn.execute(
        f"UPDATE memories SET status='invalidated', updated_at=? "
        f"WHERE id IN ({placeholders}) AND kind='discovered_constraint' AND status='active'",
        [now] + invalidate_ids,
    )

    return InvalidateResult(
        invalidated_count=cursor.rowcount,
        invalidated_ids=active_ids,
    )
