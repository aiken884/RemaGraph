# SPDX-License-Identifier: Apache-2.0
"""FTS5 BM25 全文檢索與狀態查詢邏輯。

本模組負責：
- FTS5 query sanitization（移除特殊字元、關鍵字跳脫）
- BM25 全文檢索（trigram tokenizer，支援 CJK）
- kind/status/tags/agent_id/task_id 過濾
- 短查詢（≤2 字元）處理：回傳空結果 + warning
- has_more 判定（LIMIT top_k + 1）
- 最新 status 查詢（active status_update 依 task_id 去重）
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
