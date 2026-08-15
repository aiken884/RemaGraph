# SPDX-License-Identifier: Apache-2.0
"""FTS5 BM25 全文檢索與狀態查詢邏輯。

本模組負責：
- FTS5 query sanitization（移除特殊字元、關鍵字跳脫）
- BM25 全文檢索（trigram tokenizer，支援 CJK）
- kind/status/tags/agent_id/task_id 過濾
- 空字串 query：視為「列出最近的記憶」，不做全文檢索
- 短查詢（1–2 字元、非空）處理：回傳空結果 + warning
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

include_related（PPLX 架構改善計畫 item 5）是第三個獨立維度：與
cross_project_label 對『所有』已知專案無差別 fan-out 不同，include_related
只 fan out 到透過 db.recall_related()（project_edges traversal）明確找到、
在 related_hops 之內的「圖形關聯」專案，且查詢方式是正常的 FTS 全文查詢
（非 label 精確比對）。兩者的「開連線 + 查詢 + 合併 + 依 (source_project_id,
id) 去重 + fan-out 上限與 capped 回報」這一段模式高度相似，因此抽出共用的
_cross_project_fanout() 供兩者重用（見該函式 docstring），差異只在於：
候選專案清單怎麼來（list_known_projects() vs. db.recall_related()）、以及
對每個連線實際執行的查詢邏輯（依 label 比對 vs. 依 FTS query/過濾條件）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from pathlib import Path
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
# 50（PPLX 架構審查共識，BUG 2 修復，2026-07）：原本 20 這個數字過於保守——
# 實務量測顯示真實候選專案數已經常超過 20，導致 fan-out 過早被截斷。50 仍是
# 刻意保守、易於之後調整的預留，可由呼叫端經 resolve_fanout_cap() 的
# REMAGRAPH_FANOUT_CAP 環境變數或明確覆寫值（CLI --fanout-cap）調整，唯一律
# clamp 到硬性上限（見 _FANOUT_HARD_CAP_DEFAULT）——這是刻意的資源保護
# 上限：呼叫端（agent）對這台機器上實際存在多少個 state_dir 毫無可見度，
# 不設硬上限會有 OOM／過多並行 SQLite 連線的風險（尤其 CI 容器等資源受限
# 環境）。因此刻意不提供「0 表示不限」的逃生艙口。
_CROSS_PROJECT_FANOUT_CAP = 50

# 硬性上限（PPLX 架構審查共識，BUG 2 修復）：不論呼叫端透過 CLI --fanout-cap
# 或 REMAGRAPH_FANOUT_CAP 環境變數要求多高的 cap，resolve_fanout_cap() 一律
# clamp 到此值，除非額外、明確地透過 REMAGRAPH_FANOUT_HARD_CAP 環境變數
# opt-in 提高——這是刻意的兩層設計：一般呼叫端可自由調整「軟」上限以取得更
# 完整的結果，但「硬」上限的提高需要另一個獨立、更明確的環境變數，避免
# 隨手調高 --fanout-cap 就意外繞過資源保護。
_FANOUT_HARD_CAP_DEFAULT = 200


def _resolve_fanout_hard_cap() -> int:
    """解析目前生效的硬性上限。預設 _FANOUT_HARD_CAP_DEFAULT，僅能由
    REMAGRAPH_FANOUT_HARD_CAP 環境變數明確 opt-in 提高（或降低）；環境變數
    值無法解析為正整數時，防禦性退回預設值。
    """
    raw = os.environ.get("REMAGRAPH_FANOUT_HARD_CAP")
    if raw:
        try:
            parsed = int(raw)
        except ValueError:
            return _FANOUT_HARD_CAP_DEFAULT
        if parsed > 0:
            return parsed
    return _FANOUT_HARD_CAP_DEFAULT


def resolve_fanout_cap(explicit: int | None = None) -> int:
    """解析本次跨專案 fan-out 搜尋實際生效的 cap（PPLX 架構審查共識，
    BUG 2 修復）。

    優先序：
    1. explicit（呼叫端明確提供的值，對應 CLI `--fanout-cap` 或
       SearchRequest.fanout_cap）——若提供，優先於環境變數。
    2. REMAGRAPH_FANOUT_CAP 環境變數（可解析為整數時）。
    3. _CROSS_PROJECT_FANOUT_CAP 模組預設值（50）。

    0 或負數一律視為無效值、防禦性退回步驟 3 的預設值——刻意不提供
    「0 表示不限」的逃生艙口（PPLX 共識明確拒絕此設計，理由見上方模組
    常數說明）。

    最終結果一律 clamp 到 _resolve_fanout_hard_cap() 解析出的硬性上限
    （預設 200，僅能由 REMAGRAPH_FANOUT_HARD_CAP 環境變數明確 opt-in
    提高）——即使 explicit 或環境變數要求更高的值，也不會超過硬上限。
    """
    if explicit is not None:
        cap = explicit
    else:
        raw = os.environ.get("REMAGRAPH_FANOUT_CAP")
        if raw:
            try:
                cap = int(raw)
            except ValueError:
                cap = _CROSS_PROJECT_FANOUT_CAP
        else:
            cap = _CROSS_PROJECT_FANOUT_CAP

    if cap <= 0:
        cap = _CROSS_PROJECT_FANOUT_CAP

    return min(cap, _resolve_fanout_hard_cap())


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


def _split_fts_tokens_by_length(sanitized: str) -> tuple[list[str], list[str]]:
    """把 sanitize 後的查詢依 token 有效長度拆成（可檢索, 太短）兩組。

    修復診斷發現的閘門缺陷：舊閘門把「所有 token 去空白後的總字元數」當
    判斷基準，但 _build_fts5_match 把每個空白分隔的 token 各自包成獨立
    phrase，而 trigram tokenizer 對 < 3 字元的 phrase 產生零個 token——
    結果「資料 搜尋」（兩個 2 字詞、總長 4）通過舊閘門後永遠回傳 0 筆，
    也不會落入短查詢的列表模式 fallback；混合查詢（"search ab"）的短
    token 則被 FTS5 靜默忽略。因此正確的判斷單位是「每一個 token」而非
    總長。token 可能已被 sanitize_fts5_query 包上引號（FTS5 保留字），
    計長前先去掉外層引號，避免引號被算進長度而誤判（舊版 "or" →
    '"or"' 長度 4 繞過攔截的 bug）。
    """
    long_tokens: list[str] = []
    short_tokens: list[str] = []
    for t in sanitized.split():
        inner = t[1:-1] if len(t) >= 2 and t[0] == '"' and t[-1] == '"' else t
        if _trigram_char_len(inner) >= 3:
            long_tokens.append(t)
        else:
            short_tokens.append(t)
    return long_tokens, short_tokens


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


def _list_by_filters_rows(
    conn: sqlite3.Connection,
    request: SearchRequest,
    *,
    apply_project_filter: bool = True,
) -> list[sqlite3.Row]:
    """無全文查詢時，依 task_id/agent_id 等過濾直接列出的核心查詢，回傳原始
    rows（未套用 top_k 截斷/has_more 判斷）。

    獨立成此函式（供 _list_by_filters 與 item 5 的
    _query_single_db_for_request 共用），理由與 _query_single_db_for_request
    docstring 的 apply_project_filter 說明一致：對『目前這個連線自己所屬的
    專案』要套用 request.project_id 過濾（維持既有行為），但對 fan-out 到的
    『另一個』相關專案自己獨立的資料庫檔案，不該套用這個屬於 origin 專案的
    過濾條件。

    Args:
        apply_project_filter: 是否套用 request.project_id 過濾，預設 True
            （維持 _list_by_filters 對外的既有行為不變）。
    """
    where: list[str] = []
    params: list[Any] = []

    if request.kind is not None:
        where.append("kind = ?")
        params.append(request.kind)
    # status 三態：None（預設）＝active；"all"＝不過濾；其餘＝指定值
    if request.status == "all":
        pass
    elif request.status is not None:
        where.append("status = ?")
        params.append(request.status)
    else:
        where.append("status = ?")
        params.append("active")
    if apply_project_filter and request.project_id is not None:
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
    return conn.execute(sql, params).fetchall()


def _list_by_filters(
    conn: sqlite3.Connection,
    request: SearchRequest,
) -> SearchResponse:
    """無全文查詢時，依 task_id/agent_id 等過濾直接列出（給 auto/recall 用）。"""
    rows = _list_by_filters_rows(conn, request, apply_project_filter=True)
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

    if request.include_related:
        if request.project_id is None:
            # 呼叫端使用錯誤（而非系統錯誤）：include_related 需要一個
            # project_id 作為 db.recall_related() traversal 的起點，
            # project_id=None 時沒有『我』這個起點可以走。優雅退化為
            # 一般搜尋（不展開 related fan-out），不拋出例外——見模組頂端
            # 說明與 tests/test_project_edges_and_recall_related.py 的
            # test_include_related_with_project_id_none_does_not_crash_and_falls_back。
            logger.warning(
                "include_related=True but project_id is None — nothing to traverse "
                "from (recall_related requires a starting project_id); falling back "
                "to a normal search without related-project fan-out"
            )
        else:
            return _search_related_projects(conn, request)

    # 空字串（或僅空白）query：語意上等同「列出最近的記憶」而非全文檢索，
    # 不論是否帶有 task_id/agent_id/kind/tags 等過濾條件，一律走列表模式。
    # 這與「query 經 sanitize 後才變空」（如純特殊字元 "**\"\""）不同——
    # 後者代表使用者確實想搜尋，只是內容不構成有效查詢詞，仍應維持既有的
    # 短查詢空結果行為（見下方 _trigram_char_len 分支）。
    if not (request.query or "").strip():
        return _list_by_filters(conn, request)

    sanitized = sanitize_fts5_query(request.query or "")

    # 無有效全文查詢時：有過濾條件就直接列表，否則空結果。判斷單位是
    # 「每一個 token」而非總長（見 _split_fts_tokens_by_length 的缺陷說明）。
    long_tokens, short_tokens = _split_fts_tokens_by_length(sanitized)
    if short_tokens and long_tokens:
        logger.warning(
            "dropping too-short tokens %r from FTS5 query %r (trigram tokenizer "
            "needs >= 3 chars per token); searching with %r only",
            short_tokens,
            request.query,
            long_tokens,
        )
    if not long_tokens:
        if request.task_id or request.agent_id or request.kind or request.tags:
            return _list_by_filters(conn, request)
        logger.warning(
            "FTS5 query too short for trigram tokenizer: %r → %r",
            request.query,
            sanitized,
        )
        return SearchResponse(results=[], has_more=False)

    match_clause = _build_fts5_match(" ".join(long_tokens))

    # 動態 WHERE 條件
    where: list[str] = []
    params: list[Any] = [match_clause]

    if request.kind is not None:
        where.append("m.kind = ?")
        params.append(request.kind)
    # 與列表模式（_list_by_filters_rows）一致的 status 三態：None（預設）
    # ＝只回 active（診斷發現的語意不一致修復——同一個 search 指令，有無
    # query 不得有不同存活語意）；"all"＝顯式不過濾（歷史記憶逃生口）；
    # 其餘＝指定值。
    if request.status == "all":
        pass
    elif request.status is not None:
        where.append("m.status = ?")
        params.append(request.status)
    else:
        where.append("m.status = ?")
        params.append("active")
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
# 共用跨專案 fan-out 骨架（PPLX 架構改善計畫 item 4b Part 3 起，item 5 重用）
#
# 抽出理由：item 4b 的「依 label 對『所有』已知專案 fan-out」與 item 5 的
# 「依 FTS query 對『明確關聯』專案 fan-out」，兩者的骨架完全一致——(a) 查
# 目前這個連線自己的資料庫、(b) 逐一對候選的『其他』project_id 開唯讀連線
# 執行同一份查詢、(c) 合併並依 (source_project_id, id) 去重、(d) 套用
# fan-out 上限與 cross_project_fanout_capped 回報慣例——差異只在於「候選
# project_id 清單怎麼來」（item 4b：db.list_known_projects() 全量；item 5：
# db.recall_related() 的 hop-bounded traversal）與「對每個連線實際執行的
# 查詢邏輯」（item 4b：依 label 精確比對；item 5：依 FTS query/過濾條件）。
# 因此把 (a)(b)(c)(d) 這段共用骨架抽成 _cross_project_fanout()，兩個呼叫端
# 只需各自提供候選清單與查詢 closure，不重複維護一份幾乎相同的迴圈/去重/
# 上限邏輯。
# ---------------------------------------------------------------------------


def _optional_score(row: sqlite3.Row) -> float | None:
    """回傳 row 若含有 FTS5 BM25 查詢附加的 'score' 欄位，否則回傳 None。

    label 比對查詢（SELECT m.*）與 list-by-filters 查詢（SELECT * FROM
    memories）的 rows 都沒有這個欄位；FTS5 全文查詢的 rows
    （SELECT m.*, bm25(memories_fts) AS score ...）才有。獨立成小函式，讓
    _cross_project_fanout() 的合併邏輯不必知道 query_fn 究竟是哪一種查詢，
    一律嘗試讀取、讀不到就退回 _row_to_result() 既有的 0.0 預設值。
    """
    try:
        return float(row["score"])
    except (IndexError, KeyError):
        return None


def _resolve_physical_db_path(file_path: str | Path) -> str | None:
    """把一個資料庫檔案路徑正規化為絕對路徑字串（.resolve()），供比較兩個
    路徑是否指向同一個實體檔案使用。任何解析失敗（例如路徑本身不合法）
    一律回傳 None——呼叫端須將 None 視為『無法判斷是否相同』，因此絕不
    會因此誤判兩者相同而錯誤跳過候選（見 _cross_project_fanout 呼叫處的
    比較邏輯，None 一律視為不相等）。
    """
    try:
        return str(Path(file_path).resolve())
    except (OSError, ValueError):
        return None


def _own_connection_db_path(conn: sqlite3.Connection) -> str | None:
    """回傳 conn 目前所連接的 SQLite 'main' 資料庫實體檔案的絕對路徑（已
    正規化），供 _cross_project_fanout() 判斷某個候選 project_id 是否與
    『目前這個連線』物理上是同一個 SQLite 檔案——而不僅是 project_id
    字串是否相等。

    背景（真實回歸 bug）：own_project_id 為 None（呼叫端未指定 --project）
    或與某個已註冊候選專案的邏輯名稱不同，但兩者的 state_dir 實際上解析到
    同一個實體目錄/檔案（例如本機的 'default' state dir 與已註冊的
    'RemaGraph' 專案皆指向 ~/.local/state/remagraph/remagraph.db）時，純
    字串比對 `pid == own_project_id` 從不成立，導致 (b) 對該候選開第二條
    連線、重新查詢同一個實體檔案，回傳同一筆記憶兩次；且因兩次出現的
    source_project_id 標籤字串不同（例如 None/'default' vs 'RemaGraph'），
    最終依 (source_project_id, id) 的去重也攔不住。透過 PRAGMA
    database_list 取得 conn 實際連到的檔案路徑，可在『不依賴任何
    project_id 標籤字串』的情況下，直接以實體檔案路徑正確判斷別名關係。

    回傳 None 的情況（in-memory 連線、PRAGMA 執行失敗、查無 'main' 資料庫、
    路徑無法解析）一律視為『無法判斷是否相同』——維持既有、較保守的行為，
    不會因此誤跳過任何候選。
    """
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    try:
        file_path = row["file"]
    except (IndexError, KeyError):
        return None
    if not file_path:
        return None
    return _resolve_physical_db_path(file_path)


def _cross_project_fanout(
    conn: sqlite3.Connection,
    request: SearchRequest,
    *,
    own_project_id: str | None,
    candidate_project_ids: list[str],
    query_fn: Any,
    cap: int,
    log_label: str,
) -> SearchResponse:
    """共用的跨專案 fan-out 骨架，見上方模組內說明。

    Args:
        conn: 目前這個連線（呼叫端自己所屬的專案，或無特定專案時的預設
            連線）。
        request: 原始 SearchRequest，僅用於 top_k 截斷/has_more 判斷。
        own_project_id: 目前這個連線所屬的 project_id（可能為 None——見
            下方對 (source_project_id, id) 去重機制的說明，None 時仍保證
            正確、只是無法提前跳過候選清單中『恰好等於自己』的那一項）。
        candidate_project_ids: 候選的『其他』project_id 清單（item 4b 傳入
            db.list_known_projects() 的全量；item 5 傳入
            db.recall_related() 的 hop-bounded 結果）。
        query_fn: 對『任一』連線（自己的或某個候選 project 的唯讀連線）
            執行同一份查詢邏輯的 callable：Callable[[sqlite3.Connection],
            list[sqlite3.Row]]。同一個 query_fn 同時用於 (a) 自己的連線與
            (b) 每一個候選連線——這正是兩個呼叫端「查詢邏輯不同、但骨架
            相同」得以共用本函式的關鍵：查詢邏輯的差異完全封裝在呼叫端
            傳入的 closure 裡。
        cap: fan-out 上限（item 4b/5 目前都重用同一個
            _CROSS_PROJECT_FANOUT_CAP，見該常數與呼叫端的說明）。
        log_label: 超過上限時 warning log 訊息裡標明是哪一種 fan-out
            （"cross_project_label" 或 "include_related"），方便日後從
            log 判斷是哪個功能觸發。

    韌性：db.connect_foreign_project_readonly() 對任何已註冊但目前不可達的
    專案一律回傳 None——遇到 None 時直接跳過、繼續處理其餘候選，絕不讓整個
    搜尋因單一專案不可達而失敗；query_fn 拋出 sqlite3.OperationalError
    （例如該外部專案的資料庫尚未升級到含所需表格的 schema 版本）時同樣
    防禦性跳過。

    去重：(source_project_id, id) —— 沿用 item 4b bug 回歸修復的既有結論：
    不論 own_project_id 是否為 None（因而讓下方「跳過候選清單中等於自己的
    那一項」這個提前優化是否有命中），一律在合併後的最終結果集上依
    (source_project_id, id) 去重，正確性不依賴這個提前優化。

    物理路徑別名防線（真實回歸 bug，見 _own_connection_db_path
    docstring）：上述 (source_project_id, id) 去重鍵仰賴『同一筆記憶在兩次
    出現時會帶著同一個 source_project_id 字串』，但 own_project_id 為
    None，或候選 project_id 的登記名稱與目前連線所屬的邏輯名稱不同、卻
    實際指向同一個 SQLite 實體檔案時（例如 'default' 與已註冊的
    'RemaGraph' 專案指向同一個 state_dir），(a) 標記的 source_project_id
    與 (b) 對該候選標記的 source_project_id 是兩個不同字串，去重鍵不同，
    完全攔不住——因此下方在字串比對之外，額外以 PRAGMA database_list
    取得的實體檔案路徑判斷候選是否『物理上』就是目前這個連線，是本函式
    唯一真正堵住這個別名情境的防線，而不是最終的去重步驟。
    """
    from remagraph import db as db_mod

    all_results: list[dict[str, Any]] = []
    any_scored = False

    # (a) 目前這個連線自己所屬專案的資料庫。source_project_id 以 row 自己
    # 的 project_id 為準——own 連線指向共用資料庫檔案時，query_fn（例如
    # label 比對，不含 project_id 過濾）可能撈到『別的』專案的記憶，一律
    # 標成 own_project_id 會錯置來源（診斷發現）；row 的 project_id 才是
    # 這筆記憶真正的歸屬。
    own_rows = query_fn(conn)
    for row in own_rows:
        score = _optional_score(row)
        if score is not None:
            any_scored = True
        result = _row_to_result(row, score=score)
        result["source_project_id"] = row["project_id"] or own_project_id
        all_results.append(result)

    # 目前這個連線實際連到的實體檔案路徑（供下方物理別名比對；解析失敗時為
    # None，此時一律不跳過任何候選，維持既有的保守行為——見
    # _own_connection_db_path docstring）。刻意不在此呼叫
    # db.list_known_projects() 一次性枚舉全量已知專案再建表——那是
    # _search_cross_project_by_label 自己準備候選清單時才需要的全量枚舉；
    # 本函式對任何呼叫端（包括只窄範圍 fan-out 到 project_edges 關聯專案的
    # _search_related_projects）都只需要「這一個候選 project_id 的
    # state_dir 是什麼」，改用 db.get_registered_state_dir() 逐一查詢單一
    # project_id，避免讓 include_related 路徑也連帶觸發一次全量 registry
    # 枚舉（見 tests/test_project_edges_and_recall_related.py 的
    # test_cross_project_label_include_related_all_projects_are_fully_decoupled
    # 對 list_known_projects() 呼叫次數的明確斷言）。
    own_db_path = _own_connection_db_path(conn)

    # candidate_project_ids 是『所有』已知/相關的 project_id（來源見上方
    # docstring），本來就包含呼叫端自己的 project_id——db.list_known_projects()
    # 回傳全量已知專案，其中當然含有呼叫端自己（見 resolve_project_state_dir
    # 的 best-effort 自動登記副作用）；db.recall_related() 的 BFS 亦保證
    # 起點 project_id 本身雖不含在回傳的 related 集合中，但呼叫端有時仍會
    # 傳入含自身的候選清單。因此在計算候選統計『之前』，先過濾掉空字串與
    # 邏輯上等於 own_project_id 的項目，得到 other_candidate_ids——這才是
    # 真正『其他』專案的候選清單，同時作為下方迴圈的迭代對象與
    # candidates_total 的計算基礎。
    #
    # 修復真實回歸 bug（BUG 2，獨立對抗式審查發現）：修復前 candidates_total
    # 直接用 len(candidate_project_ids)（未過濾、含呼叫端自己）計算，但下方
    # 迴圈卻用『已排除自己』的邏輯在跑，導致即使完全沒有撞到 cap，
    # candidates_skipped 也會恆為至少 1（把呼叫端自己算成一個「被跳過的候選」
    # ）——虛報「搜尋不完整」。修復後 candidates_total 與下方迴圈的迭代範圍
    # 完全一致，因此 candidates_total == candidates_searched + candidates_skipped
    # 恆成立，且 candidates_skipped 只在真正撞到 cap 時才會 > 0（見下方物理
    # 別名判斷仍可能造成極少數例外，屬於另一個獨立、罕見的既有邊界情況，
    # 不在本次修復範圍——見 _own_connection_db_path 的別名 bug 說明）。
    other_candidate_ids = [
        pid
        for pid in candidate_project_ids
        if pid and not (own_project_id and pid == own_project_id)
    ]

    # (b) 候選的其他專案
    fanout_capped = False
    fanned_out = 0
    for pid in other_candidate_ids:
        if own_db_path is not None:
            candidate_state_dir = db_mod.get_registered_state_dir(pid)
            if candidate_state_dir:
                candidate_db_path = _resolve_physical_db_path(
                    Path(candidate_state_dir) / db_mod.DB_FILENAME
                )
                if candidate_db_path is not None and candidate_db_path == own_db_path:
                    # 字串比對沒攔到，但物理上就是目前這個連線已經查過的
                    # 同一個 SQLite 檔案——跳過，避免重複計入 fan-out 上限、
                    # 重複結果（見上方模組/函式 docstring 的別名 bug 說明）。
                    continue

        if fanned_out >= cap:
            fanout_capped = True
            logger.warning(
                "%s fan-out cap (%d) reached; %d candidate projects in total — "
                "results may be incomplete",
                log_label,
                cap,
                len(other_candidate_ids),
            )
            break

        foreign_conn = db_mod.connect_foreign_project_readonly(pid)
        if foreign_conn is None:
            # 已註冊但目前不可達（例如 state_dir 已被刪除）——跳過，不讓
            # 整個搜尋因單一專案失敗。不計入 fanned_out：cap 與
            # candidates_searched 的語意是「實際開啟並查詢的連線數」，
            # 修復前不可達候選也被計入，統計虛報（診斷發現）。
            continue
        fanned_out += 1
        try:
            rows = query_fn(foreign_conn)
        except sqlite3.DatabaseError:
            # OperationalError（schema 未升級）之外，實體損毀的候選 DB 拋
            # 的是父類 DatabaseError（"database disk image is malformed"）
            # ——修復前只吞 OperationalError，單一損毀專案會炸掉整個搜尋，
            # 違反本函式 docstring 的韌性合約（診斷發現）。
            logger.warning("skipping unreadable candidate project %r during %s fan-out",
                           pid, log_label, exc_info=True)
            continue
        finally:
            foreign_conn.close()

        for row in rows:
            score = _optional_score(row)
            if score is not None:
                any_scored = True
            result = _row_to_result(row, score=score)
            result["source_project_id"] = row["project_id"] or pid
            all_results.append(result)

    seen_keys: set[tuple[str, str]] = set()
    deduped_results: list[dict[str, Any]] = []
    for result in all_results:
        key = (result["source_project_id"], result["id"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped_results.append(result)

    # 合併後必須重新排序再截斷（診斷發現：修復前 own rows 先放、各候選
    # 依序附加就直接 [:top_k]，own 專案有 >= top_k 筆低相關命中時，其他
    # 專案更相關/更新的命中被系統性丟棄）。排序鍵依查詢型態：FTS 查詢的
    # rows 帶 BM25 score（越低越相關，升冪）；label / 列表查詢無 score，
    # 依 created_at 降冪（與 _LABEL_MATCH_SQL / _list_by_filters_rows 的
    # 單庫排序一致）。
    if any_scored:
        deduped_results.sort(key=lambda r: r["score"])
    else:
        deduped_results.sort(key=lambda r: r["created_at"], reverse=True)

    has_more = len(deduped_results) > request.top_k
    trimmed = deduped_results[: request.top_k]

    # 候選數量統計（PPLX 架構審查共識，BUG 2 修復；獨立對抗式審查發現
    # off-by-one 後再次修復，見上方 other_candidate_ids 的說明）：讓只看
    # exit code 或只看 results 陣列的呼叫端，也能從結構化欄位本身明確判斷
    # 「搜尋不完整」，而非僅能從 cross_project_fanout_capped 這個布林值猜測
    # 差距有多大。candidates_total 為候選的『其他』專案總數（不含目前這個
    # 連線自己所屬的專案，計算基礎與下方迴圈的迭代範圍 other_candidate_ids
    # 完全一致）；candidates_searched 為「實際開啟連線並查詢」的數量
    # （診斷修復後：不可達或損毀而被跳過的候選不再計入）；
    # candidates_skipped 恆為兩者之差——candidates_total ==
    # candidates_searched + candidates_skipped 恆成立。注意（對抗式審查
    # 指正後的語意更新）：skipped > 0 不再等同「撞到 cap」——不可達/損毀
    # 候選也會計入 skipped 而 capped 維持 False；判斷「是否因上限截斷」
    # 一律以 cross_project_fanout_capped 為準。
    candidates_total = len(other_candidate_ids)
    candidates_searched = fanned_out
    candidates_skipped = candidates_total - candidates_searched

    return SearchResponse(
        results=trimmed,
        has_more=has_more,
        cross_project_fanout_capped=fanout_capped,
        candidates_total=candidates_total,
        candidates_searched=candidates_searched,
        candidates_skipped=candidates_skipped,
    )


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

# status="all" 逃生口用的變體（無 status 過濾，其餘與上完全一致）
_LABEL_MATCH_SQL_ALL_STATUSES = (
    "SELECT m.* FROM memories m "
    "JOIN memory_labels ml ON ml.memory_id = m.id "
    "WHERE ml.label = ? "
    "ORDER BY m.created_at DESC "
    "LIMIT ?"
)


def _query_labeled_memories(
    conn: sqlite3.Connection, label: str, status: str | None, limit: int
) -> list[sqlite3.Row]:
    """對單一連線（可能是目前這個專案自己的、也可能是另一個 project 的唯讀
    連線）查詢符合 label 的 memories rows。獨立成小函式，讓自身 project 與
    其他已知 project 的查詢共用同一份 SQL 與參數順序，避免兩處各自維護一份
    容易漂移的 SQL 字串。
    """
    if status is None:
        return conn.execute(_LABEL_MATCH_SQL_ALL_STATUSES, (label, limit)).fetchall()
    return conn.execute(_LABEL_MATCH_SQL, (label, status, limit)).fetchall()


def _search_cross_project_by_label(
    conn: sqlite3.Connection,
    request: SearchRequest,
) -> SearchResponse:
    """跨專案標籤搜尋：(a) 查詢目前連線自己的資料庫 + (b) 透過 item 4a 的
    registry 逐一開啟其他已知專案的唯讀連線查詢，合併結果並標記各筆結果的
    來源 project_id（見模組頂端說明，此為與既有 all_projects 完全獨立的
    新能力）。骨架部分（開連線/查詢/合併/去重/上限）由 _cross_project_fanout
    共用，本函式只負責準備 label 搜尋專屬的查詢 closure 與候選清單。

    status 過濾：預設只回傳 status='active'（沿用 _list_by_filters 對
    『無全文查詢、走列表模式』查詢的既有預設慣例），若呼叫端明確指定
    request.status 則改用該值。

    Fan-out 上限：見模組頂端 _CROSS_PROJECT_FANOUT_CAP 的說明。超過上限時
    SearchResponse.cross_project_fanout_capped 設為 True 並記一筆 warning
    log，不悄悄截斷佯裝結果完整。
    """
    from remagraph import db as db_mod

    label = request.cross_project_label
    assert label is not None  # 呼叫端（search_memories）已保證非空才會走到這裡
    # status 三態（與主路徑/列表模式一致，第二輪驗收掃描補上）：None＝
    # active；"all"＝不過濾（修復前 "all" 被當字面 status 值綁進 SQL，
    # 恆回 0 筆，與逃生口的目的直接矛盾）；其餘＝指定值。
    status: str | None = request.status or "active"
    if request.status == "all":
        status = None
    limit = request.top_k + 1

    def _query(c: sqlite3.Connection) -> list[sqlite3.Row]:
        return _query_labeled_memories(c, label, status, limit)

    known_projects = db_mod.list_known_projects()
    candidate_ids = [pid for p in known_projects if (pid := p.get("project_id"))]

    return _cross_project_fanout(
        conn,
        request,
        own_project_id=request.project_id,
        candidate_project_ids=candidate_ids,
        query_fn=_query,
        cap=resolve_fanout_cap(request.fanout_cap),
        log_label="cross_project_label",
    )


# ---------------------------------------------------------------------------
# include_related：依 project_edges traversal 範圍限縮的 fan-out
# (PPLX 架構改善計畫 item 5)
# ---------------------------------------------------------------------------


def _query_single_db_for_request(
    conn: sqlite3.Connection,
    request: SearchRequest,
    *,
    apply_project_filter: bool,
) -> list[sqlite3.Row]:
    """對單一連線執行『目前這個 SearchRequest』所描述的一般全文/過濾查詢
    （FTS5 BM25，或短查詢時的 list-by-filters fallback），回傳原始 rows
    （已套用 LIMIT top_k+1，尚未做去重/has_more/top_k 截斷——那些留給呼叫端
    在合併多個資料庫的結果之後統一處理，見 _cross_project_fanout）。

    供 _search_related_projects 對『目前這個連線』與每一個 related project
    各自的資料庫檔案共用同一份查詢邏輯——差異只在於 conn 參數指向哪一個
    資料庫檔案，以及 apply_project_filter。

    刻意不在此處理『查詢過短且無過濾條件』時的 warning log——該 log 屬於
    呼叫端一次性的行為（同一個 request.query 對每個資料庫都會得到相同的
    『太短』判斷結果，若在此處逐一 per-db 記錄，對多個 related project
    fan-out 時會重複記錄多筆幾乎相同的 warning），交由呼叫端視情境自行決定
    是否記錄。

    Args:
        apply_project_filter: 是否套用 request.project_id 過濾。對『目前
            這個連線自己所屬的專案』應傳 True（維持既有行為）；對 fan-out
            到的『另一個』相關專案自己獨立的資料庫檔案應傳 False——該檔案
            本來就整個屬於那一個 related project，request.project_id 指的
            是『目前這個』origin 專案，與該檔案的內容無關（比照 item 4b
            cross_project_label 對外部連線一律不過濾 project_id 的既有
            慣例，見 _query_labeled_memories 完全不含 project_id 過濾）。
    """
    sanitized = sanitize_fts5_query(request.query or "")

    long_tokens, _short_tokens = _split_fts_tokens_by_length(sanitized)
    if not long_tokens:
        if (
            not (request.query or "").strip()
            or request.task_id
            or request.agent_id
            or request.kind
            or request.tags
        ):
            # 空 query＝「列出最近的記憶」（與 search_memories 主路徑 line
            # ~355 的語意一致——第二輪驗收掃描：修復前 include_related +
            # 空 query 在此回 []，加上「擴大搜尋範圍」的旗標反而讓結果
            # 從有變無）；有任何過濾條件時同樣走列表模式。
            return _list_by_filters_rows(conn, request, apply_project_filter=apply_project_filter)
        # 非空 query 但全部 token 過短且無過濾：維持短查詢空結果語意
        return []

    match_clause = _build_fts5_match(" ".join(long_tokens))
    where: list[str] = []
    params: list[Any] = [match_clause]

    if request.kind is not None:
        where.append("m.kind = ?")
        params.append(request.kind)
    # 與 search_memories 主路徑相同的 status 三態（見該處說明）
    if request.status == "all":
        pass
    elif request.status is not None:
        where.append("m.status = ?")
        params.append(request.status)
    else:
        where.append("m.status = ?")
        params.append("active")
    if apply_project_filter and request.project_id is not None:
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
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        logger.exception("FTS5 query failed for: %r", request.query)
        return []


def _search_related_projects(
    conn: sqlite3.Connection,
    request: SearchRequest,
) -> SearchResponse:
    """include_related 分支：對『目前這個連線』執行正常的 FTS 全文查詢
    （非 item 4b 的 label 搜尋），並額外 fan out 到透過 db.recall_related()
    （project_edges traversal，見該函式對稱性 vs. 方向性的完整討論）在
    request.related_hops 之內找到的『明確宣告為圖形關聯』專案，合併結果並
    套用與 item 4b 相同的去重/上限慣例（_cross_project_fanout 共用骨架）。

    呼叫端（search_memories）已保證 request.project_id 非 None 才會走到
    這裡——include_related 需要一個 project_id 作為 traversal 起點。

    Fan-out 上限：重用既有的 _CROSS_PROJECT_FANOUT_CAP 常數（而非另立新的
    hop-bounded 專屬上限）。理由：project_edges 的候選集合天生就已經是
    「使用者透過 `remagraph link` 明確宣告過關聯」的子集，範圍遠比 item 4b
    「所有已知專案」小得多，因此撞到同一個上限的機率更低；重用同一個常數
    避免徒增一個新的、意義相近卻獨立維護的魔術數字，兩處 fan-out 上限的
    語意（「單次搜尋最多開幾個『其他』專案的資料庫連線」）也完全一致，
    合用同一個常數更容易讓人一次理解、一次調整。
    """
    from remagraph import db as db_mod

    own_project_id = request.project_id
    assert own_project_id is not None  # 呼叫端已保證非 None 才會走到這裡

    related_ids = sorted(db_mod.recall_related(own_project_id, hops=request.related_hops))

    def _query_own(c: sqlite3.Connection) -> list[sqlite3.Row]:
        return _query_single_db_for_request(c, request, apply_project_filter=True)

    def _query_foreign(c: sqlite3.Connection) -> list[sqlite3.Row]:
        return _query_single_db_for_request(c, request, apply_project_filter=False)

    def _query(c: sqlite3.Connection) -> list[sqlite3.Row]:
        return _query_own(c) if c is conn else _query_foreign(c)

    return _cross_project_fanout(
        conn,
        request,
        own_project_id=own_project_id,
        candidate_project_ids=related_ids,
        query_fn=_query,
        cap=resolve_fanout_cap(request.fanout_cap),
        log_label="include_related",
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
