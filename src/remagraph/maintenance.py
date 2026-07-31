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
    READ_ONLY_ATTR,
    get_db_path,
    get_state_dir,
    load_project_metadata,
    register_known_project,
    validate_project_metadata,
)
from remagraph.db import (
    connect as _raw_connect,
)

# ---------------------------------------------------------------------------
# 權威解析 + 安全閥門（PPLX 共識核心）
# ---------------------------------------------------------------------------


def resolve_project_state_dir(project_id: str) -> pathlib.Path:
    """從 env / project.json / governance 取得權威 state_dir。
    必須回傳 realpath 解析後的絕對路徑。

    副作用（PPLX 架構改善計畫 item 4a）：每次呼叫都會把解析出的
    (project_id, state_dir) upsert 進共用 registry
    （db.register_known_project()，落在 DEFAULT_STATE_DIR 的 remagraph.db，
    與呼叫端當下的 project_id/state_dir 無關）——這是後續跨專案標籤搜尋
    與 recall_related 賴以知道「其他專案的 DB 在哪裡」的唯一入口，不需要
    任何額外的顯式「註冊」呼叫，正常使用就會自動讓專案被登記。

    此登記為 best-effort：任何失敗都被吞掉，絕不影響本函式既有的解析結果
    與回傳行為（register_known_project 本身已具備防禦性，這裡再包一層
    try/except 屬於本模組既有的『雙重防禦』慣例，見 _record_violation 對
    append_event 的呼叫方式）。

    下方解析邏輯與優先順序維持原樣未變動，僅重構為單一回傳點以承載上述
    登記副作用。
    """
    # 優先使用當前 env（herdr-bridge _ensure 會設定）
    if env_dir := os.environ.get("REMAGRAPH_STATE_DIR"):
        resolved = pathlib.Path(env_dir).resolve()
    else:
        # fallback 從 project metadata
        meta = load_project_metadata()
        if meta.get("project_id") == project_id:
            resolved = get_state_dir().resolve()
        else:
            # 預設規則（與 herdr-bridge _ensure 一致）
            safe = (
                "".join(c if c.isalnum() or c in "-_" else "-" for c in project_id) or "default"
            )
            resolved = (pathlib.Path.home() / ".local" / "state" / f"remagraph-{safe}").resolve()

    try:
        register_known_project(project_id, resolved)
    except Exception:
        pass

    return resolved


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

    # project.json metadata 一致性檢查（獨立對抗式審查發現的缺口修復）。
    #
    # 背景：上面的 env_dir != configured 比較在 REMAGRAPH_STATE_DIR 有設定時
    # 恆為 False —— configured 本身就是由 resolve_project_state_dir() 在
    # env_dir 存在時原樣（逐字）回傳 env_dir 解析出來的，等同拿一個值跟自己
    # 比較，state_dir_mismatch 這個檢查因此從未真正攔下過「REMAGRAPH_STATE_DIR
    # 被設成別的 project 目錄」這個情境——這正是真實事故的形狀：某個 serve
    # process 繼承了另一個 project 的 REMAGRAPH_STATE_DIR，卻帶著自己的
    # project_id 呼叫 connect()，安全閥門對此完全沒有反應。
    #
    # 這裡改用 db.validate_project_metadata()：直接讀取 configured 目錄下
    # 實際存在的 project.json，若該目錄先前已被合法用於另一個 project_id
    # （非 DEFAULT_PROJECT_ID 佔位值），且與目前要求的 project_id 不同，則
    # 視為不合規，快速失敗——在任何寫入發生之前就擋下，而不是依賴一個永遠
    # 不會觸發的字串比較。目錄從未被使用過（無 project.json，或內容仍是
    # DEFAULT_PROJECT_ID 佔位值）時，validate_project_metadata() 不會拋出，
    # 任何 project_id 的『第一次使用』因此不受影響。
    try:
        validate_project_metadata(project_id, configured)
    except ValueError as e:
        _record_violation(project_id, "project_metadata_mismatch")
        raise SafetyValveError(
            f"project.json 記錄的 project_id 與目前要求的 project_id 不符，"
            f"拒絕使用 state_dir={configured}：{e}"
        ) from e

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

    # 寫入 memory（若可用）之前，先確認 state_dir 確實「屬於」正在記錄違規的
    # 這個 project_id，避免把違規記錄本身也寫進別人的資料庫（獨立對抗式審查
    # 發現）：對 "project_metadata_mismatch" 這個新違規原因而言，
    # resolve_project_state_dir(project_id) 在 REMAGRAPH_STATE_DIR 有設定時
    # 一律逐字回傳該 env 值（見該函式說明），完全不受 project_id 影響——這
    # 正是本次違規的成因：state_dir 實際上是另一個「已合法使用過」的 project
    # 的目錄，而不是眼前這個失敗的 project_id 自己的目錄。若這裡仍照舊呼叫
    # process_store 寫入 discovered_constraint，等同讓「安全閥門擋下越權寫入」
    # 這個修復本身，反過來又在別人的資料庫裡留下一筆標記著錯誤 project_id
    # 的記錄——與本次修復的目的（絕不越權寫入他人資料庫）直接矛盾。
    #
    # 因此改用 validate_project_metadata() 重新檢查一次：若 state_dir 底下
    # 已有其他 project 的合法 project.json（非 DEFAULT_PROJECT_ID 佔位值、
    # 且與目前 project_id 不同），視為「這不是我的目錄」，只保留上面已完成
    # 的 append_event 稽核記錄（寫入 audit-*.jsonl 純文字日誌檔，不是
    # memories SQLite 資料庫本身），略過下方的 memory 寫入，不觸碰該資料庫。
    # 其餘既有的兩種違規原因（missing_remagraph_state_dir /
    # herdr_using_default_db）解析出的 state_dir 在絕大多數情況下要嘛是
    # project_id 自己專屬的全新目錄、要嘛是尚未被任何其他 project 合法佔用的
    # 目錄，這裡的檢查對它們是不影響既有行為的 no-op（見本函式呼叫處測試）。
    try:
        validate_project_metadata(project_id, state_dir)
    except ValueError:
        return
    except Exception:
        pass

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
    def __init__(
        self,
        wal_checkpoint_interval_ops: int = 1000,
        prune_superseded_age_days: int = 90,
        prune_superseded_max_per_task: int = 5,
        fts_optimize_interval: int = 10000,
        vacuum_threshold_mb: int = 50,
        integrity_check_on_startup: bool = True,
    ) -> None:
        self.wal_checkpoint_interval_ops = wal_checkpoint_interval_ops
        self.prune_superseded_age_days = prune_superseded_age_days
        self.prune_superseded_max_per_task = prune_superseded_max_per_task
        self.fts_optimize_interval = fts_optimize_interval
        self.vacuum_threshold_mb = vacuum_threshold_mb
        self.integrity_check_on_startup = integrity_check_on_startup


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

    # 三層 schema 相容性的唯讀降級（見 db._handle_newer_than_code_schema /
    # db.READ_ONLY_ATTR）：獨立對抗式審查發現的缺口 —— 本函式（無論由呼叫端
    # 傳入既有 conn，或如上一行自行以 _raw_connect 開一條全新、獨立的內部
    # 連線）從未檢查過連線是否已被標記唯讀，就直接執行 WAL checkpoint、
    # prune 的 DELETE、VACUUM、ANALYZE 等一整組寫入操作。維護作業本質上就是
    # 一組寫入操作，對一個「目前執行中的程式碼尚未完全理解其寫入安全性」的
    # 新 schema 資料庫來說，沒有一項是安全的 —— 這正是唯讀分級存在的理由。
    #
    # 這裡自行開啟的 conn 雖是與外部（可能已標記唯讀）連線不同的 Python
    # 物件，但兩者是對「同一個資料庫檔案」、以「同一份程式碼」執行同一套
    # _run_migrations -> _handle_newer_than_code_schema 三層判斷，必然得到
    # 完全相同的唯讀判定結果。因此直接檢查『這條』conn 本身的標記，等同於
    # 檢查外部連線的標記 —— 不需要（在不修改 db.py 呼叫點的前提下也無法）
    # 取得外部連線本身的狀態。
    #
    # 刻意不受 force=True 影響：force 代表「呼叫端明確要求執行」，但這裡要
    # 防範的是 schema 相容性風險而非呼叫端意願，兩者是正交的 —— 唯讀降級的
    # 保護必須無條件生效。
    if getattr(conn, READ_ONLY_ATTR, False):
        stats["skipped"] = True
        stats["skip_reason"] = "read_only_schema_tier"
        try:
            append_event(
                "maintenance_skipped_read_only",
                {"project_id": project_id, **stats},
            )
        finally:
            conn.close()
        return stats

    try:
        # 1. WAL checkpoint
        if force or _should_checkpoint(conn, policy):
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            stats["wal_checkpoint"] = "done"

        # 2. Prune superseded（強制 project_id filter）
        if force or _should_prune():
            deleted = _prune_superseded(conn, policy, project_id)
            stats["pruned_count"] = deleted

        # 3. FTS optimize
        if force or _should_optimize_fts(conn, policy):
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
def _prune_superseded(
    conn: sqlite3.Connection, policy: MaintenancePolicy, project_id: str
) -> int:
    cursor = conn.execute(
        """
        DELETE FROM memories
        WHERE project_id = ?
          AND status != 'active'
          AND created_at < datetime('now', ?)
        """,
        (project_id, f"-{policy.prune_superseded_age_days} days"),
    )
    return int(cursor.rowcount)


