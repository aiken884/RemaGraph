# SPDX-License-Identifier: Apache-2.0
"""Regression test: the safety_violation audit event and the
discovered_constraint memory record that _record_violation writes for the
SAME violation must land under the SAME directory.

Root cause: when safety_validate_project(project_id) fails with reason
"missing_remagraph_state_dir" (REMAGRAPH_STATE_DIR unset),
maintenance._record_violation does two things:

1. append_event("safety_violation", {...}) -- audit.py's _audit_path()
   resolves the target directory purely from the REMAGRAPH_STATE_DIR env
   var, falling back to the plain, project-unaware default
   ~/.local/state/remagraph/ when it is unset. It has no notion of
   project_id at all.
2. Writes a discovered_constraint memory record via
   _raw_connect(resolve_project_state_dir(project_id), ...) --
   maintenance.resolve_project_state_dir() is project-aware: for the same
   "unset" condition it falls back to a project-specific directory such as
   ~/.local/state/remagraph-<project_id>/.

Since "missing_remagraph_state_dir" by definition means REMAGRAPH_STATE_DIR
is unset, these two fallbacks disagree -- the audit line for the violation
silently lands in the shared default directory while the memory record for
the exact same event lands in the project-specific directory.

Isolation: this test NEVER touches the real ~/.local/state/remagraph* dirs
on this machine. HOME is monkeypatched to a tmp_path, and db.DEFAULT_STATE_DIR
(a module-level constant computed once from the real HOME at db.py import
time, so patching the HOME env var alone would not affect it) is
monkeypatched to match, so every fallback path exercised here resolves
under tmp_path.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from remagraph import db as db_mod
from remagraph import maintenance as maint_mod


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
    if not state_dir.exists():
        return records
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


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Isolate HOME (and db.py's cached DEFAULT_STATE_DIR constant, which is
    computed once from the real HOME at import time -- monkeypatching the
    HOME env var alone would not retroactively change it) so every fallback
    path resolves under tmp_path -- never the real ~/.local/state/remagraph*.

    Also actually *unsets* REMAGRAPH_STATE_DIR (delenv, not just empty) --
    this is precisely what triggers the "missing_remagraph_state_dir" reason.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(db_mod, "DEFAULT_STATE_DIR", home / ".local" / "state" / "remagraph")
    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)
    monkeypatch.delenv("REMAGRAPH_PROJECT", raising=False)
    return home


def test_safety_violation_audit_and_memory_land_in_same_directory(fake_home):
    """When REMAGRAPH_STATE_DIR is unset, safety_validate_project fails with
    reason "missing_remagraph_state_dir". The safety_violation audit event
    and the discovered_constraint memory record this failure triggers must
    both end up under whatever resolve_project_state_dir(project_id) computes
    for this project_id -- not two different directories.
    """
    project_id = "dir-consistency-test-project"

    with pytest.raises(maint_mod.SafetyValveError):
        maint_mod.safety_validate_project(project_id)

    expected_dir = maint_mod.resolve_project_state_dir(project_id)
    default_dir = fake_home / ".local" / "state" / "remagraph"

    # Sanity check: this test only proves something if the project-specific
    # resolved dir actually differs from the plain env-var-driven default --
    # otherwise the two writes could coincidentally agree for the wrong reason.
    assert expected_dir != default_dir, (
        "test setup error: expected_dir should differ from the plain default "
        "dir for this scenario to be meaningful"
    )

    violations = [r for r in _audit_events(expected_dir) if r["action"] == "safety_violation"]
    assert len(violations) == 1, (
        f"expected exactly 1 safety_violation audit event under the "
        f"project-specific resolved dir {expected_dir}, got {len(violations)} -- "
        "the audit event likely landed in a different (env-var-derived "
        "default) directory instead"
    )
    assert violations[0]["project_id"] == project_id
    assert violations[0]["reason"] == "missing_remagraph_state_dir"

    assert _discovered_constraint_count(expected_dir, project_id) == 1, (
        f"discovered_constraint memory record must be written under the same "
        f"resolved dir {expected_dir}"
    )

    # And the plain, project-unaware default directory (what the pre-fix
    # append_event/_audit_path fallback resolves to when REMAGRAPH_STATE_DIR
    # is unset) must NOT silently receive the audit event instead.
    wrong_dir_violations = [
        r for r in _audit_events(default_dir) if r["action"] == "safety_violation"
    ]
    assert wrong_dir_violations == [], (
        f"safety_violation audit event incorrectly landed in the plain "
        f"default dir {default_dir} instead of the project-specific "
        f"resolved dir {expected_dir}: {wrong_dir_violations}"
    )
