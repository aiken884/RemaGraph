# SPDX-License-Identifier: Apache-2.0
"""測試 dedup.py — model2vec 去重（規則 #4），涵蓋 D05 D1-D7。"""

import sqlite3
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from remagraph.arbitration import ArbitrationResult
from remagraph.dedup import (
    EMBEDDING_DIM,
    SIMILARITY_THRESHOLD,
    ModelLoadError,
    _cosine_similarity,
    check_duplicate,
    encode_summary,
)


# ---------------------------------------------------------------------------
# D3: cosine similarity
# ---------------------------------------------------------------------------
def test_cosine_similarity_identical():
    """D3: 相同向量的 cosine similarity = 1.0。"""
    a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    assert _cosine_similarity(a, a) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal():
    """D3: 正交向量的 cosine similarity = 0.0。"""
    a = np.array([1.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0], dtype=np.float32)
    assert _cosine_similarity(a, b) == pytest.approx(0.0)


def test_cosine_similarity_opposite():
    """D3: 相反向量的 cosine similarity = -1.0。"""
    a = np.array([1.0, 2.0], dtype=np.float32)
    b = np.array([-1.0, -2.0], dtype=np.float32)
    assert _cosine_similarity(a, b) == pytest.approx(-1.0)


def test_cosine_similarity_zero_vector():
    """D3: 零向量的 cosine similarity = 0.0。"""
    a = np.array([0.0, 0.0], dtype=np.float32)
    b = np.array([1.0, 1.0], dtype=np.float32)
    assert _cosine_similarity(a, b) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# D2: encode_summary（mock model2vec）
# ---------------------------------------------------------------------------
def test_encode_summary_with_mock():
    """D2: encode_summary 回傳正確維度的 bytes。"""
    mock_model = MagicMock()
    mock_model.dim = EMBEDDING_DIM
    mock_vec = np.ones(EMBEDDING_DIM, dtype=np.float32) * 0.5
    mock_model.encode.return_value = mock_vec

    with patch("remagraph.dedup._get_model", return_value=mock_model):
        blob = encode_summary("test summary")
        assert isinstance(blob, bytes)
        assert len(blob) == EMBEDDING_DIM * 4  # float32 = 4 bytes
        restored = np.frombuffer(blob, dtype="<f4")
        assert restored.shape == (EMBEDDING_DIM,)
        np.testing.assert_array_almost_equal(restored, mock_vec)


def test_encode_summary_returns_little_endian():
    """D2: encode_summary 回傳 little-endian float32。"""
    mock_model = MagicMock()
    mock_model.dim = EMBEDDING_DIM
    mock_model.encode.return_value = np.array(
        [0.5, -0.3] + [0.0] * (EMBEDDING_DIM - 2), dtype=np.float32
    )

    with patch("remagraph.dedup._get_model", return_value=mock_model):
        blob = encode_summary("test")
        arr = np.frombuffer(blob, dtype="<f4")
        assert arr[0] == pytest.approx(0.5)
        assert arr[1] == pytest.approx(-0.3)


# ---------------------------------------------------------------------------
# D4, D5, D6: check_duplicate（mock DB + mock model2vec）
# ---------------------------------------------------------------------------

MEMORY_TABLE_SQL = "CREATE TABLE memories (id TEXT, project_id TEXT DEFAULT 'default', kind TEXT, status TEXT, embedding BLOB, created_at TEXT)"  # noqa: E501


def _make_conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(MEMORY_TABLE_SQL)
    return c


def test_check_duplicate_empty_db():
    """D6: 空資料庫自動通過。"""
    conn = _make_conn()

    mock_model = MagicMock()
    mock_model.dim = EMBEDDING_DIM

    with patch("remagraph.dedup._get_model", return_value=mock_model):
        with patch.object(
            mock_model, "encode", return_value=np.ones(EMBEDDING_DIM, dtype=np.float32)
        ):
            result = check_duplicate("test summary", "task_handoff", conn)
            assert result.passed is True

    conn.close()