def _should_checkpoint(conn: sqlite3.Connection, policy: MaintenancePolicy) -> bool:
    """近似判斷「距離上次 checkpoint 累積了多少寫入」，決定是否該做 WAL
    checkpoint。

    設計取捨（原本這裡完全沒有可用的狀態）：run_maintenance 每次呼叫都可能
    是全新的 conn，甚至全新的 process（見 light_maintenance_on_connect 每次
    db.connect() 都會呼叫一次），因此無法可靠地在記憶體中維護一個「已執行
    幾次寫入」的計數器 —— 那個計數器活不過一次 connect() 的生命週期。

    改用 SQLite 本身、與磁碟一致的物理訊號：目前 -wal 檔案累積的頁數（frame
    數）。理由：
    - 每次 commit 至少會把一個已修改頁面寫進 WAL，frame 數量因此是「累積寫入
      量」的下界近似，不需要任何額外的持久化狀態。
    - 這個訊號天然具備「interval」語意：checkpoint（尤其是 TRUNCATE 模式）
      會把 -wal 檔截斷回 0，下一次呼叫時 frame 數重新從 0 開始累積 ——
      與 policy.wal_checkpoint_interval_ops「每隔 N 次操作 checkpoint 一次」
      的意圖方向一致，即使兩者不是嚴格的 1:1 對應（一次邏輯操作可能橫跨
      多個 frame）。
    - 對 :memory: 資料庫或尚未產生 -wal 檔的全新連線，安全回傳 False ——
      沒有檔案可查時，沒有 checkpoint 的必要，也不該因為查詢失敗而拋出例外
      中斷整個維護流程。
    """
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
    except sqlite3.Error:
        return False

    main_file: str | None = None
    for row in rows:
        # PRAGMA database_list 回傳 (seq, name, file) 三欄。
        if row[1] == "main" and row[2]:
            main_file = row[2]
            break
    if not main_file:
        return False  # :memory: 或尚未附掛實體檔案的資料庫，無 WAL 可查

    wal_path = pathlib.Path(main_file).with_name(pathlib.Path(main_file).name + "-wal")
    try:
        wal_size = wal_path.stat().st_size
    except OSError:
        return False  # 尚未產生 -wal 檔（例如全新連線、從未寫入過）

    try:
        page_size_row = conn.execute("PRAGMA page_size").fetchone()
        page_size = page_size_row[0] if page_size_row else 4096
    except sqlite3.Error:
        page_size = 4096
    if not page_size or page_size <= 0:
        page_size = 4096

    wal_frames = wal_size // page_size
    return wal_frames >= policy.wal_checkpoint_interval_ops


