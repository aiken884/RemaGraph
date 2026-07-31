# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the db.connect() <-> maintenance infinite recursion bug.

Root cause (confirmed by direct reproduction against a real project state dir):

    db.connect(project_id=X)
        -> light_maintenance_on_connect(X)
            -> run_maintenance(policy, X, force=False)
                -> conn = _raw_connect(state_dir)      # positional call, project_id=None
                    -> db.connect(state_dir)            # project_id falsy
                        -> backward-compat branch reads REMAGRAPH_PROJECT from env
                           (which is set in the normal, documented per-project usage
                           pattern -- every project state dir ships an env.sh that
                           exports it) and re-derives project_id
                        -> "if project_id: light_maintenance_on_connect(project_id)"
                           fires AGAIN -> infinite recursion -> RecursionError

This reproduces the exact failure from:
    REMAGRAPH_STATE_DIR=~/.local/state/remagraph-meganote REMAGRAPH_PROJECT=meganote \
        .venv/bin/python3 -m remagraph.cli status --project meganote
"""

from __future__ import annotations

import pytest

from remagraph import db as db_mod


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # Make sure no leftover REMAGRAPH_* env vars from the running shell leak in.
    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)
    monkeypatch.delenv("REMAGRAPH_PROJECT", raising=False)
    yield


def test_connect_with_project_id_and_matching_env_project_does_not_recurse(tmp_path, monkeypatch):
    """db.connect(project_id=...) must not recurse when REMAGRAPH_PROJECT is also
    set to a non-'default' value in the environment (the normal, documented usage
    pattern -- every per-project state dir's env.sh exports REMAGRAPH_PROJECT).

    Before the fix this raises RecursionError because the internal maintenance
    connection (opened via the re-imported `connect` inside maintenance.py) falls
    into the backward-compat env branch and re-triggers
    light_maintenance_on_connect, which calls run_maintenance again, which opens
    another connection, forever.
    """
    state_dir = tmp_path / "remagraph-meganote"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "meganote")

    conn = db_mod.connect(project_id="meganote")
    try:
        # Connection must be usable (schema initialized).
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memories'"
        ).fetchone()
        assert row is not None
    finally:
        conn.close()


def test_raw_connect_used_by_maintenance_does_not_retrigger_light_maintenance(
    tmp_path, monkeypatch
):
    """The specific call maintenance.py makes (connect(state_dir) with no
    project_id, while REMAGRAPH_PROJECT is set in the environment) must not
    itself re-trigger light_maintenance_on_connect -- that is precisely the
    reentrancy that causes the infinite recursion.
    """
    state_dir = tmp_path / "remagraph-meganote"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "meganote")

    calls = []
    from remagraph import maintenance as maint_mod

    original = maint_mod.light_maintenance_on_connect

    def _spy(project_id="default"):
        calls.append(project_id)
        return original(project_id)

    monkeypatch.setattr(maint_mod, "light_maintenance_on_connect", _spy)
    # db.connect imports light_maintenance_on_connect locally inside the
    # function body, so patching the maintenance module attribute is enough
    # only if db.py re-resolves it each call (it does: `from remagraph.maintenance
    # import light_maintenance_on_connect` happens inside connect()).

    conn = db_mod.connect(state_dir=state_dir, skip_maintenance=True)
    conn.close()

    assert calls == []
