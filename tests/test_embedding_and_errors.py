"""Tests for embedding and error handling paths.

- test_model_load_failure_propagates: model2vec load failure during dedup
  should raise ModelLoadError.
- test_store_tolerates_late_encoding_failure: when embedding encoding fails
  late in the pipeline, process_store should still store the memory.
"""

from __future__ import annotations

import pytest

import remagraph.dedup as dedup
import remagraph.server as server
from remagraph.arbitration import ArbitrationResult


def _reset_conn():
    server._conn = None


@pytest.fixture(autouse=True)
def setup_env(tmp_path, monkeypatch):
    state_dir = str(tmp_path / "state")
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", state_dir)
    _reset_conn()
    yield
    _reset_conn()


def test_model_load_failure_propagates(monkeypatch):
    """If model2vec model fails to load during check_duplicate, the error should propagate."""

    class FakeError(RuntimeError):
        pass

    def fake_get_model():
        raise dedup.ModelLoadError("failed to load model")

    monkeypatch.setattr(dedup, "_get_model", fake_get_model)

    with pytest.raises(dedup.ModelLoadError):
        server.remagraph_store(
            task_id="t-embed-1",
            agent_id="embed-tester",
            kind="task_handoff",
            summary=("A" * 40),
            learnings=["l1"],
            handoff_note=("x" * 20),
            tags=[],
        )


def test_store_tolerates_late_encoding_failure(monkeypatch):
    """If encode_summary raises during final embedding step, process_store should still store."""

    # Make check_duplicate succeed without calling model
    def fake_check_duplicate(summary, kind, conn):
        return ArbitrationResult(passed=True)

    def fake_encode_summary(summary):
        raise Exception("encoding failed")

    monkeypatch.setattr(dedup, "check_duplicate", fake_check_duplicate)
    monkeypatch.setattr(dedup, "encode_summary", fake_encode_summary)

    res = server.remagraph_store(
        task_id="t-embed-2",
        agent_id="embed-tester",
        kind="task_handoff",
        summary=("This summary is long enough to pass arbitration rules." * 2),
        learnings=["l1"],
        handoff_note=("note" * 10),
        tags=["test"],
    )

    assert res["status"] == "stored"
    assert res.get("id") is not None