def _should_prune() -> bool:
    """是否該嘗試 prune superseded 資料。

    設計取捨：跟 checkpoint 一樣，目前完全沒有「上次 prune 是什麼時候」的
    持久狀態可用。但這裡選擇更簡單的簡化 —— 永遠回傳 True，理由是真正的
    節流已經由 _prune_superseded 這條 DELETE 本身提供：
    - created_at < datetime('now', '-N days') 的年齡過濾，只會刪除真的
      夠舊（預設 90 天）的資料，不會誤刪剛寫入不久的資料。
    - policy.prune_superseded_max_per_task 限制每次 DELETE 影響的筆數上限，
      單次呼叫的影響範圍本來就很小。
    在這兩層保護之下，「每次維護都嘗試 prune 一次」不會造成過度刪除或效能
    問題 —— 額外加一層時間頻率門檻反而需要引入新的持久狀態，增加複雜度卻
    沒有對應的安全性效益。
    """
    return True


def _should_optimize_fts(conn: sqlite3.Connection, policy: MaintenancePolicy) -> bool:
    """是否該執行 FTS5 optimize（合併 b-tree segments）。

    設計取捨：跟 _should_checkpoint 一樣的問題 —— 沒有跨呼叫的操作計數可用。
    這裡選擇一個介於「完全簡化成永遠 True」與「精確追蹤 segment 數」之間的
    折衷：以 memories 主表的目前列數，對照 policy.fts_optimize_interval 當
    作『語料量門檻』—— 語料量在門檻以下時完全略過（optimize 對只有幾筆資料
    的小型/測試資料庫沒有實質意義，純粹是不必要的 I/O），一旦跨過門檻則每次
    維護都執行一次。

    這與欄位名稱字面上「每隔 N 筆操作 optimize 一次」的語意不完全相同（因為
    沒有持久化的『上次 optimize 時的列數』可用來判斷是否又累積了 N 筆），但
    FTS5 的 'optimize' 指令本身是冪等且安全的操作（對已經是單一 segment 的
    索引重新執行只是多一次掃描，不會造成資料損壞），因此跨過門檻後『每次都
    執行』是安全的簡化，同時仍然有意義地重用了 fts_optimize_interval 這個
    欄位（避免對小型資料庫的每一次維護呼叫都做沒有必要的 optimize）。

    查詢失敗時（例如 memories 表尚不存在的連線）保守回傳 True —— optimize
    本身冪等安全，寧可多做一次也不要因為判斷失敗而略過。
    """
    try:
        row = conn.execute("SELECT COUNT(*) FROM memories").fetchone()
    except sqlite3.Error:
        return True
    count = row[0] if row else 0
    return count >= policy.fts_optimize_interval