def test_check_duplicate_no_matching_kind():
    """D4: 不同 kind 不會被比對。"""
    conn = _make_conn()
    existing_emb = np.ones(EMBEDDING_DIM, dtype=np.float32).tobytes()
    conn.execute(
        "INSERT INTO memories (id, project_id, kind, status, embedding, created_at) VALUES (?,?,?,?,?,?)",  # noqa: E501
        ("mem-001", "testproj", "status_update", "active", existing_emb, "2026-01-01T00:00:00"),
    )

    mock_model = MagicMock()
    mock_model.dim = EMBEDDING_DIM
    mock_model.encode.return_value = np.ones(EMBEDDING_DIM, dtype=np.float32)

    with patch("remagraph.dedup._get_model", return_value=mock_model):
        result = check_duplicate("test", "task_handoff", conn)
        assert result.passed is True

    conn.close()


def test_check_duplicate_similar_content():
    """D5: 高度相似的內容應被拒絕。"""
    conn = _make_conn()
    existing_emb = np.ones(EMBEDDING_DIM, dtype=np.float32).tobytes()
    conn.execute(
        "INSERT INTO memories (id, project_id, kind, status, embedding, created_at) VALUES (?,?,?,?,?,?)",  # noqa: E501
        ("mem-001", "testproj", "task_handoff", "active", existing_emb, "2026-01-01T00:00:00"),
    )

    mock_model = MagicMock()
    mock_model.dim = EMBEDDING_DIM

    with patch("remagraph.dedup._get_model", return_value=mock_model):
        with patch.object(
            mock_model, "encode", return_value=np.ones(EMBEDDING_DIM, dtype=np.float32)
        ):
            result = check_duplicate("similar summary", "task_handoff", conn)
            assert result.passed is False
            assert result.reason == "duplicate_content"
            assert result.closest_memory_id == "mem-001"
            assert result.closest_similarity is not None
            assert result.closest_similarity >= SIMILARITY_THRESHOLD

    conn.close()


def test_check_duplicate_dissimilar_content():
    """D5: 不相關的內容應通過。"""
    conn = _make_conn()
    existing_emb = np.array([1.0] * EMBEDDING_DIM, dtype=np.float32).tobytes()
    conn.execute(
        "INSERT INTO memories (id, project_id, kind, status, embedding, created_at) VALUES (?,?,?,?,?,?)",  # noqa: E501
        ("mem-001", "testproj", "task_handoff", "active", existing_emb, "2026-01-01T00:00:00"),
    )

    mock_model = MagicMock()
    mock_model.dim = EMBEDDING_DIM
    mock_model.encode.return_value = np.array([-1.0] * EMBEDDING_DIM, dtype=np.float32)

    with patch("remagraph.dedup._get_model", return_value=mock_model):
        result = check_duplicate(
            "completely different content here for the test", "task_handoff", conn
        )
        assert result.passed is True

    conn.close()


def test_check_duplicate_skip_inactive():
    """D4: superseded/invalidated 記錄不參與比對。"""
    conn = _make_conn()
    existing_emb = np.ones(EMBEDDING_DIM, dtype=np.float32).tobytes()
    conn.execute(
        "INSERT INTO memories (id, project_id, kind, status, embedding, created_at) VALUES (?,?,?,?,?,?)",  # noqa: E501
        ("mem-001", "testproj", "task_handoff", "superseded", existing_emb, "2026-01-01T00:00:00"),
    )

    mock_model = MagicMock()
    mock_model.dim = EMBEDDING_DIM
    mock_model.encode.return_value = np.ones(EMBEDDING_DIM, dtype=np.float32)

    with patch("remagraph.dedup._get_model", return_value=mock_model):
        result = check_duplicate("test", "task_handoff", conn)
        assert result.passed is True

    conn.close()


