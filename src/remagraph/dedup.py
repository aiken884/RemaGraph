# SPDX-License-Identifier: Apache-2.0
"""model2vec 語意去重（仲裁規則 #4）。

本模組負責：
- 將 summary 編碼為 model2vec embedding（minishlab/potion-multilingual-128M，256 維）
- 與同 kind 的所有 active 記憶做 cosine similarity 比對
- 若最高相似度 ≥ 0.90 → 拒絕寫入，回傳最相似的既有記憶 ID

模型延遲載入（首次呼叫時初始化）；載入失敗 → fail-fast (ModelLoadError)。
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import numpy as np

from remagraph.arbitration import ArbitrationResult
from remagraph.models import MemoryKind

if TYPE_CHECKING:
    from model2vec import StaticModel

# ---------------------------------------------------------------------------
# 常數
# ---------------------------------------------------------------------------

MODEL_NAME = "minishlab/potion-multilingual-128M"
SIMILARITY_THRESHOLD = 0.90
MAX_TOKENS = 512
DEDUP_MAX_CANDIDATES = 2000
EMBEDDING_DIM = 256  # 由 potion-multilingual-128M 實測鎖定


class ModelLoadError(RuntimeError):
    """model2vec 模型無法載入。fail-fast，不降級。"""


# ---------------------------------------------------------------------------
# 模型單例
# ---------------------------------------------------------------------------

_model: StaticModel | None = None


def _get_model() -> "StaticModel":
    """延遲載入 model2vec 模型（Singleton）。載入失敗 → raise ModelLoadError。"""
    global _model
    if _model is None:
        try:
            from model2vec import StaticModel

            _model = StaticModel.from_pretrained(MODEL_NAME)
        except Exception as e:
            raise ModelLoadError(f"Failed to load model2vec model '{MODEL_NAME}': {e}") from e
    return _model


# ---------------------------------------------------------------------------
# Cosine similarity
# ---------------------------------------------------------------------------


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """計算兩個 numpy 向量的 cosine similarity。"""
    dot = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(dot / (norm_a * norm_b))


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------


def encode_summary(summary: str) -> bytes:
    """將 summary 編碼為 model2vec embedding（256 維 float32）。

    取前 MAX_TOKENS token 做編碼。回傳 numpy bytes（float32 little-endian <f4）。

    Raises:
        ModelLoadError: 模型載入失敗（fail-fast）
    """
    model = _get_model()
    # 取前 MAX_TOKENS token（model2vec 內部處理 tokenizer 限制）
    vec = model.encode(summary[: MAX_TOKENS * 4])  # 粗略估算 char→token
    # 確保是 float32
    vec = vec.astype(np.float32)
    return vec.tobytes()


def check_duplicate(
    summary: str,
    kind: MemoryKind,
    conn: sqlite3.Connection,
    project_id: str | None = None,
) -> ArbitrationResult:
    """model2vec 語意去重（仲裁規則 #4）。

    1. 將 summary 編碼為 embedding
    2. 載入同 kind 所有 active embedding（上限 2000 筆，以 created_at DESC 取最新）
    3. 計算 cosine similarity
    4. 若最高相似度 ≥ 0.90 → 回傳 ArbitrationResult(passed=False, ...)
       否則 → 回傳 ArbitrationResult(passed=True, ...)

    Raises:
        ModelLoadError: model2vec 模型載入失敗（fail-fast）
    """
    # 編碼新 summary
    model = _get_model()
    new_vec = model.encode(summary[: MAX_TOKENS * 4])
    new_vec = new_vec.astype(np.float32)

    # 載入同 kind 的 active embedding（上限 DEDUP_MAX_CANDIDATES，取最新）
    if project_id:
        rows = conn.execute(
            "SELECT id, embedding FROM memories "
            "WHERE project_id=? AND kind=? AND status='active' AND embedding IS NOT NULL "
            "ORDER BY created_at DESC LIMIT ?",
            (project_id, kind, DEDUP_MAX_CANDIDATES),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, embedding FROM memories "
            "WHERE kind=? AND status='active' AND embedding IS NOT NULL "
            "ORDER BY created_at DESC LIMIT ?",
            (kind, DEDUP_MAX_CANDIDATES),
        ).fetchall()

    if not rows:
        return ArbitrationResult(passed=True)

    # 線性掃描比對
    best_id: str | None = None
    best_sim: float = -1.0

    for row in rows:
        try:
            existing_vec = np.frombuffer(row["embedding"], dtype=np.float32)
            sim = _cosine_similarity(new_vec, existing_vec)
            if sim > best_sim:
                best_sim = sim
                best_id = row["id"]
        except (ValueError, TypeError):
            # BLOB 損毀，跳過
            continue

    if best_sim >= SIMILARITY_THRESHOLD:
        return ArbitrationResult(
            passed=False,
            reason="duplicate_content",
            detail=(
                f"Highly similar to an existing memory (similarity={best_sim:.2f}); "
                f"closest memory: {best_id}"
            ),
            closest_memory_id=best_id,
            closest_similarity=best_sim,
        )

    return ArbitrationResult(passed=True)