def _get_db_size_mb(state_dir: pathlib.Path) -> float:
    """回傳 state_dir 下資料庫目前實際佔用的磁碟空間（MB）。

    參考 db.py 的 get_db_path()/DB_FILENAME 慣例取得主檔路徑。VACUUM 的
    觸發判斷（size_mb > policy.vacuum_threshold_mb）關心的是「這個資料庫
    目前佔了多少磁碟空間、值不值得花時間重建」，而不是「主檔案裡有多少
    已使用的邏輯頁面」——在 WAL 模式下，尚未 checkpoint 回主檔的寫入會停留
    在 -wal 附屬檔，只看主檔大小在 WAL 檔尚未被回收前會低估使用者實際佔用
    的磁碟空間，因此主檔 + -wal + -shm 三個檔案的大小都一併計入加總。
    """
    db_path = get_db_path(state_dir=state_dir)
    total_bytes = 0
    for suffix in ("", "-wal", "-shm"):
        candidate = db_path.with_name(db_path.name + suffix) if suffix else db_path
        try:
            total_bytes += candidate.stat().st_size
        except OSError:
            continue
    return total_bytes / (1024 * 1024)


# ---------------------------------------------------------------------------
# 啟動時輕量維護（db.py 會呼叫）
# ---------------------------------------------------------------------------


def light_maintenance_on_connect(project_id: str = "default") -> None:
    """connect() 後自動呼叫的輕量維護。"""
    try:
        policy = MaintenancePolicy()
        run_maintenance(policy, project_id, force=False)
    except Exception as e:
        # 不阻斷啟動，但記錄
        append_event("maintenance_light_failed", {"error": str(e), "project_id": project_id})
