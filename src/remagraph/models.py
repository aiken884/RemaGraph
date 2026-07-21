"""Pydantic schema"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

MemoryKind = Literal["task_handoff", "status_update", "discovered_constraint"]
MemoryStatus = Literal["active", "superseded", "invalidated"]


class Memory(BaseModel):
    """單筆記憶記錄。

    對應 DESIGN.md「記憶 Schema」章節：三種 ``kind``
    （``task_handoff`` / ``status_update`` / ``discovered_constraint``），
    每條記錄包含 id、task_id、agent_id、timestamp、kind、summary、
    learnings、handoff_note、tags、status。

    注意：此類別只定義資料形狀，不包含仲裁規則（五條仲裁規則見
    ``arbitration.py``），例如 summary/handoff_note 的長度門檻不在此驗證。
    """

    id: str
    task_id: str
    agent_id: str
    timestamp: datetime
    kind: MemoryKind
    summary: str
    learnings: list[str] = Field(default_factory=list)
    handoff_note: str
    tags: list[str] = Field(default_factory=list)
    status: MemoryStatus = "active"
