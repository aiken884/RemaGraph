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

    多詞時不做額外 AND 串接——FTS5 預設 MATCH 多詞即隱含 AND 語意，
    交由 trigram tokenizer 自行分詞。
    """
    return sanitized


def search_memories(
    conn: sqlite3.Connection,
    request: SearchRequest,
) -> SearchResponse:
    """執行 FTS5 BM25 全文檢索。

    流程：
    1. sanitize 查詢字串（移除 FTS5 特殊字元）
    2. 短查詢（≤2 字元）→ 回傳空 results + warning log
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
    sanitized = sanitize_fts5_query(request.query)

    # 短查詢處理：≤2 字元無法形成 trigram
    if _trigram_char_len(sanitized) < 3:
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
    if request.agent_id is not None:
        where.append("m.agent_id = ?")
        params.append(request.agent_id)
    if request.task_id is not None:
        where.append("m.task_id = ?")
        params.append(request.task_id)
    if request.tags:
        for tag in request.tags:
            where.append(
                "EXISTS (SELECT 1 FROM json_each(m.tags) WHERE value = ?)"
            )
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

    result_dicts: list[dict[str, Any]] = []
    for row in results:
        result_dicts.append(
            {
                "id": row["id"],
                "summary": row["summary"],
                "agent_id": row["agent_id"],
                "kind": row["kind"],
                "task_id": row["task_id"],
                "timestamp": row["timestamp"],
                "score": row["score"],
            }
        )

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
    rows = conn.execute(
        "SELECT m.* FROM memories m "
        "INNER JOIN ("
        "  SELECT task_id, MAX(created_at) AS max_ts "
        "  FROM memories "
        "  WHERE kind = 'status_update' AND status = 'active' "
        "  GROUP BY task_id"
        ") latest ON m.task_id = latest.task_id AND m.created_at = latest.max_ts "
        "WHERE m.kind = 'status_update' "
        "ORDER BY m.created_at DESC "
        "LIMIT ?",
        (request.limit,),
    ).fetchall()

    latest: list[dict[str, Any]] = []
    for row in rows:
        latest.append(
            {
                "id": row["id"],
                "task_id": row["task_id"],
                "agent_id": row["agent_id"],
                "kind": row["kind"],
                "summary": row["summary"],
                "timestamp": row["timestamp"],
                "status": row["status"],
            }
        )

    return StatusResponse(latest=latest)
