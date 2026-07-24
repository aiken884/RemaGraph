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
from typing import Any

from remagraph.models import StoreRequest, StoreResponse


def _audit_path(state_dir: Path | None = None) -> Path:
    """回傳 audit-YYYYMM.jsonl 的絕對路徑（按月分檔，自動 rotation）。

    優先序與 db.get_db_path 一致：環境變數 REMAGRAPH_STATE_DIR 覆蓋預設
    ~/.local/state/remagraph/，避免測試或多實例情境寫入真實使用者目錄。

    Args:
        state_dir: 當呼叫端已經權威解析出目標目錄時（例如
            maintenance._record_violation 已呼叫 resolve_project_state_dir()
            取得與 memory 記錄相同的目錄），可明確傳入以覆蓋上述環境變數
            解析邏輯，確保同一事件的 audit 寫入與 memory 寫入落在同一目錄。
            未傳入（None，預設）時行為與過去完全相同。

    若目錄下存在舊版 audit.jsonl，後續讀取工具需相容兩者。
    """
    if state_dir is not None:
        base = state_dir
    else:
        env_dir = os.environ.get("REMAGRAPH_STATE_DIR")
        base = Path(env_dir) if env_dir else Path.home() / ".local" / "state" / "remagraph"
    month = datetime.now(timezone.utc).strftime("%Y%m")
    return base / f"audit-{month}.jsonl"


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
        "project_id": getattr(request, "project_id", None),
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


def append_event(action: str, detail: dict[str, Any], *, state_dir: Path | None = None) -> None:
    """記錄維護／生命週期事件（非 remagraph_store 交易）到同一 audit.jsonl。

    與 append_audit 共用同一份 audit-YYYYMM.jsonl（同路徑、同 rotation、
    同 0600/0700 權限、append-only、絕不拋出例外的慣例），但用於
    remagraph_store 之外的事件（例如安全閥門違規、維護完成/失敗）。

    append_audit 是專為 StoreResponse/StoreRequest 設計的型別化 writer，
    不應被鴨子定型濫用；本函式才是通用維護事件應呼叫的入口。

    Args:
        action: 事件名稱，例如 "safety_violation"、"maintenance_completed"、
            "maintenance_light_failed"。
        detail: 事件詳細內容（純資料，不含 traceback）。detail 內任何字串值
            都會先套用 _sanitize_detail 移除 traceback，實際在程式碼中落實
            （而非僅靠呼叫端自律）與 append_audit 相同的不洩漏 traceback
            保證。
        state_dir: 選擇性覆蓋目標目錄（傳遞給 _audit_path）。預設 None，
            行為與過去完全相同（由環境變數 REMAGRAPH_STATE_DIR 決定）。僅
            當呼叫端已經權威解析出與同一事件的其他寫入（例如 memory 記錄）
            相同的目錄時才應傳入，確保兩者落在同一目錄，不各自重新推導。

    此函式保證絕不拋出例外，也絕不寫入半殘/損毀的行 —— record 會先在記憶體
    中完整序列化成字串（json.dumps），只有序列化成功才會開檔寫入；若
    detail 內含不可 JSON 序列化的值（例如誤傳了例外物件），序列化會在開檔
    前就失敗，事件會被靜默捨棄，不會留下不完整的 JSON 片段。
    """
    sanitized_detail: dict[str, Any] = {
        key: (_sanitize_detail(value) if isinstance(value, str) else value)
        for key, value in detail.items()
    }
    record: dict[str, Any] = {
        "action": action,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **sanitized_detail,
    }

    try:
        # 先在記憶體中完整序列化；失敗（TypeError/ValueError）不會碰到檔案，
        # 因此絕不會留下半殘的行。
        serialized = json.dumps(record, ensure_ascii=False)
        path = _audit_path(state_dir)
        _ensure_dir(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(serialized)
            f.write("\n")
        os.chmod(path, 0o600)
    except (OSError, TypeError, ValueError):
        pass  # 審計寫入失敗（含序列化失敗）不應中斷主流程
