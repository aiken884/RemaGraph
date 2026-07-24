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

    @field_validator("project_id", "task_id", "agent_id", mode="before")
    @classmethod
    def _validate_id(cls, v: object) -> str:
        if not isinstance(v, str) or not _TASK_ID_RE.match(v):
            raise ValueError(
                f"project_id/task_id/agent_id must match {_TASK_ID_RE.pattern}, got {v!r}"
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
    status: MemoryStatus | None = None
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
