# SPDX-License-Identifier: Apache-2.0
"""Database 自動維護機制（T-RG-D06 共識版）。

嚴格遵守：
- per-project state_dir 隔離
- project_id 強制 + 所有操作過濾
- 安全閥門阻擋不合規（herdr-* 不得用 default DB）
- 最小干擾 + 可審計
- 與 D01/D02/D04/arbitration/governance 對齊

所有 herdr-bridge 相關啟動點必須先呼叫 _ensure_remagraph_project(project) 並通過 safety_validate。
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
from datetime import datetime, timezone
from typing import Any

from remagraph.audit import append_event
from remagraph.db import (
    connect as _raw_connect,
)
from remagraph.db import (
    get_state_dir,
    load_project_metadata,
)

# ---------------------------------------------------------------------------
# 權威解析 + 安全閥門（PPLX 共識核心）
# ---------------------------------------------------------------------------


def resolve_project_state_dir(project_id: str) -> pathlib.Path:
    """從 env / project.json / governance 取得權威 state_dir。
    必須回傳 realpath 解析後的絕對路徑。
    """
    # 優先使用當前 env（herdr-bridge _ensure 會設定）
    if env_dir := os.environ.get("REMAGRAPH_STATE_DIR"):
        return pathlib.Path(env_dir).resolve()

    # fallback 從 project metadata
    meta = load_project_metadata()
    if meta.get("project_id") == project_id:
        return get_state_dir().resolve()

    # 預設規則（與 herdr-bridge _ensure 一致）
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in project_id) or "default"
    return (pathlib.Path.home() / ".local" / "state" / f"remagraph-{safe}").resolve()


class SafetyValveError(RuntimeError):
    """安全閥門觸發：不合規的 project / state_dir / DB 使用。"""


def safety_validate_project(project_id: str, *, require_env_match: bool = True) -> pathlib.Path:
    """單一權威安全閥門。
    - 驗證 project_id 與 state_dir 完全對映
    - herdr-* 專案嚴禁使用 default DB
    - 違規時寫 audit + discovered_constraint 並 raise
    """
    configured = resolve_project_state_dir(project_id)
    env_dir_str = os.environ.get("REMAGRAPH_STATE_DIR", "")
    env_dir = pathlib.Path(env_dir_str).resolve() if env_dir_str else None

    if require_env_match:
        if not env_dir:
            _record_violation(project_id, "missing_remagraph_state_dir")
            raise SafetyValveError("REMAGRAPH_STATE_DIR 未設定；herdr-* 必須設定正確 state_dir")
        if env_dir != configured:
            _record_violation(project_id, "state_dir_mismatch")
            raise SafetyValveError(
                f"REMAGRAPH_STATE_DIR 與 project 不符: {env_dir} != {configured}"
            )

    if project_id.startswith("herdr-") and configured.name == "remagraph":
        _record_violation(project_id, "herdr_using_default_db")
        raise SafetyValveError("herdr-* 不得使用 default DB，必須用獨立 state_dir")

    return configured


def _record_violation(project_id: str, reason: str) -> None:
    """記錄違規到 audit 與 memory（discovered_constraint）。

    注意：這是「記錄違規已發生」的內部自我記錄路徑 —— 其存在的唯一目的
    就是記錄 safety_validate_project 剛剛失敗這件事，因此絕不能重新觸發
    同一個目前正在失敗的安全驗證，否則會形成
    safety_validate_project -> _record_violation -> process_store ->
    safety_validate_project 的無窮遞迴（同一個違規原因不會因為重新驗證而
    改變）。下方對 _raw_connect 與 process_store 的呼叫因此都明確傳入
    skip_safety_check=True —— 這個略過旗標僅限本函式使用，任何其他呼叫者
    （CLI、MCP server 或帶明確 project_id 的一般呼叫）都不得傳入，安全閥門
    對它們維持完整強制。

    目錄解析只做一次：resolve_project_state_dir(project_id) 是「權威、
    project-aware」的解析器（REMAGRAPH_STATE_DIR 未設定時會 fallback 到
    project 專屬目錄，而非 audit.py 環境變數導向的共用預設目錄）。若在此
    直接呼叫 append_event 而不傳入解析結果，append_event 內部的
    _audit_path() 會各自重新從環境變數推導目錄 —— 對於
    "missing_remagraph_state_dir" 這類原因（定義上就是 REMAGRAPH_STATE_DIR
    未設定），兩邊 fallback 邏輯不一致，會導致同一違規事件的 audit 記錄與
    memory 記錄落在兩個不同目錄。因此這裡先解析一次，再明確傳給
    append_event(state_dir=...)，確保與下方 memory 記錄使用同一目錄。
    """
    try:
        state_dir = resolve_project_state_dir(project_id)
    except Exception:
        # 解析失敗時退回舊行為：讓 append_event 用它自己的環境變數 fallback。
        state_dir = None

    try:
        append_event(
            "safety_violation",
            {"project_id": project_id, "reason": reason},
            state_dir=state_dir,
        )
    except Exception:
        pass

    if state_dir is None:
        return

    # 盡量寫入 memory（若可用）
    try:
        from remagraph.models import StoreRequest
        from remagraph.store import process_store

        req = StoreRequest(
            kind="discovered_constraint",
            task_id=f"safety-{project_id}",
            agent_id="maintenance",
            summary=f"Safety valve triggered: {reason}",
            learnings=[f"project_id={project_id}", f"reason={reason}"],
            project_id=project_id,
            tags=["safety", "violation", reason],
        )
        conn = _raw_connect(
            state_dir,
            skip_maintenance=True,
            skip_safety_check=True,
        )
        process_store(req, conn, skip_safety_check=True)
        conn.close()
    except Exception:
        pass  # 避免維護本身失敗


# ---------------------------------------------------------------------------
# Maintenance Policy & 核心操作（簡化共識版）
# ---------------------------------------------------------------------------


class MaintenancePolicy:
    def __init__(self, **kwargs):
        self.wal_checkpoint_interval_ops = kwargs.get("wal_checkpoint_interval_ops", 1000)
        self.prune_superseded_age_days = kwargs.get("prune_superseded_age_days", 90)
        self.prune_superseded_max_per_task = kwargs.get("prune_superseded_max_per_task", 5)
        self.fts_optimize_interval = kwargs.get("fts_optimize_interval", 10000)
        self.vacuum_threshold_mb = kwargs.get("vacuum_threshold_mb", 50)
        self.integrity_check_on_startup = kwargs.get("integrity_check_on_startup", True)


def run_maintenance(
    policy: MaintenancePolicy,
    project_id: str,
    force: bool = False,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """執行自動維護。**只接受 project_id**，內部自行建立正確 conn。"""
    state_dir = safety_validate_project(project_id)
    if conn is None:
        conn = _raw_connect(state_dir, skip_maintenance=True)
    stats: dict[str, Any] = {
        "project_id": project_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        # 1. WAL checkpoint
        if force or _should_checkpoint(conn):
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            stats["wal_checkpoint"] = "done"

        # 2. Prune superseded（強制 project_id filter）
        if force or _should_prune():
            deleted = _prune_superseded(conn, policy, project_id)
            stats["pruned_count"] = deleted

        # 3. FTS optimize
        if force or _should_optimize_fts():
            conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('optimize')")
            stats["fts_optimized"] = True

        # 4. Vacuum
        size_mb = _get_db_size_mb(state_dir)
        if force or size_mb > policy.vacuum_threshold_mb:
            conn.execute("VACUUM")
            stats["vacuum"] = "done"
            stats["size_before_mb"] = size_mb

        # 5. Analyze
        conn.execute("ANALYZE")

        # 6. Integrity
        if policy.integrity_check_on_startup or force:
            res = conn.execute("PRAGMA quick_check").fetchone()
            stats["integrity"] = res[0] if res else "unknown"
            if stats["integrity"] != "ok":
                _record_violation(project_id, "integrity_failed")
                raise RuntimeError(f"DB integrity failed: {stats['integrity']}")

        append_event("maintenance_completed", {"project_id": project_id, **stats})
        return stats
    finally:
        if conn:
            conn.close()


# 簡化 helper（實際應從 arbitration 共用）
def _prune_superseded(conn, policy, project_id: str) -> int:
    cursor = conn.execute(
        """
        DELETE FROM memories
        WHERE project_id = ?
          AND status != 'active'
          AND created_at < datetime('now', ?)
        """,
        (project_id, f"-{policy.prune_superseded_age_days} days"),
    )
    return cursor.rowcount


def _should_checkpoint(conn: sqlite3.Connection) -> bool: ...
def _should_prune() -> bool: ...
def _should_optimize_fts() -> bool: ...
def _get_db_size_mb(state_dir: pathlib.Path) -> float:
    return 0  # 實作省略，見 db.py 類似邏輯


# ---------------------------------------------------------------------------
# 啟動時輕量維護（db.py 會呼叫）
# ---------------------------------------------------------------------------


def light_maintenance_on_connect(project_id: str = "default") -> None:
    """connect() 後自動呼叫的輕量維護。"""
    try:
        policy = MaintenancePolicy()  # type: ignore[no-untyped-call]
        run_maintenance(policy, project_id, force=False)
    except Exception as e:
        # 不阻斷啟動，但記錄
        append_event("maintenance_light_failed", {"error": str(e), "project_id": project_id})