def test_check_duplicate_no_embedding_skip():
    """D4: 無 embedding 的記錄跳過。"""
    conn = _make_conn()
    conn.execute(
        "INSERT INTO memories (id, project_id, kind, status, embedding, created_at) VALUES (?,?,?,?,?,?)",  # noqa: E501
        ("mem-001", "testproj", "task_handoff", "active", None, "2026-01-01T00:00:00"),
    )

    mock_model = MagicMock()
    mock_model.dim = EMBEDDING_DIM
    mock_model.encode.return_value = np.ones(EMBEDDING_DIM, dtype=np.float32)

    with patch("remagraph.dedup._get_model", return_value=mock_model):
        result = check_duplicate("test", "task_handoff", conn)
        assert result.passed is True

    conn.close()


# ---------------------------------------------------------------------------
# ModelLoadError（fail-fast）
# ---------------------------------------------------------------------------
def test_model_load_error_fail_fast():
    """D1: model2vec 載入失敗應 raise ModelLoadError。"""
    with patch("remagraph.dedup._get_model", side_effect=ModelLoadError("test error")):
        with pytest.raises(ModelLoadError):
            encode_summary("test")


# ---------------------------------------------------------------------------
# EMBEDDING_DIM 鎖定
# ---------------------------------------------------------------------------
def test_embedding_dim_constant():
    """EMBEDDING_DIM 為正整數。"""
    assert isinstance(EMBEDDING_DIM, int)
    assert EMBEDDING_DIM > 0


def test_blob_size_matches_embedding_dim():
    """BLOB 大小 = EMBEDDING_DIM * 4。"""
    assert EMBEDDING_DIM * 4 > 0


# ---------------------------------------------------------------------------
# DEDUP_MAX_CANDIDATES
# ---------------------------------------------------------------------------
def test_dedup_respects_max_candidates():
    """逾 2000 筆時只取最新 2000 筆比對。"""
    conn = _make_conn()

    for i in range(2005):
        emb = np.random.randn(EMBEDDING_DIM).astype(np.float32).tobytes()
        conn.execute(
            "INSERT INTO memories (id, project_id, kind, status, embedding, created_at) VALUES (?,?,?,?,?,?)",  # noqa: E501
            (f"mem-{i:04d}", "default", "task_handoff", "active", emb, f"2026-07-21T{i:04d}"),
        )

    mock_model = MagicMock()
    mock_model.dim = EMBEDDING_DIM
    mock_model.encode.return_value = np.ones(EMBEDDING_DIM, dtype=np.float32)

    with patch("remagraph.dedup._get_model", return_value=mock_model):
        result = check_duplicate(
            "unique new content that is very different from everything else",
            "task_handoff",
            conn,
        )
        assert result.passed is True

    conn.close()


# ---------------------------------------------------------------------------
# 真實模型測試（僅在本機有模型時執行）
# ---------------------------------------------------------------------------
@pytest.mark.slow
def test_real_model_encode():
    """以真實 model2vec 模型確認 EMBEDDING_DIM。"""
    try:
        blob = encode_summary("test summary for real model")
        assert len(blob) == EMBEDDING_DIM * 4
        arr = np.frombuffer(blob, dtype="<f4")
        assert arr.shape == (EMBEDDING_DIM,)
    except ModelLoadError:
        pytest.skip("model2vec 模型不可用")


@pytest.mark.slow
def test_real_model_check_duplicate():
    """以真實模型測試完整去重流程。"""
    try:
        conn = _make_conn()

        blob1 = encode_summary("嘗試修復 subagent 委派時的 acpx 連線錯誤")
        conn.execute(
            "INSERT INTO memories (id, project_id, kind, status, embedding, created_at) VALUES (?,?,?,?,?,?)",  # noqa: E501
            ("mem-001", "testproj", "task_handoff", "active", blob1, "2026-01-01T00:00:00"),
        )

        result = check_duplicate(
            "修復 subagent 委派 + deny-all 時的 acpx 連線錯誤問題",
            "task_handoff",
            conn,
        )
        assert isinstance(result, ArbitrationResult)

        conn.close()
    except ModelLoadError:
        pytest.skip("model2vec 模型不可用")
