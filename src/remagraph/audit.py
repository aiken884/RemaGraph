# SPDX-License-Identifier: Apache-2.0
"""自管 audit writer —— 在 store transaction commit 之後寫入 audit.jsonl。

對外提供 append_audit API：
- 僅記錄 stored / error 兩種 status（不記錄 rejected）
- 路徑 ~/.local/state/remagraph/audit.jsonl
- 目錄權限 0700，檔案權限 0600
- 不含 traceback
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from remagraph.models import StoreRequest, StoreResponse


def _audit_path() -> Path:
    """回傳 audit.jsonl 的絕對路徑。

    優先序與 db.get_db_path 一致：環境變數 REMAGRAPH_STATE_DIR 覆蓋預設
    ~/.local/state/remagraph/，避免測試或多實例情境寫入真實使用者目錄。
    """
    env_dir = os.environ.get("REMAGRAPH_STATE_DIR")
    base = Path(env_dir) if env_dir else Path.home() / ".local" / "state" / "remagraph"
    return base / "audit.jsonl"


def _ensure_dir(path: Path) -> None:
    """確保目錄存在且權限為 0700。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)


def _sanitize_detail(detail: str | None) -> str | None:
    """移除 traceback，只保留錯誤訊息本身。"""
    if detail is None:
        return None
    # 若包含 traceback 特徵，擷取最後一行的實際錯誤訊息
    if "Traceback (most recent call last)" in detail:
        lines = detail.split("\n")
        for line in reversed(lines):
            stripped = line.strip()
            if stripped and not stripped.startswith("File ") and "Traceback" not in stripped:
                return stripped
        return None
    return detail


def append_audit(response: StoreResponse, request: StoreRequest) -> None:
    """在 transaction commit 之後呼叫，寫入 audit.jsonl。

    僅記錄 stored / error status，不記錄 rejected。
    此函式不應拋出例外 —— 寫入失敗僅靜默忽略。
    """
    if response.status not in ("stored", "error"):
        return

    record: dict[str, str | None] = {
        "action": "remagraph_store",
        "task_id": request.task_id,
        "agent_id": request.agent_id,
        "kind": request.kind,
        "status": response.status,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    if response.status == "stored":
        record["id"] = response.id
    else:
        record["reason"] = response.reason
        detail = _sanitize_detail(response.detail)
        if detail:
            record["detail"] = detail

    try:
        path = _audit_path()
        _ensure_dir(path)
        with open(path, "a", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False)
            f.write("\n")
        os.chmod(path, 0o600)
    except OSError:
        pass  # 審計寫入失敗不應中斷主流程
