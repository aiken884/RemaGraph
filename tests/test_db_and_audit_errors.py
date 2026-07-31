# SPDX-License-Identifier: Apache-2.0
"""Tests for DB failure and audit write failure handling.

- test_db_insert_failure_returns_error: simulate insert_memory raising
  to ensure remagraph_store returns status 'error' with reason 'db_error'.
- test_audit_write_failure_is_silent: simulate file write OSError
  during append_audit; remagraph_store should still return stored
  and not raise.
"""

from __future__ import annotations

import builtins
import sqlite3

import pytest

import remagraph.server as server
import remagraph.store as store


def _reset_conn():
    server._conn = None


@pytest.fixture(autouse=True)
def setup_env(tmp_path, monkeypatch):
    state_dir = str(tmp_path / "state")
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", state_dir)
    _reset_conn()
    yield
    _reset_conn()


def test_db_insert_failure_returns_error(monkeypatch):
    def fake_insert_memory(conn, memory, embedding):
        raise sqlite3.DatabaseError("simulated insert failure")

    monkeypatch.setattr(store, "insert_memory", fake_insert_memory)

    res = server.remagraph_store(
        project_id="testproj",
        task_id="t-db-fail",
        agent_id="dbtester",
        kind="task_handoff",
        summary=("A" * 60),
        learnings=["l1"],
        handoff_note=("n" * 20),
        tags=["test"],
    )

    assert res["status"] == "error"
    assert res.get("reason") == "db_error"
    assert "simulated insert failure" in res.get("detail", "")


def test_audit_write_failure_is_silent(monkeypatch):
    # Monkeypatch builtins.open to raise OSError when audit attempts to write
    original_open = builtins.open

    def raising_open(*args, **kwargs):
        raise OSError("disk full simulated")

    monkeypatch.setattr(builtins, "open", raising_open)

    try:
        res = server.remagraph_store(
            project_id="default",
            task_id="t-audit-fail",
            agent_id="audit-tester",
            kind="task_handoff",
            summary=("B" * 60),
            learnings=["l1"],
            handoff_note=("n" * 20),
            tags=["test"],
        )

        # append_audit should swallow OSError; store should still be stored
        assert res["status"] == "stored"
    finally:
        # restore
        monkeypatch.setattr(builtins, "open", original_open)
