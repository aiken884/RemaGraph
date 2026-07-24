# SPDX-License-Identifier: Apache-2.0
"""FTS5 BM25 全文檢索與狀態查詢邏輯。

本模組負責：
- FTS5 query sanitization（移除特殊字元、關鍵字跳脫）
- BM25 全文檢索（trigram tokenizer，支援 CJK）
- kind/status/tags/agent_id/task_id 過濾
- 短查詢（≤2 字元）處理：回傳空結果 + warning
- has_more 判定（LIMIT top_k + 1）
- 最新 status 查詢（active status_update 依 task_id 去重）
- 跨專案標籤搜尋（PPLX 架構改善計畫 item 4b，見 _search_cross_project_by_label）

跨專案標籤搜尋 vs. 既有 all_projects 旗標 —— 兩個完全獨立的維度：
既有的 all_projects（由呼叫端把 SearchRequest.project_id 設為 None 來實現）
只移除『目前這一個資料庫檔案內』的 project_id 過濾，因為每個 project 各自
對應完全獨立的 SQLite 檔案，all_projects 從來就不會、也不需要開啟其他檔案。
cross_project_label 則是全新能力：透過 db.list_known_projects() /
db.connect_foreign_project_readonly()（item 4a 的共用 registry 機制）真正
開啟其他 project 各自獨立的資料庫檔案，逐一查詢其 memory_labels 表。兩者
互不取代、互不影響——本模組刻意讓 cross_project_label 為 None（預設值）時
完全不觸碰 db.list_known_projects/connect_foreign_project_readonly，維持
all_projects 既有語意零副作用。
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from typing import Any

from remagraph.models import SearchRequest, SearchResponse, StatusRequest, StatusResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 跨專案標籤搜尋的 fan-out 上限（PPLX 架構改善計畫 item 4b）
#
# 背景：db.list_known_projects() 回傳的已知專案數會隨時間單調增加（只要曾經
# 用過的專案都會被登記，目前沒有任何自動清除機制）。若 cross_project_label
# 對每一個已知專案都無條件逐一開啟連線 + 查詢，單次搜尋的延遲會隨已知專案數
# 線性增長，且沒有上限——PPLX 研究引用 Azure DevOps 官方文件對「跨專案連結
# 查詢（cross-project linked queries）」明確發出的效能警示做為佐證，同一類
# 問題（查詢成本隨可查詢的專案數增加而增加、且使用者往往不會意識到）在此處
# 同樣適用。
#
# 因此刻意設一個硬性上限：單次 cross_project_label 搜尋最多開啟
# _CROSS_PROJECT_FANOUT_CAP 個「其他」專案的資料庫（不含目前這一個連線
# 本身所屬的專案，那一個是直接查詢，不計入上限）。超過上限時，不悄悄截斷
# 佯裝『已涵蓋所有已知專案』——而是在 SearchResponse.cross_project_fanout_capped
# 標記 True，並記一筆 warning log，讓呼叫端能明確知道這次的結果可能不完整。
#
# 20 這個數字本身是刻意保守、易於之後調整的預留：多數個人/小團隊使用情境下
# 已知專案數遠低於 20（20 個獨立 SQLite 檔案的循序開啟+查詢，在本地磁碟上的
# 延遲仍在可接受範圍——實務上每個 connect+query 約數毫秒等級），同時已足以
# 讓典型使用情境完全不會撞到上限；若之後量測顯示實際延遲可接受更高的數字，
# 這是一個可以獨立調整、不影響其餘邏輯的常數。
_CROSS_PROJECT_FANOUT_CAP = 20

# ---------------------------------------------------------------------------
# FTS5 query sanitization
# ---------------------------------------------------------------------------

_FTS5_SPECIAL_RE = re.compile(r'[*"()^~]')
_FTS5_KEYWORDS = frozenset({"AND", "OR", "NOT", "NEAR"})


def sanitize_fts5_query(query: str) -> str:
    """移除 FTS5 特殊字元並將獨立出現的關鍵字以雙引號包住。

    防止 FTS5 查詢語法錯誤或非預期行為（如 glob 模式、布林運算子、
    欄位限定詞等被誤解為 FTS5 語法）。

    處理方式：
    - 移除：* " ( ) ^ ~
    - 將獨立出現的 AND/OR/NOT/NEAR 關鍵字以雙引號包住，使其被視為一般詞彙
    - 保留大小寫（FTS5 trigram tokenizer 為 case-sensitive，但實務上 query 多為小寫）

    Returns:
        經過 sanitize 的安全查詢字串。
    """
    clean = _FTS5_SPECIAL_RE.sub(" ", query)
    tokens = clean.split()
    sanitized: list[str] = []
    for t in tokens:
        if t.upper() in _FTS5_KEYWORDS:
            sanitized.append(f'"{t}"')
        else:
            sanitized.append(t)
    return " ".join(sanitized).strip()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def _trigram_char_len(s: str) -> int:
    """計算字串的字元數（不含空白），用於判斷 trigram 匹配可行性。

    FTS5 trigram tokenizer 需要至少 3 個字元才能產生 trigram。
    1–2 字的查詢無法匹配任何 trigram，應提前攔截。
    """
    return len(s.replace(" ", ""))


def _build_fts5_match(sanitized: str) -> str:
    """將 sanitized query 包裝為 FTS5 MATCH 字串。

    每個 token 都以雙引號包成 FTS5 phrase literal，使其被 FTS5 query
    parser 視為純文字，不會被重新解讀為 AND/OR/NOT/NEAR、column-filter
    （如 "col:term"）或其他語法。這比逐一列舉並移除「特殊字元」更完整、
    更不脆弱——例如連字號（-）雖不在 _FTS5_SPECIAL_RE 涵蓋範圍內，卻是
    FTS5 column-filter 排除語法（"-colname:term"）的觸發字元，未加引號時
    會被誤解析為欲排除的欄位名稱，因欄位不存在而拋出
    sqlite3.OperationalError（no such column），導致本應命中的查詢被
    search_memories 的例外處理吞掉、回傳空結果。

    包成雙引號後，phrase 內容仍會交由底層 tokenizer（trigram，支援 CJK）
    正常斷詞，因此中文查詢、既有的 AND/OR/NOT/NEAR 關鍵字加引號行為，
    以及多詞查詢的隱含 AND 語意皆維持不變；只是每個 token 各自成為一個
    literal phrase，彼此之間仍以空白隱含 AND 串接。

    若 sanitize_fts5_query() 已將保留字包成 "AND" 形式，此處會先去除
    外層引號再重新包裝並跳脫內部引號，避免產生 ""AND"" 這種雙重引號。
    """
    tokens = sanitized.split()
    quoted: list[str] = []
    for t in tokens:
        if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
            t = t[1:-1]
        escaped = t.replace('"', '""')
        quoted.append(f'"{escaped}"')
    return " ".join(quoted)


def _row_to_result(row: sqlite3.Row, score: float | None = None) -> dict[str, Any]:
    """將 memories 資料表的 row 轉換為 search/status 回傳用的 result dict。

    涵蓋 memories 表的完整欄位集合（embedding 除外，該欄位為內部向量儲存，
    不對外回傳）。learnings/tags 依 db.py schema 以 JSON 陣列字串儲存，
    需 json.loads() 還原為實際的 Python list，做法與 store.py 的
    _row_to_memory() 一致，維持整個程式庫同一套 JSON encode/decode 慣例。
    """
    result: dict[str, Any] = {
        "id": row["id"],
        "project_id": row["project_id"],
        "summary": row["summary"],
        "agent_id": row["agent_id"],
        "kind": row["kind"],
        "task_id": row["task_id"],
        "timestamp": row["timestamp"],
        "score": score if score is not None else 0.0,
        "learnings": json.loads(row["learnings"]),
        "handoff_note": row["handoff_note"],
        "tags": json.loads(row["tags"]),
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    return result


def _list_by_filters(
    conn: sqlite3.Connection,
    request: SearchRequest,
) -> SearchResponse:
    """無全文查詢時，依 task_id/agent_id 等過濾直接列出（給 auto/recall 用）。"""
    where: list[str] = []
    params: list[Any] = []

    if request.kind is not None:
        where.append("kind = ?")
        params.append(request.kind)
    if request.status is not None:
        where.append("status = ?")
        params.append(request.status)
    else:
        where.append("status = ?")
        params.append("active")
    if request.project_id is not None:
        where.append("project_id = ?")
        params.append(request.project_id)
    if request.agent_id is not None:
        where.append("agent_id = ?")
        params.append(request.agent_id)
    if request.task_id is not None:
        where.append("task_id = ?")
        params.append(request.task_id)
    if request.tags:
        for tag in request.tags:
            where.append("EXISTS (SELECT 1 FROM json_each(tags) WHERE value = ?)")
            params.append(tag)

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    params.append(request.top_k + 1)
    sql = f"""
        SELECT * FROM memories
        {where_sql}
        ORDER BY timestamp DESC
        LIMIT ?
    """
    rows = conn.execute(sql, params).fetchall()
    has_more = len(rows) > request.top_k
    results = [_row_to_result(r) for r in rows[: request.top_k]]
    return SearchResponse(results=results, has_more=has_more)


def search_memories(
    conn: sqlite3.Connection,
    request: SearchRequest,
) -> SearchResponse:
    """執行 FTS5 BM25 全文檢索。

    流程：
    1. sanitize 查詢字串（移除 FTS5 特殊字元）
    2. 短查詢（≤2 字元）→ 若有 task_id/agent_id 過濾則改走列表模式；否則回傳空
    3. 建立 FTS5 MATCH 條件
    4. 套用 kind/status/tags/agent_id/task_id 過濾
    5. LIMIT top_k + 1 以判斷 has_more
    6. 依 BM25 score 排序回傳

    Args:
        conn: SQLite 連線（須已初始化 schema）
        request: SearchRequest（含 query, top_k, kind, status, tags 等）

    Returns:
        SearchResponse（results 依 BM25 score 遞增排序，分數越低越相關）
    """
    if request.cross_project_label:
        return _search_cross_project_by_label(conn, request)

    sanitized = sanitize_fts5_query(request.query or "")

    # 無有效全文查詢時：有過濾條件就直接列表，否則空結果
    if _trigram_char_len(sanitized) < 3:
        if request.task_id or request.agent_id or request.kind or request.tags:
            return _list_by_filters(conn, request)
        logger.warning(
            "FTS5 query too short for trigram tokenizer: %r → %r",
            request.query,
            sanitized,
        )
        return SearchResponse(results=[], has_more=False)

    match_clause = _build_fts5_match(sanitized)

    # 動態 WHERE 條件
    where: list[str] = []
    params: list[Any] = [match_clause]

    if request.kind is not None:
        where.append("m.kind = ?")
        params.append(request.kind)
    if request.status is not None:
        where.append("m.status = ?")
        params.append(request.status)
    if request.project_id is not None:
        where.append("m.project_id = ?")
        params.append(request.project_id)
    if request.agent_id is not None:
        where.append("m.agent_id = ?")
        params.append(request.agent_id)
    if request.task_id is not None:
        where.append("m.task_id = ?")
        params.append(request.task_id)
    if request.tags:
        for tag in request.tags:
            where.append("EXISTS (SELECT 1 FROM json_each(m.tags) WHERE value = ?)")
            params.append(tag)

    where_sql = (" AND " + " AND ".join(where)) if where else ""

    # k+1 取法以判斷 has_more
    params.append(request.top_k + 1)

    sql = f"""
        SELECT m.*, bm25(memories_fts) AS score
        FROM memories_fts
        JOIN memories m ON m.rowid = memories_fts.rowid
        WHERE memories_fts MATCH ?{where_sql}
        ORDER BY score
        LIMIT ?
    """

    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        # FTS5 查詢語法錯誤（不應發生，因已 sanitize，但防禦性處理）
        logger.exception("FTS5 query failed for: %r", request.query)
        return SearchResponse(results=[], has_more=False)

    has_more = len(rows) > request.top_k
    results = rows[: request.top_k]

    result_dicts: list[dict[str, Any]] = [
        _row_to_result(row, score=row["score"]) for row in results
    ]

    return SearchResponse(results=result_dicts, has_more=has_more)


# ---------------------------------------------------------------------------
# 跨專案標籤搜尋（PPLX 架構改善計畫 item 4b Part 3）
# ---------------------------------------------------------------------------

_LABEL_MATCH_SQL = (
    "SELECT m.* FROM memories m "
    "JOIN memory_labels ml ON ml.memory_id = m.id "
    "WHERE ml.label = ? AND m.status = ? "
    "ORDER BY m.created_at DESC "
    "LIMIT ?"
)


def _query_labeled_memories(
    conn: sqlite3.Connection, label: str, status: str, limit: int
) -> list[sqlite3.Row]:
    """對單一連線（可能是目前這個專案自己的、也可能是另一個 project 的唯讀
    連線）查詢符合 label 的 memories rows。獨立成小函式，讓自身 project 與
    其他已知 project 的查詢共用同一份 SQL 與參數順序，避免兩處各自維護一份
    容易漂移的 SQL 字串。
    """
    return conn.execute(_LABEL_MATCH_SQL, (label, status, limit)).fetchall()


def _search_cross_project_by_label(
    conn: sqlite3.Connection,
    request: SearchRequest,
) -> SearchResponse:
    """跨專案標籤搜尋：(a) 查詢目前連線自己的資料庫 + (b) 透過 item 4a 的
    registry 逐一開啟其他已知專案的唯讀連線查詢，合併結果並標記各筆結果的
    來源 project_id（見模組頂端說明，此為與既有 all_projects 完全獨立的
    新能力）。

    status 過濾：預設只回傳 status='active'（沿用 _list_by_filters 對
    『無全文查詢、走列表模式』查詢的既有預設慣例），若呼叫端明確指定
    request.status 則改用該值。

    韌性：db.connect_foreign_project_readonly() 對任何已註冊但目前不可達的
    專案（例如目錄已被刪除）一律回傳 None——遇到 None 時直接跳過該專案、
    繼續處理其餘專案，絕不讓整個搜尋因單一專案不可達而失敗。

    Fan-out 上限：見模組頂端 _CROSS_PROJECT_FANOUT_CAP 的說明。超過上限時
    SearchResponse.cross_project_fanout_capped 設為 True 並記一筆 warning
    log，不悄悄截斷佯裝結果完整。
    """
    from remagraph import db as db_mod

    label = request.cross_project_label
    assert label is not None  # 呼叫端（search_memories）已保證非空才會走到這裡
    status = request.status or "active"
    limit = request.top_k + 1

    all_results: list[dict[str, Any]] = []

    # (a) 目前這個連線自己所屬專案的資料庫
    own_rows = _query_labeled_memories(conn, label, status, limit)
    own_project_id = request.project_id
    for row in own_rows:
        result = _row_to_result(row)
        result["source_project_id"] = own_project_id or row["project_id"]
        all_results.append(result)

    # (b) 其他已知專案（item 4a 的共用 registry）
    known_projects = db_mod.list_known_projects()
    fanout_capped = False
    fanned_out = 0
    for project in known_projects:
        pid = project.get("project_id")
        if not pid:
            continue
        if own_project_id and pid == own_project_id:
            continue  # 已經在 (a) 查過，避免重複計入 fan-out 上限、重複結果

        if fanned_out >= _CROSS_PROJECT_FANOUT_CAP:
            fanout_capped = True
            logger.warning(
                "cross_project_label fan-out cap (%d) reached; %d known projects "
                "registered in total — results for label=%r may be incomplete",
                _CROSS_PROJECT_FANOUT_CAP,
                len(known_projects),
                label,
            )
            break

        fanned_out += 1
        foreign_conn = db_mod.connect_foreign_project_readonly(pid)
        if foreign_conn is None:
            # 已註冊但目前不可達（例如 state_dir 已被刪除）——跳過，不讓
            # 整個搜尋因單一專案失敗。
            continue
        try:
            rows = _query_labeled_memories(foreign_conn, label, status, limit)
        except sqlite3.OperationalError:
            # 例如該外部專案的資料庫尚未升級到含 memory_labels 表的 schema
            # 版本——防禦性跳過，不讓整個搜尋失敗。
            continue
        finally:
            foreign_conn.close()

        for row in rows:
            result = _row_to_result(row)
            result["source_project_id"] = pid
            all_results.append(result)

    # 去重：(source_project_id, id) —— bug 回歸修復。
    #
    # 背景：上面的 `if own_project_id and pid == own_project_id: continue`
    # 只有在呼叫端明確提供 request.project_id 時才能正確判斷「這個已知專案
    # 就是目前這個連線自己所屬的專案」；一旦 request.project_id 為 None
    # （SearchRequest.project_id 的合法預設值——remagraph_search 工具本身
    # 在呼叫端未傳 project_id 時的預設，也會在 all_projects=True 併用
    # cross_project_label 時出現，見 server.remagraph_search 的
    # eff_project = None if all_projects else project_id），
    # own_project_id 為 falsy，該 guard 恆為 False，於是目前這個連線自己
    # 所屬的專案（已在上方 (a) 直接查過）又會在 (b) 的 fan-out 迴圈中被當成
    # 『別的』已知專案重新查一次，回傳同一筆記憶兩次。
    #
    # 選擇在合併後的最終結果集上依 (source_project_id, id) 去重，而不是
    # 試圖在 fan-out 前更準確地『預先算出』own_project_id 再跳過（例如反查
    # conn 對應的 project.json 或由更上層呼叫端傳入解析後的 project_id）：
    # 這裡的去重無論 request.project_id 是否明確提供都保證正確，也不依賴
    # 「目前這個連線屬於哪個 project_id」這件事在未來如何被解析或傳遞——
    # 只要 (a) 直接查與 (b) fan-out 查到的是同一筆實體記憶，其
    # (source_project_id, id) 必然相同（同一個資料庫檔案、同一個 memories
    # row），去重後恆為一筆。上方既有的 guard 仍保留：多數呼叫端明確提供
    # project_id 時，可省下一次不必要的 fan-out 連線與查詢；但正確性不再
    # 依賴這個 guard 是否有命中。
    seen_keys: set[tuple[str, str]] = set()
    deduped_results: list[dict[str, Any]] = []
    for result in all_results:
        key = (result["source_project_id"], result["id"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped_results.append(result)
    all_results = deduped_results

    has_more = len(all_results) > request.top_k
    trimmed = all_results[: request.top_k]

    return SearchResponse(
        results=trimmed,
        has_more=has_more,
        cross_project_fanout_capped=fanout_capped,
    )


# ---------------------------------------------------------------------------
# Status query
# ---------------------------------------------------------------------------


def get_status(
    conn: sqlite3.Connection,
    request: StatusRequest,
) -> StatusResponse:
    """查詢所有 active status_update，依 task_id 去重取最新。

    供 remagraph_status MCP tool 使用。

    limit 由 StatusRequest.limit 控制（預設 20，最大 100，由 Pydantic 強制）。

    Args:
        conn: SQLite 連線
        request: StatusRequest（含 limit）

    Returns:
        StatusResponse（latest 陣列依 created_at 降冪排列）
    """
    if request.project_id:
        inner_sql = (
            "SELECT task_id, MAX(created_at) AS max_ts "
            "FROM memories "
            "WHERE kind = 'status_update' AND status = 'active' AND project_id = ? "
            "GROUP BY task_id"
        )
        outer_where = " AND m.project_id = ?"
        params = (request.project_id, request.project_id, request.limit)
    else:
        inner_sql = (
            "SELECT task_id, MAX(created_at) AS max_ts "
            "FROM memories "
            "WHERE kind = 'status_update' AND status = 'active' "
            "GROUP BY task_id"
        )
        outer_where = ""
        params = (request.limit,)  # type: ignore[assignment]
    sql = (
        "SELECT m.* FROM memories m "
        f"INNER JOIN ({inner_sql}) latest "
        "ON m.task_id = latest.task_id AND m.created_at = latest.max_ts "
        f"WHERE m.kind = 'status_update' AND m.status = 'active'{outer_where} "
        "ORDER BY m.created_at DESC LIMIT ?"
    )
    rows = conn.execute(sql, params).fetchall()

    # 重用 _row_to_result：與 search_memories 共用同一套欄位對映與
    # JSON decode 邏輯，避免兩處各自維護一份欄位清單而再次出現本函式
    # 先前發生過的欄位遺漏（handoff_note/learnings/tags/status）問題。
    # status_update 沒有 BM25 分數的概念，score 沿用 _row_to_result 的
    # 預設值 0.0 —— 這與 _list_by_filters()（無全文查詢時的列表模式）
    # 對「無真實分數」記錄的既有處理方式一致。
    latest: list[dict[str, Any]] = [_row_to_result(row) for row in rows]

    return StatusResponse(latest=latest)
