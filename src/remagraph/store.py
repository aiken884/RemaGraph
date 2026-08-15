# SPDX-License-Identifier: Apache-2.0
"""SQLite + FTS5 讀寫。

本模組負責：
- 記憶 ID 生成（mem-YYYYMMDD-NNN）
- 記憶的 INSERT / UPDATE（supersede / invalidate）
- 查詢（單筆、embedding 批次、最新 status）
- process_store：完整 store 流程（仲裁 → dedup → 寫入）
- migrate_project_memories：跨專案記憶遷移的共用核心邏輯（CLI
  `migrate-project` 子指令與 MCP `remagraph_migrate_project` tool 共用）

注意：本模組不自行管理 transaction 邊界（migrate_project_memories 內部
自行管理來源/目標各自的 transaction，process_store 內部使用單一 transaction）。
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from remagraph import db as _db
from remagraph.arbitration import (
    ArbitrationResult,
    invalidate_constraints,
    run_arbitration_rules_cheap,
    supersede_for_kind,
)
from remagraph.audit import append_audit
from remagraph.db import READ_ONLY_ATTR, READ_ONLY_DETAIL_ATTR
from remagraph.dedup import check_duplicate, encode_summary
from remagraph.maintenance import safety_validate_project
from remagraph.models import Memory, MemoryKind, StoreRequest, StoreResponse

# ---------------------------------------------------------------------------
# 自訂例外
# ---------------------------------------------------------------------------


class MemoryIDGenerationError(RuntimeError):
    """記憶 ID 生成失敗（例如並發衝突超過重試次數）。"""


# ---------------------------------------------------------------------------
# generate_memory_id
# ---------------------------------------------------------------------------


def generate_memory_id(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
) -> str:
    """生成唯一記憶 ID，格式 mem-YYYYMMDD-NNN。

    應在 transaction 內呼叫以保證並發安全。
    """
    if now is None:
        now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y%m%d")
    prefix = f"mem-{date_str}-%"

    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(id, 14) AS INTEGER)) FROM memories WHERE id LIKE ?",
        (prefix,),
    ).fetchone()

    max_nnn = row[0] if row[0] is not None else 0
    nnn = max_nnn + 1
    return f"mem-{date_str}-{nnn:03d}"


def _remap_collided_memory_id(conn_tgt: sqlite3.Connection, original_id: str) -> str:
    """在目標資料庫為一個 id 已碰撞的遷移記錄配置新 id。

    標準格式（mem-YYYYMMDD-NNN）保留原日期段、取目標庫該日期的 MAX+1
    （與 generate_memory_id 同一取號邏輯）；非標準格式退回附加序號後綴。
    應在目標庫的 transaction 內呼叫。
    """
    m = re.fullmatch(r"(mem-\d{8})-(\d+)", original_id)
    if m:
        date_prefix = m.group(1)
        row = conn_tgt.execute(
            "SELECT MAX(CAST(SUBSTR(id, 14) AS INTEGER)) FROM memories WHERE id LIKE ?",
            (f"{date_prefix}-%",),
        ).fetchone()
        nnn = (row[0] if row[0] is not None else 0) + 1
        return f"{date_prefix}-{nnn:03d}"
    suffix = 2
    while True:
        candidate = f"{original_id}-{suffix}"
        exists = conn_tgt.execute(
            "SELECT 1 FROM memories WHERE id = ?", (candidate,)
        ).fetchone()
        if exists is None:
            return candidate
        suffix += 1


# ---------------------------------------------------------------------------
# insert_memory
# ---------------------------------------------------------------------------


def insert_memory(
    conn: sqlite3.Connection,
    memory: Memory,
    embedding: np.ndarray | None,
) -> str:
    """插入一筆記憶記錄。

    應在 transaction 內呼叫。FTS5 trigger 會自動同步。
    回傳 memory.id。
    """
    learnings_json = json.dumps(memory.learnings, ensure_ascii=False)
    tags_json = json.dumps(memory.tags, ensure_ascii=False)
    ts = memory.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    ca = memory.created_at.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    ua = memory.updated_at.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    emb_bytes: bytes | None = None
    if embedding is not None:
        emb_bytes = embedding.astype(np.float32).tobytes()

    conn.execute(
        "INSERT INTO memories (id, project_id, kind, task_id, agent_id, timestamp, summary, "
        "learnings, handoff_note, tags, status, embedding, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            memory.id,
            memory.project_id,
            memory.kind,
            memory.task_id,
            memory.agent_id,
            ts,
            memory.summary,
            learnings_json,
            memory.handoff_note,
            tags_json,
            memory.status,
            emb_bytes,
            ca,
            ua,
        ),
    )
    return memory.id


# ---------------------------------------------------------------------------
# 查詢函式
# ---------------------------------------------------------------------------


def get_memory_by_id(
    conn: sqlite3.Connection,
    memory_id: str,
) -> Memory | None:
    """依 id 查詢單筆記憶。回傳 Memory 物件，若不存在回傳 None。"""
    row = conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
    if row is None:
        return None
    return _row_to_memory(row)


def get_active_embeddings(
    conn: sqlite3.Connection,
    kind: MemoryKind,
) -> list[tuple[str, bytes]]:
    """載入同 kind、status='active' 的所有記憶的 (id, embedding)。

    只回傳 embedding IS NOT NULL 的記錄。供 dedup.py 使用。
    """
    rows = conn.execute(
        "SELECT id, embedding FROM memories "
        "WHERE kind=? AND status='active' AND embedding IS NOT NULL "
        "ORDER BY created_at DESC",
        (kind,),
    ).fetchall()
    return [(r["id"], bytes(r["embedding"])) for r in rows]


def get_latest_status_updates(
    conn: sqlite3.Connection,
    limit: int = 20,
) -> list[Memory]:
    """回傳所有 active status_update，以 task_id 去重取最新。

    供 remagraph_status MCP tool 使用。
    """
    rows = conn.execute(
        "SELECT m.* FROM memories m "
        "INNER JOIN ("
        "  SELECT task_id, MAX(created_at) AS max_ts "
        "  FROM memories "
        "  WHERE kind='status_update' AND status='active' "
        "  GROUP BY task_id"
        ") latest ON m.task_id=latest.task_id AND m.created_at=latest.max_ts "
        "WHERE m.kind='status_update' "
        "ORDER BY m.created_at DESC "
        "LIMIT ?",
        (limit,),
    ).fetchall()
    return [_row_to_memory(r) for r in rows]


# ---------------------------------------------------------------------------
# _row_to_memory
# ---------------------------------------------------------------------------


def _row_to_memory(row: sqlite3.Row) -> Memory:
    """將 sqlite3.Row 轉換為 Memory Pydantic 物件。"""
    return Memory(
        id=row["id"],
        project_id=row["project_id"],
        task_id=row["task_id"],
        agent_id=row["agent_id"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        kind=row["kind"],
        summary=row["summary"],
        learnings=json.loads(row["learnings"]),
        handoff_note=row["handoff_note"],
        tags=json.loads(row["tags"]),
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


# ---------------------------------------------------------------------------
# process_store：完整 store 流程
# ---------------------------------------------------------------------------


def process_store(
    request: StoreRequest,
    conn: sqlite3.Connection,
    *,
    skip_safety_check: bool = False,
) -> StoreResponse:
    """執行完整的 remagraph_store 流程：

    1. 便宜仲裁規則（#1, #2, #3, #5）
    2. model2vec 去重（#4）
    3. supersede / invalidate（若適用）
    4. 生成 ID、編碼 embedding
    5. INSERT 寫入 + transaction commit

    回傳 StoreResponse。

    Args:
        skip_safety_check: 僅供 maintenance._record_violation 自身記錄違規時
            使用 —— 略過本函式開頭的 safety_validate_project 呼叫，避免
            「記錄違規」這個內部自我記錄路徑重新觸發同一個目前正在失敗的
            安全驗證，造成 safety_validate_project -> _record_violation ->
            process_store -> safety_validate_project 的無窮遞迴。一般外部
            呼叫者（CLI、MCP server、或任何帶明確 project_id 的呼叫）不得
            傳入，維持預設 False 以保留既有的安全閥門強制行為。
    """
    # 唯讀降級檢查（PPLX 架構改善計畫 item 2）：必須是本函式最前面執行的
    # 檢查 —— 早於安全閥門、早於仲裁規則、早於 model2vec 去重（規則 #4），
    # 完全不嘗試任何 transaction。db.connect() 在三層版本相容性判斷得出
    # 「讀相容但寫不安全」（tier 2）結論時，會在連線物件上掛
    # db.READ_ONLY_ATTR 標記（見 db._handle_newer_than_code_schema）。
    # 沿用既有的 StoreResponse.status="rejected"（不新增列舉值，避免牽動
    # audit.append_audit 對 status 的 switch），reason 使用專屬字串
    # "read_only_mode" 供呼叫端區分。
    if getattr(conn, READ_ONLY_ATTR, False):
        detail = getattr(
            conn,
            READ_ONLY_DETAIL_ATTR,
            "This connection is currently in read-only mode (the database "
            "schema has been upgraded beyond this code's write-compatible "
            "version); this write has been rejected. Please upgrade the "
            "remagraph package and retry.",
        )
        return StoreResponse(
            status="rejected",
            reason="read_only_mode",
            detail=detail,
        )

    # 安全閥門（PPLX 共識版）：強制 project + state_dir 對映
    from remagraph.maintenance import safety_validate_project

    if request.project_id and not skip_safety_check:
        safety_validate_project(request.project_id)  # 違規直接 raise SafetyValveError

    # 規則 #1, #2, #3, #5: 便宜仲裁
    arb_result = run_arbitration_rules_cheap(request)
    if not arb_result.passed:
        return StoreResponse(
            status="rejected",
            reason=arb_result.reason,
            detail=arb_result.detail,
        )

    # 規則 #4: model2vec 去重
    dedup_result = check_duplicate(request.summary, request.kind, conn, request.project_id)
    if not dedup_result.passed:
        return StoreResponse(
            status="rejected",
            reason=dedup_result.reason,
            detail=dedup_result.detail,
        )

    now = datetime.now(timezone.utc)

    # 開始 transaction。BEGIN IMMEDIATE 而非 deferred BEGIN：
    # generate_memory_id 的 SELECT MAX 在 deferred 交易下不取寫鎖，兩個
    # process 併發 store 會讀到相同 MAX、算出相同 id，後寫入者撞 PRIMARY
    # KEY 而整筆失敗（診斷發現；MemoryIDGenerationError docstring 宣稱的
    # 重試機制實際上不存在）。IMMEDIATE 讓寫鎖在交易一開始就取得，
    # 序列化整段「取號 + 插入」。
    conn.execute("BEGIN IMMEDIATE")

    try:
        # guardrail: 跨 project 碰撞偵測
        if request.project_id and request.project_id != "default":
            other = conn.execute(
                "SELECT project_id FROM memories WHERE task_id=? AND project_id != ? LIMIT 1",
                (request.task_id, request.project_id),
            ).fetchone()
            if other:
                print(f"WARNING: task '{request.task_id}' in other project", file=sys.stderr)

        # supersede（status_update 或 fleet_member：同 task 保留最新 active）
        superseded_ids: list[str] = []
        if request.kind in ("status_update", "fleet_member"):
            result = supersede_for_kind(request.kind, request.project_id, request.task_id, conn)
            if result.superseded_count > 0:
                rows = conn.execute(
                    "SELECT id FROM memories WHERE project_id=? AND task_id=? "
                    "AND kind=? AND status='superseded' "
                    "ORDER BY created_at DESC LIMIT ?",
                    (request.project_id, request.task_id, request.kind, result.superseded_count),
                ).fetchall()
                superseded_ids = [r["id"] for r in rows]

        # invalidate（僅 discovered_constraint）
        invalidated_count = 0
        if request.kind == "discovered_constraint" and request.invalidates:
            inv_result = invalidate_constraints(request.invalidates, conn)
            if isinstance(inv_result, ArbitrationResult):
                conn.execute("ROLLBACK")
                return StoreResponse(
                    status="rejected",
                    reason=inv_result.reason,
                    detail=inv_result.detail,
                )
            invalidated_count = inv_result.invalidated_count

        # 生成 ID
        mem_id = generate_memory_id(conn, now=now)

        # 編碼 embedding
        try:
            emb_bytes = encode_summary(request.summary)
            emb_array: np.ndarray | None = np.frombuffer(emb_bytes, dtype="<f4").copy()
        except Exception:
            # 模型載入失敗應在 check_duplicate 時已觸發
            emb_array = None

        # 建立 Memory 物件
        memory = Memory(
            id=mem_id,
            project_id=request.project_id,
            task_id=request.task_id,
            agent_id=request.agent_id,
            timestamp=now,
            kind=request.kind,
            summary=request.summary,
            learnings=request.learnings,
            handoff_note=request.handoff_note,
            tags=request.tags,
            status="active",
            created_at=now,
            updated_at=now,
        )

        # INSERT
        insert_memory(conn, memory, emb_array)

        # 寫入 memory_labels（PPLX 架構改善計畫 item 4b）：與 memory 本身的
        # INSERT 在同一個 transaction 內，確保 memory 與其 labels 要嘛一起
        # commit、要嘛一起 rollback，不會出現「memory 寫入成功但 labels
        # 遺漏」的不一致狀態。此時 request.labels 內每個元素都已保證通過
        # run_arbitration_rules_cheap 的 validate_labels() 格式檢查（見該
        # 函式呼叫順序 —— 早於本函式的 transaction 區塊），故此處不再重複
        # 驗證格式，只做去重（dict.fromkeys 保留原順序）以避免呼叫端傳入
        # 重複 label 時，撞上 memory_labels 的 (memory_id, label) 複合主鍵
        # 而拋出 IntegrityError。
        for label in dict.fromkeys(request.labels):
            conn.execute(
                "INSERT INTO memory_labels (memory_id, label) VALUES (?, ?)",
                (mem_id, label),
            )

        conn.execute("COMMIT")

        response = StoreResponse(
            status="stored",
            id=mem_id,
            superseded=superseded_ids,
            invalidated_count=invalidated_count,
        )
        append_audit(response, request)
        return response

    except Exception as e:
        # ROLLBACK 需保護：SQLite 在 SQLITE_FULL / SQLITE_IOERR / SQLITE_NOMEM
        # 等錯誤下會自動回滾交易，此時再執行 ROLLBACK 會拋出
        # "cannot rollback - no transaction is active"——若不攔截，原始錯誤
        # （例如撞到 max_page_count 的 disk-full）會被這個誤導的次生例外
        # 遮蔽，且本函式「一律回傳 StoreResponse」的契約被打破（診斷發現）。
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        response = StoreResponse(
            status="error",
            reason="db_error",
            detail=str(e),
        )
        append_audit(response, request)
        return response


# ---------------------------------------------------------------------------
# migrate_project_memories：跨專案記憶遷移共用核心邏輯
#
# 背景（真實功能缺口修復）：修復前，CLI 的 cmd_migrate_project 是唯一存在
# 的實作，且把來源資料庫路徑寫死為 Path.home() / ".local/state/remagraph/
# remagraph.db"（等同假設 from_project 永遠是 'default'），MCP tool
# remagraph_migrate_project 則完全是空殼，只驗證 to_project 就回傳一則寫死
# 的假訊息，從未真正搬移任何資料。
#
# 本函式是兩邊（cli.cmd_migrate_project / server.remagraph_migrate_project）
# 共用的唯一核心實作：
# - 來源 state_dir 解析改用 db.get_registered_state_dir(from_project) 查詢
#   registry 內實際登記的路徑（而不是寫死 default 路徑），對從未登記過的
#   from_project 明確拋出 ProjectNotRegisteredError，而不是靜默當作 0 筆。
#   'default' 是唯一例外：一般 CLI 用法中，project_id == 'default' 這個
#   回退值本來就刻意不觸發 safety_validate_project/resolve_project_state_dir
#   （見 cli._project_id_for_conn 的既有設計），因此幾乎不會出現在
#   registry 裡——這不代表『default』專案不存在，而是它就是『目前環境沒有
#   REMAGRAPH_STATE_DIR/REMAGRAPH_PROJECT 覆寫時，ambient 的預設位置』這個
#   語意本身，所以改用 db.get_state_dir() 直接解析（尊重目前 process 的
#   REMAGRAPH_STATE_DIR/REMAGRAPH_HOME 覆寫，而不是像修復前一樣寫死絕對
#   路徑）。
# - 不透過 db.connect()/get_state_dir() 開連線（那條路徑對 REMAGRAPH_
#   STATE_DIR 環境變數有最高優先權，長駐的 MCP server 行程一旦綁定了自己
#   主要專案的 state_dir，會讓傳入的來源/目標路徑被整個忽略、悄悄操作錯的
#   DB 檔案），改用 db.connect_at_state_dir()：對明確、已解析出來的路徑
#   開連線，完全不受呼叫端目前 project 情境影響。
# - 不呼叫 print()/sys.exit()：所有錯誤一律以例外拋出，呼叫端（CLI/MCP
#   server）各自決定如何呈現。
# - dry_run 模式與實際執行共用同一段 SQL 比對邏輯（_MIGRATE_MATCH_WHERE），
#   保證「dry-run 預估筆數」與「之後真正執行時的實際遷移筆數」在資料未變動
#   的前提下必然一致。
# - 唯讀降級偵測：db.connect_at_state_dir() 內部執行與一般 connect() 相同
#   的 _run_migrations()，若來源或目標的 schema 版本比目前程式碼新，連線
#   會被標記 db.READ_ONLY_ATTR；本函式在真正嘗試寫入前明確檢查並拋出
#   MigrationReadOnlyError，而不是讓底層 sqlite3 寫入操作本身失敗、拋出
#   難以理解的原始例外。
# ---------------------------------------------------------------------------


class ProjectNotRegisteredError(RuntimeError):
    """from_project 從未被登記過（未曾有任何
    maintenance.resolve_project_state_dir()/db.connect(project_id=...) 呼叫
    對它發生過），因此找不到其 state_dir，無法安全解析遷移來源。

    刻意不靜默視為『0 筆記錄可遷移』——那會讓使用者誤以為遷移已完成，實際
    上只是來源路徑根本解析錯誤（例如 project_id 打錯字）。
    """


class MigrationReadOnlyError(RuntimeError):
    """來源或目標專案的資料庫目前處於唯讀降級狀態（schema 版本比目前程式碼
    新，見 db.READ_ONLY_ATTR），遷移所需的寫入操作（INSERT 進目標 / 標記
    來源 invalidated）被拒絕。"""


@dataclass
class MigrationResult:
    """migrate_project_memories() 的結構化回傳結果。"""

    from_project: str
    to_project: str
    dry_run: bool
    migrated_count: int
    skipped_ids: list[str] = field(default_factory=list)


# 來源資料庫內「這筆記錄是否屬於 to_project」的啟發式比對條件：來源資料庫
# 裡的舊記錄可能還沒有正確的 project_id（本來就是要被遷移過去的舊資料），
# 因此不能單純用 `WHERE project_id = ?` 精確過濾，改用 task_id/tags/
# agent_id/summary 四個欄位是否包含目標專案名稱做啟發式比對（沿用修復前
# CLI 實作既有的設計，非本次修復重點）。dry-run 估算與實際遷移共用同一段
# SQL，保證兩者對同一份未變動資料的計算結果必然一致。
_MIGRATE_MATCH_WHERE = (
    "(task_id LIKE ? OR tags LIKE ? OR agent_id LIKE ? OR summary LIKE ?) "
    "AND status != 'invalidated'"
)


def _resolve_migration_source_state_dir(from_project: str) -> Path:
    """解析遷移『來源』專案實際登記的 state_dir。

    'default' 是唯一特例：一般合法用法中，project_id == 'default' 這個
    回退值本來就刻意不觸發 registry 登記（見 cli._project_id_for_conn 的
    既有設計說明），因此改用 db.get_state_dir()（尊重目前行程的
    REMAGRAPH_STATE_DIR/REMAGRAPH_HOME 覆寫）直接解析，而不查 registry。

    其餘任何 project_id 一律查 db.get_registered_state_dir()；查無登記時
    明確拋出 ProjectNotRegisteredError，絕不靜默回傳一個猜測的路徑。
    """
    if from_project == _db.DEFAULT_PROJECT_ID:
        return _db.get_state_dir()

    registered = _db.get_registered_state_dir(from_project)
    if registered is None:
        raise ProjectNotRegisteredError(
            f"Source project {from_project!r} has never been registered "
            "(no remagraph command has resolved a state_dir for it yet, "
            "so its location is unknown); refusing to silently treat this "
            "as 0 migratable records. Run any `remagraph` command against "
            f"{from_project!r} at least once first (which registers it), "
            "or double-check the --from/from_project value for typos."
        )
    return Path(registered)


def migrate_project_memories(
    from_project: str,
    to_project: str,
    *,
    dry_run: bool = False,
) -> MigrationResult:
    """把 from_project 資料庫裡「看起來屬於」to_project 的記錄遷移過去。

    核心邏輯（CLI `migrate-project` 子指令與 MCP `remagraph_migrate_project`
    tool 共用的唯一實作，見本節模組頂端註解說明設計理由）：
    1. 驗證 to_project 合法（沿用既有安全閥門 safety_validate_project，
       require_env_match=False——這是『主動遷移進某個已知合法專案』的情境，
       不要求呼叫端目前的 REMAGRAPH_STATE_DIR 剛好已經等於該專案目錄）。
    2. 解析 from_project 實際登記的 state_dir（見
       _resolve_migration_source_state_dir）。**注意：這裡只透過
       db.get_registered_state_dir()/db.get_state_dir() 查出 state_dir 實際
       路徑，並沒有像 to_project 那樣經過完整的 safety_validate_project()
       （受限前綴規則、project.json metadata 一致性等檢查）。這是刻意的
       不對稱設計**——遷移的語意是『把資料從一個已知來源搬進一個受驗證合法
       的目標』，來源本身是否也要通過同一套安全閥門，交由呼叫端（CLI/MCP
       server）依情境自行決定是否要在呼叫本函式之前額外驗證，不要誤以為
       from_project 也受完整安全閥保護。
    3. 用 task_id/tags/agent_id/summary 啟發式比對，找出來源資料庫裡「看起
       來屬於」to_project 的記錄。
    4. dry_run=True：只回傳預估筆數，不開啟目標連線、不做任何寫入。
    5. dry_run=False：逐筆遷移，且對每一筆記錄都先完成所有可能失敗的純運算
       （目前是解析/更新 learnings 欄位的 JSON），再執行唯一會產生外部
       副作用的 INSERT OR IGNORE 進目標資料庫（強制 project_id 為
       to_project），最後才在來源標記 status='invalidated' 並寫回更新後的
       learnings；任何一步失敗都只記錄該筆 id 到 skipped_ids、不中斷其餘筆
       ——刻意把純運算放在 INSERT 之前，是為了避免『INSERT 已經寫進目標，
       但緊接著的純運算（例如壞掉的 JSON）才失敗，導致這筆記錄同時以
       active 狀態存在於來源與目標兩邊，卻被回報成 skipped』這種矛盾狀態。

    不呼叫 print()/sys.exit()：所有失敗一律以例外拋出，呼叫端（CLI/MCP
    server）各自決定如何呈現給使用者。

    Raises:
        ValueError: from_project 與 to_project 相同。
        remagraph.maintenance.SafetyValveError: to_project 未通過既有安全
            閥門驗證（受限前綴規則、project.json metadata 不一致等）。
        ProjectNotRegisteredError: from_project 從未被登記過，找不到其
            state_dir。
        FileNotFoundError: from_project 的 state_dir 已解析出來，但底下
            並沒有實際的 remagraph.db 檔案。
        MigrationReadOnlyError: 來源或目標資料庫目前處於唯讀降級狀態。
    """
    if from_project == to_project:
        raise ValueError("from_project and to_project must not be the same")

    # 步驟 1：驗證目標合法性（不要求呼叫端目前環境已經切換過去）。
    to_state = safety_validate_project(to_project, require_env_match=False)

    # 步驟 2：解析來源 state_dir。
    from_state = _resolve_migration_source_state_dir(from_project)

    # 別名防護：from_project != to_project（上面已檢查過字面上的
    # project_id 不同），但兩者各自解析出來的 state_dir 仍有可能是『物理上
    # 同一個目錄』——例如目前行程的 REMAGRAPH_STATE_DIR 剛好同時是兩者的
    # 解析結果（resolve_project_state_dir 對已設定的 REMAGRAPH_STATE_DIR
    # 有最高優先權，與 project_id 本身無關；'default' 特例走
    # db.get_state_dir() 也一樣受同一個環境變數影響）。若不擋下，會對同一份
    # remagraph.db 檔案同時開兩條各自 BEGIN 寫入 transaction 的連線，輕則
    # 自我碰撞造成 SQLITE_BUSY、重則把一筆記錄同時當成『來源』又當成
    # 『目標』寫出矛盾狀態。與近期修復的『跨專案 fan-out 對物理上同一份
    # 資料庫的別名專案』問題同一類根因，這裡採用同樣的思路：比較 resolve()
    # 後的絕對路徑而非原始字串/project_id。
    if from_state.resolve() == to_state.resolve():
        raise ValueError(
            f"from_project {from_project!r} and to_project {to_project!r} "
            f"resolve to the same physical database directory "
            f"({from_state.resolve()}); refusing to migrate a project into "
            "itself under a different name. This usually means "
            "REMAGRAPH_STATE_DIR is currently pointing at a directory that "
            "coincidentally matches both projects' resolution -- check the "
            "ambient environment before retrying."
        )

    from_db_path = from_state / _db.DB_FILENAME
    if not from_db_path.exists():
        raise FileNotFoundError(
            f"Source project {from_project!r} database does not exist at "
            f"{from_db_path}"
        )

    conn_src = _db.connect_at_state_dir(from_state)
    try:
        # 步驟 3：啟發式比對出「看起來屬於」to_project 的來源記錄。
        match_params = (f"%{to_project}%",) * 4
        rows = conn_src.execute(
            f"SELECT * FROM memories WHERE {_MIGRATE_MATCH_WHERE}",
            match_params,
        ).fetchall()

        if dry_run:
            return MigrationResult(
                from_project=from_project,
                to_project=to_project,
                dry_run=True,
                migrated_count=len(rows),
            )

        if getattr(conn_src, READ_ONLY_ATTR, False):
            raise MigrationReadOnlyError(
                f"Source project {from_project!r} database is currently in "
                "read-only degraded mode (its schema is newer than this "
                "code's write-compatible version); refusing to mark "
                "records invalidated. Upgrade the remagraph package and "
                "retry."
            )

        conn_tgt = _db.connect_at_state_dir(to_state)
        try:
            if getattr(conn_tgt, READ_ONLY_ATTR, False):
                raise MigrationReadOnlyError(
                    f"Target project {to_project!r} database is currently "
                    "in read-only degraded mode (its schema is newer than "
                    "this code's write-compatible version); refusing to "
                    "migrate data into it. Upgrade the remagraph package "
                    "and retry."
                )

            migrated = 0
            skipped_ids: list[str] = []

            conn_tgt.execute("BEGIN IMMEDIATE")
            conn_src.execute("BEGIN IMMEDIATE")
            try:
                for row in rows:
                    try:
                        # 先完成所有「純運算、可能失敗」的步驟（例如
                        # learnings 欄位若含壞掉的 JSON，json.loads 會在
                        # 這裡拋例外），確保接下來的 INSERT 是這個迴圈裡
                        # 第一個會產生外部副作用（寫入 conn_tgt）的操作。
                        learn = json.loads(row["learnings"] or "[]")

                        cols = [k for k in row.keys() if k != "project_id"]
                        vals = [row[k] for k in cols]
                        placeholders = ",".join("?" for _ in cols)
                        cols_str = ",".join(cols)

                        # memory id（mem-YYYYMMDD-NNN）是每個 DB 各自獨立的
                        # 日序列——兩個專案只要同一天各自存過記憶，id 幾乎
                        # 必然碰撞。修復前用 INSERT OR IGNORE：碰撞時靜默
                        # 不插入，卻仍把來源標 invalidated、migrated += 1，
                        # 構成靜默資料遺失（診斷發現）。現在改為：碰撞時在
                        # 目標庫重新配一個不衝突的 id（保留原日期段），
                        # 確定插入成功才繼續。
                        target_id = row["id"]
                        cur = conn_tgt.execute(
                            f"INSERT OR IGNORE INTO memories (project_id, {cols_str}) "
                            f"VALUES (?, {placeholders})",
                            [to_project] + vals,
                        )
                        if cur.rowcount == 0:
                            target_id = _remap_collided_memory_id(conn_tgt, row["id"])
                            vals[cols.index("id")] = target_id
                            conn_tgt.execute(
                                f"INSERT INTO memories (project_id, {cols_str}) "
                                f"VALUES (?, {placeholders})",
                                [to_project] + vals,
                            )

                        # 來源標記寫在 INSERT 之後：記錄實際抵達的 target_id
                        # （re-id 時與原 id 不同），供日後追蹤。
                        learn.append(
                            f"migrated-to:{to_project} as {target_id} at "
                            f"{datetime.now(timezone.utc).isoformat()}"
                        )
                        updated_learnings = json.dumps(learn, ensure_ascii=False)
                        try:
                            conn_src.execute(
                                "UPDATE memories SET status='invalidated', "
                                "learnings=? WHERE id=?",
                                (updated_learnings, row["id"]),
                            )
                        except Exception:
                            # 來源 UPDATE 失敗（SQLITE_BUSY、disk full 等）
                            # ——把剛插入目標的那筆撤掉再記 skipped，否則
                            # 迴圈結束後兩邊照樣 COMMIT，同一筆記憶會同時
                            # 以 active 存在於來源與目標（診斷發現的矛盾
                            # 狀態，正是上方註解宣稱要避免的）。
                            conn_tgt.execute(
                                "DELETE FROM memories WHERE project_id=? AND id=?",
                                (to_project, target_id),
                            )
                            raise
                        migrated += 1
                    except Exception:
                        skipped_ids.append(row["id"])

                conn_tgt.execute("COMMIT")
                conn_src.execute("COMMIT")
            except Exception:
                # ROLLBACK 各自保護：SQLite 在某些錯誤下會自動回滾（此時
                # ROLLBACK 拋 "cannot rollback"）；conn_tgt 也可能已經
                # COMMIT 成功（是 conn_src 的 COMMIT 失敗）——不能讓
                # 清理動作自己拋出新例外遮蔽原始錯誤（診斷發現）。
                for _c in (conn_tgt, conn_src):
                    try:
                        _c.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                raise
        finally:
            conn_tgt.close()

        return MigrationResult(
            from_project=from_project,
            to_project=to_project,
            dry_run=False,
            migrated_count=migrated,
            skipped_ids=skipped_ids,
        )
    finally:
        conn_src.close()
