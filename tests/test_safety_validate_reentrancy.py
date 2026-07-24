# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the second, unconditionally-firing reentrancy loop
through safety_validate_project -> _record_violation -> process_store
(and the overlapping variant through db.connect()'s own top-level
safety_validate_project call).

Root cause (confirmed by direct reproduction, see task write-up):

    safety_validate_project(P) fails for reason R
        -> _record_violation(P, R)
            -> builds StoreRequest(project_id=P, ...)
            -> process_store(req, conn)     # UNCONDITIONAL, no bypass
                -> "if request.project_id: safety_validate_project(request.project_id)"
                -> same underlying condition still true -> fails again with R
                -> _record_violation(P, R) AGAIN -> ... unbounded

A live repro (herdr-* project_id against a state dir whose basename is
"remagraph", fully isolated under tmp_path -- never the real
~/.local/state/remagraph*) showed this writes ~330 duplicate
"safety_violation" audit entries for a single real violation, and the
discovered_constraint memory record this code path exists to write is
*never* actually persisted (process_store always fails validation before
reaching the INSERT). Depending on ambient stack depth this can also let a
bare RecursionError escape uncaught, reproducing the same unhandled-crash
symptom round 1 was meant to eliminate.

There is a second, overlapping variant: db.connect() itself calls
safety_validate_project() at its own top level (both when an explicit
project_id is given, and in the REMAGRAPH_PROJECT-env backward-compat
branch). _record_violation's internal call to `_raw_connect(state_dir,
skip_maintenance=True)` does NOT pass project_id, so if REMAGRAPH_PROJECT is
set in the environment (the normal per-project deployment pattern), connect()
re-derives project_id from env in its backward-compat branch and calls
safety_validate_project() again there too, independently of process_store.

Required fix: a keyword-only `skip_safety_check` bypass on both
`store.process_store` and `db.connect`, used ONLY by the internal
self-logging path inside `_record_violation` -- never by any normal external
caller (CLI, MCP server, or any other caller with an explicit project_id).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from remagraph import db as db_mod
from remagraph.maintenance import SafetyValveError, safety_validate_project
from remagraph.models import StoreRequest
from remagraph.store import process_store


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _audit_events(state_dir: Path) -> list[dict]:
    records: list[dict] = []
    for f in state_dir.glob("audit-*.jsonl"):
        records.extend(_read_jsonl(f))
    return records


def _discovered_constraint_count(state_dir: Path, project_id: str) -> int:
    db_path = state_dir / "remagraph.db"
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT count(*) FROM memories WHERE kind='discovered_constraint' "
            "AND project_id=?",
            (project_id,),
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # Never let a leftover REMAGRAPH_* env var from the running shell leak in.
    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)
    monkeypatch.delenv("REMAGRAPH_PROJECT", raising=False)


@pytest.fixture
def isolated_state_dir(tmp_path, monkeypatch):
    """Isolated fake state dir whose *basename* is "remagraph" -- this is what
    makes safety_validate_project's herdr_using_default_db branch fire for a
    herdr-* project_id, without ever touching the real default state dir
    (REMAGRAPH_STATE_DIR is monkeypatched, scoped to this test only).
    """
    state_dir = tmp_path / "state" / "remagraph"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    return state_dir


# ---------------------------------------------------------------------------
# 1. safety_validate_project itself must not recurse via _record_violation
# ---------------------------------------------------------------------------


def test_safety_validate_project_violation_logs_exactly_once_no_recursion(isolated_state_dir):
    """A herdr-* project_id against the default DB (basename "remagraph")
    triggers the herdr_using_default_db violation. This must raise exactly
    one SafetyValveError -- never RecursionError, never hang -- and must
    write exactly one safety_violation audit event, and must actually
    succeed in writing the discovered_constraint memory record once (proving
    the bypass only skips the redundant re-validation, not the intended
    record-what-happened functionality).
    """
    project_id = "herdr-reentrancy-test"

    with pytest.raises(SafetyValveError):
        safety_validate_project(project_id)

    violations = [
        r for r in _audit_events(isolated_state_dir) if r["action"] == "safety_violation"
    ]
    assert len(violations) == 1, (
        f"expected exactly 1 safety_violation audit event, got {len(violations)} -- "
        "this indicates the process_store reentrancy loop is firing repeatedly"
    )
    assert violations[0]["project_id"] == project_id
    assert violations[0]["reason"] == "herdr_using_default_db"

    assert _discovered_constraint_count(isolated_state_dir, project_id) == 1, (
        "discovered_constraint memory record must be written exactly once"
    )


# ---------------------------------------------------------------------------
# 2. process_store's own top-level gate must not recurse, and must still
#    enforce safety_validate_project for normal (non-bypassed) callers
# ---------------------------------------------------------------------------


def test_process_store_violating_project_id_raises_once_not_recursively(isolated_state_dir):
    """Calling process_store directly (the real entry point used by CLI /
    MCP server) with a project_id that fails the safety valve must still
    raise SafetyValveError (security not weakened for external callers) --
    but must not recurse hundreds of times through _record_violation first.
    """
    project_id = "herdr-reentrancy-test-2"
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    db_mod._init_schema(conn)

    req = StoreRequest(
        project_id=project_id,
        task_id="task-001",
        agent_id="test-agent",
        kind="task_handoff",
        summary="this is a test summary that must be at least thirty characters long",
        learnings=["l1"],
        handoff_note="handoff note at least twenty chars",
    )

    with pytest.raises(SafetyValveError):
        process_store(req, conn)

    violations = [
        r for r in _audit_events(isolated_state_dir) if r["action"] == "safety_violation"
    ]
    assert len(violations) == 1
    assert _discovered_constraint_count(isolated_state_dir, project_id) == 1


# ---------------------------------------------------------------------------
# 3. db.connect()'s own top-level safety_validate_project call (both the
#    explicit project_id branch and the REMAGRAPH_PROJECT-env backward-compat
#    branch) must not recurse either.
# ---------------------------------------------------------------------------


def test_db_connect_explicit_project_id_violation_does_not_recurse(isolated_state_dir):
    project_id = "herdr-reentrancy-test-3"

    with pytest.raises(SafetyValveError):
        db_mod.connect(project_id=project_id)

    violations = [
        r for r in _audit_events(isolated_state_dir) if r["action"] == "safety_violation"
    ]
    assert len(violations) == 1
    assert _discovered_constraint_count(isolated_state_dir, project_id) == 1


def test_db_connect_env_derived_backward_compat_violation_does_not_recurse(
    isolated_state_dir, monkeypatch
):
    """The backward-compat branch: connect() called with no project_id, but
    REMAGRAPH_PROJECT set in the environment to a non-'default' value (the
    normal per-project deployment pattern via each project's env.sh).
    """
    project_id = "herdr-reentrancy-test-4"
    monkeypatch.setenv("REMAGRAPH_PROJECT", project_id)

    with pytest.raises(SafetyValveError):
        db_mod.connect()

    violations = [
        r for r in _audit_events(isolated_state_dir) if r["action"] == "safety_violation"
    ]
    assert len(violations) == 1
    assert _discovered_constraint_count(isolated_state_dir, project_id) == 1


# ---------------------------------------------------------------------------
# 4. the bypass must be reachable only from the internal violation-logging
#    path -- normal callers with no bypass keyword still get full enforcement
#    (covered implicitly by every `pytest.raises(SafetyValveError)` above,
#    since none of them pass skip_safety_check).
# ---------------------------------------------------------------------------


def test_process_store_default_still_enforces_safety_valve_for_external_callers(
    isolated_state_dir,
):
    """Default behavior (skip_safety_check=False) must be preserved for
    every existing caller -- process_store must still reject/raise for a
    violating project_id when called the normal way (no bypass kwarg).
    """
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    db_mod._init_schema(conn)

    req = StoreRequest(
        project_id="herdr-reentrancy-test-5",
        task_id="task-002",
        agent_id="test-agent",
        kind="task_handoff",
        summary="this is a test summary that must be at least thirty characters long",
        learnings=["l1"],
        handoff_note="handoff note at least twenty chars",
    )

    with pytest.raises(SafetyValveError):
        process_store(req, conn)
