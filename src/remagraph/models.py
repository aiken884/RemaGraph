# SPDX-License-Identifier: Apache-2.0
"""Pydantic schema — 定義所有 MCP tool 的 request/response 模型與核心資料型別。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Literals
# ---------------------------------------------------------------------------

MemoryKind = Literal["task_handoff", "status_update", "discovered_constraint", "fleet_member"]
MemoryStatus = Literal["active", "superseded", "invalidated"]

# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


_TASK_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")

# project_id 額外允許底線開頭（第二輪驗收掃描）：`_Scripts`、`_Megapower`
# 是常見的目錄命名慣例，post-commit hook 從 repo 目錄名推導、init 建立的
# conventional state dir 也以原名登記——若沿用 task_id 的「字母數字開頭」
# 規則，這類專案每次 commit 的記憶寫回都必然被驗證擋下。task_id/agent_id
# 維持原規則不變。
_PROJECT_ID_RE = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9_-]{0,63}$")


class StoreRequest(BaseModel):
    """remagraph_store 的輸入。不含 id、timestamp、status、embedding（伺服器端填入）。"""

    project_id: str
    task_id: str
    agent_id: str
    kind: MemoryKind
    summary: str
    learnings: list[str] = Field(default_factory=list)
    handoff_note: str = ""
    tags: list[str] = Field(default_factory=list)
    invalidates: list[str] | None = None
    labels: list[str] = Field(default_factory=list)

    @field_validator("project_id", mode="before")
    @classmethod
    def _validate_project_id(cls, v: object) -> str:
        if not isinstance(v, str) or not _PROJECT_ID_RE.match(v):
            raise ValueError(
                f"project_id must match {_PROJECT_ID_RE.pattern}, got {v!r}"
            )
        return v

    @field_validator("task_id", "agent_id", mode="before")
    @classmethod
    def _validate_id(cls, v: object) -> str:
        if not isinstance(v, str) or not _TASK_ID_RE.match(v):
            raise ValueError(
                f"task_id/agent_id must match {_TASK_ID_RE.pattern}, got {v!r}"
            )
        return v

    @field_validator("tags", mode="before")
    @classmethod
    def _validate_tags(cls, v: object) -> list[str]:
        if v is None:
            return []
        if not isinstance(v, list):
            raise ValueError("tags must be a list of strings")
        for i, item in enumerate(v):
            if not isinstance(item, str):
                raise ValueError(f"tags[{i}] must be str, got {type(item).__name__}")
        return v

    @field_validator("labels", mode="before")
    @classmethod
    def _validate_labels_type(cls, v: object) -> list[str]:
        """僅驗證型別（list[str]），與既有 tags 驗證器一致。

        刻意不在此處驗證 namespace:value 格式本身 —— labels 與 tags 不同的
        地方在於 labels 有格式要求（見 D02/PPLX item 4b 設計），但格式驗證
        刻意延後到 arbitration.run_arbitration_rules_cheap 內的
        validate_labels()，讓格式不符能走本專案既有的
        StoreResponse(status="rejected", reason=..., detail=...) 優雅拒絕
        路徑（與其餘仲裁規則一致），而不是在 pydantic 建構階段就讓
        ValidationError 直接往外拋、繞過 process_store 的仲裁/稽核流程。
        """
        if v is None:
            return []
        if not isinstance(v, list):
            raise ValueError("labels must be a list of strings")
        for i, item in enumerate(v):
            if not isinstance(item, str):
                raise ValueError(f"labels[{i}] must be str, got {type(item).__name__}")
        return v


class StoreResponse(BaseModel):
    """remagraph_store 的回應。"""

    status: Literal["stored", "rejected", "error"]
    id: str | None = None
    superseded: list[str] = Field(default_factory=list)
    invalidated_count: int = 0
    reason: str | None = None
    detail: str | None = None


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class SearchRequest(BaseModel):
    """remagraph_search 的輸入。"""

    query: str = ""
    top_k: int = Field(default=20, ge=1, le=100)
    kind: MemoryKind | None = None
    status: MemoryStatus | Literal["all"] | None = None
    """None（預設）＝只回 active（與列表模式一致的存活語意）；明確傳
    "all" 才一次涵蓋 active/superseded/invalidated——診斷把 FTS 路徑的
    status=None 從「不過濾」收斂成「預設 active」後，補上這個顯式逃生口，
    保留「全文檢索歷史記憶」的既有能力（對抗式審查發現的表達力缺口）。"""
    tags: list[str] | None = None
    project_id: str | None = None
    agent_id: str | None = None
    task_id: str | None = None
    cross_project_label: str | None = None
    """PPLX 架構改善計畫 item 4b：若提供，走跨專案標籤搜尋路徑（見
    search.search_memories 內的分支），完全獨立於既有的 all_projects 語意
    —— all_projects 只是移除『目前這一個資料庫檔案內』的 project_id 過濾，
    不會開啟其他 project 各自獨立的資料庫檔案；cross_project_label 則會透過
    db.list_known_projects() / db.connect_foreign_project_readonly()
    （item 4a 的 registry 機制）真正開啟其他 project 的資料庫檔案來查詢。
    兩者可視為互不相干的兩個維度，不互相取代。"""
    include_related: bool = False
    """PPLX 架構改善計畫 item 5：若為 True，在對『目前這個專案』執行正常的
    FTS 全文查詢之外，額外沿 project_edges（db.recall_related）traversal
    fan out 到明確宣告為圖形關聯、且在 related_hops 之內的專案。與
    cross_project_label（對『所有』已知專案無差別 fan-out、依 label 精確
    比對）、all_projects（只移除目前這一個資料庫檔案內的 project_id 過濾）
    是三個完全獨立、互不觸發彼此的維度——見
    search._search_related_projects/_search_cross_project_by_label 的
    分派邏輯與 tests/test_project_edges_and_recall_related.py 的解耦
    regression test。

    需要 project_id 才能作為 traversal 起點；project_id 為 None 時視為
    呼叫端使用錯誤（沒有『我』這個起點可以走），優雅退化為一般搜尋（不
    展開 related fan-out），記一筆 warning log，不拋出例外。"""
    related_hops: int = Field(default=1, ge=1, le=5)
    """include_related=True 時的 BFS traversal 深度上限。預設 1（僅限直接
    宣告的關聯）。上限 5 是防禦性設計：project_edges 這張表的規模在正常
    使用情境下遠小於 project_registry（需要人工透過 `remagraph link`
    顯式宣告，不像 project_registry 是每次 connect 自動登記），但仍設一個
    保守上限，避免呼叫端不慎傳入過大的 hops 造成不必要的多層 BFS 查詢
    往返。include_related=False 時此欄位無意義。"""
    fanout_cap: int | None = None
    """跨專案 fan-out（cross_project_label / include_related）單次搜尋最多
    開幾個『其他』專案資料庫連線的上限覆寫（PPLX 架構審查共識，BUG 2 修復）。
    None（預設）時由 search.resolve_fanout_cap() 依序改用
    REMAGRAPH_FANOUT_CAP 環境變數、再退回 search._CROSS_PROJECT_FANOUT_CAP
    預設值（50）。提供明確數值時（對應 CLI --fanout-cap）優先於環境變數。
    最終一律 clamp 到硬性上限（預設 200，僅能由 REMAGRAPH_FANOUT_HARD_CAP
    環境變數明確 opt-in 提高），不允許 0/負數作為「不限」的逃生艙口
    —— 見 search.resolve_fanout_cap() 完整說明。"""


class SearchResponse(BaseModel):
    """remagraph_search 的回應。"""

    results: list[dict[str, Any]]
    has_more: bool
    cross_project_fanout_capped: bool = False
    """僅在使用 cross_project_label 時有意義：若已知專案數超過
    search._CROSS_PROJECT_FANOUT_CAP（PPLX 研究引用 Azure DevOps 對跨專案
    連結查詢成本的官方警示，見 search.py 模組說明），本次搜尋只會查詢前
    N 個已知專案並在此標記 True，讓呼叫端知道結果可能不完整，而不是悄悄
    截斷、佯裝『已涵蓋所有已知專案』。一般搜尋（未使用 cross_project_label）
    恆為 False，對既有呼叫端而言是完全加法、向後相容的欄位。"""
    candidates_total: int = 0
    """本次 fan-out（cross_project_label / include_related）考慮的候選『其他』
    專案總數（BUG 2 修復，PPLX 架構審查共識）。非 fan-out 搜尋恆為 0。"""
    candidates_searched: int = 0
    """本次 fan-out 實際開連線查詢的候選專案數（撞到 fanout_cap 前）。非
    fan-out 搜尋恆為 0。"""
    candidates_skipped: int = 0
    """candidates_total - candidates_searched —— 因撞到 fanout_cap 而未查詢
    的候選專案數。非 fan-out 搜尋恆為 0。單獨看 results 陣列或
    cross_project_fanout_capped 皆無法得知「差多少」，此欄位讓呼叫端能明確
    量化搜尋不完整的程度，而不必只能靠 exit code 或布林值猜測。"""


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class StatusRequest(BaseModel):
    """remagraph_status 的輸入。"""

    limit: int = Field(default=20, ge=1, le=100)
    project_id: str | None = None


class StatusResponse(BaseModel):
    """remagraph_status 的回應。"""

    latest: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


class Memory(BaseModel):
    """完整記憶記錄（含伺服器端填入的欄位）。

    注意：embedding 不在此 Pydantic model 中（以 BLOB 形式獨立儲存）。
    """

    id: str
    project_id: str
    task_id: str
    agent_id: str
    timestamp: datetime
    kind: MemoryKind
    summary: str
    learnings: list[str] = Field(default_factory=list)
    handoff_note: str = ""
    tags: list[str] = Field(default_factory=list)
    status: MemoryStatus = "active"
    created_at: datetime
    updated_at: datetime
