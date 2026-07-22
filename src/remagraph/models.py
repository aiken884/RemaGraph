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

MemoryKind = Literal["task_handoff", "status_update", "discovered_constraint"]
MemoryStatus = Literal["active", "superseded", "invalidated"]

# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


_TASK_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


class StoreRequest(BaseModel):
    """remagraph_store 的輸入。不含 id、timestamp、status、embedding（伺服器端填入）。"""

    task_id: str
    agent_id: str
    kind: MemoryKind
    summary: str
    learnings: list[str] = Field(default_factory=list)
    handoff_note: str = ""
    tags: list[str] = Field(default_factory=list)
    invalidates: list[str] | None = None

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
    agent_id: str | None = None
    task_id: str | None = None


class SearchResponse(BaseModel):
    """remagraph_search 的回應。"""

    results: list[dict[str, Any]]
    has_more: bool


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class StatusRequest(BaseModel):
    """remagraph_status 的輸入。"""

    limit: int = Field(default=20, ge=1, le=100)


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
