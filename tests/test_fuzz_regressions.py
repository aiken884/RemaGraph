"""Regression tests derived from fuzz harness findings.

Covers representative invalid inputs that previously triggered pydantic ValidationError:
- missing agent_id
- learnings not a list
- summary is None
- unknown kind
- tags include bytes
"""

from __future__ import annotations

import pytest

import remagraph.server as server
from pydantic import ValidationError


def _reset_conn():
    server._conn = None


@pytest.fixture(autouse=True)
def setup_env(tmp_path, monkeypatch):
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path / "state"))
    _reset_conn()
    yield
    _reset_conn()


def test_missing_agent_id_raises_validation_error():
    with pytest.raises(ValidationError):
        server.remagraph_store(
            task_id="t-fuzz-1",
            agent_id=None,  # type: ignore[arg-type]
            kind="task_handoff",
            summary=("A" * 60),
            learnings=["l1"],
            handoff_note=("n" * 20),
            tags=["test"],
        )


def test_learnings_not_list_raises_validation_error():
    with pytest.raises(ValidationError):
        server.remagraph_store(
            task_id="t-fuzz-2",
            agent_id="fuzz",
            kind="task_handoff",
            summary=("A" * 60),
            learnings="not-a-list",  # type: ignore[arg-type]
            handoff_note=("n" * 20),
            tags=["test"],
        )


def test_summary_none_raises_validation_error():
    with pytest.raises(ValidationError):
        server.remagraph_store(
            task_id="t-fuzz-3",
            agent_id="fuzz",
            kind="task_handoff",
            summary=None,  # type: ignore[arg-type]
            learnings=["l1"],
            handoff_note=("n" * 20),
            tags=["test"],
        )


def test_unknown_kind_raises_validation_error():
    with pytest.raises(ValidationError):
        server.remagraph_store(
            task_id="t-fuzz-4",
            agent_id="fuzz",
            kind="unknown_kind",  # type: ignore[arg-type]
            summary=("A" * 60),
            learnings=["l1"],
            handoff_note=("n" * 20),
            tags=["test"],
        )


def test_tags_bytes_raises_validation_error():
    with pytest.raises(ValidationError):
        server.remagraph_store(
            task_id="t-fuzz-5",
            agent_id="fuzz",
            kind="task_handoff",
            summary=("A" * 60),
            learnings=["l1"],
            handoff_note=("n" * 20),
            tags=[b'bad_tag'],  # type: ignore[arg-type]
        )
