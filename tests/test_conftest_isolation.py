# SPDX-License-Identifier: Apache-2.0
"""Regression test: the pytest suite must never leak real writes into this
machine's actual ~/.local/state/remagraph/remagraph.db.

Root cause: maintenance.resolve_project_state_dir() -- reached on
essentially every CLI/server/connect() path to resolve a project's OWN
isolated per-project state directory -- always ALSO calls
db.register_known_project(project_id, resolved) as a best-effort side effect
(PPLX 架構改善計畫 item 4a). register_known_project() unconditionally opens
db._connect_default_registry_db(), which uses the real module-level constant
db.DEFAULT_STATE_DIR (= Path.home() / ".local" / "state" / "remagraph") --
*not* whatever REMAGRAPH_STATE_DIR env var or tmp_path a test set up for its
OWN project's data. This path is untouched by REMAGRAPH_STATE_DIR -- the
ONLY way to isolate it is to directly monkeypatch the module attribute
remagraph.db.DEFAULT_STATE_DIR itself.

Before the fix (see tests/conftest.py's `_isolate_default_state_dir` autouse
fixture), only 4 test files (test_cross_project_label_search.py,
test_project_registry.py, test_safety_violation_dir_consistency.py,
test_project_edges_and_recall_related.py) happened to patch
db.DEFAULT_STATE_DIR directly -- every other test exercising
resolve_project_state_dir()/connect() silently wrote/updated a real row in
this machine's actual ~/.local/state/remagraph/remagraph.db as an
unintended side effect.

This file intentionally does NOT patch db_mod.DEFAULT_STATE_DIR itself in
any of its own fixtures -- it relies ENTIRELY on the autouse
`_isolate_default_state_dir` fixture in tests/conftest.py to close the leak
at the root, for every test in the suite, without each file having to
remember to do it manually.

Safety note: the tests below deliberately guard with an assertion that
db_mod.DEFAULT_STATE_DIR is no longer the real default *before* ever calling
resolve_project_state_dir() / register_known_project(). If the autouse
isolation fixture is ever missing or broken, the guard assertion fails
first -- loudly, in this test -- and the real directory is never touched by
this test itself, even during a regression.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from remagraph import db as db_mod
from remagraph import maintenance as maint_mod

_REAL_DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "remagraph"


def test_default_state_dir_is_isolated_away_from_real_home(tmp_path):
    """Baseline sanity check, pure attribute comparison -- no filesystem I/O
    at all. Before the fix (no autouse fixture in conftest.py), db_mod's
    module-level DEFAULT_STATE_DIR constant is still bound to the REAL
    ~/.local/state/remagraph computed at db.py import time, so this fails.
    After the fix, the autouse conftest fixture has already monkeypatched it
    to somewhere under this test's own tmp_path before the test body runs.
    """
    assert db_mod.DEFAULT_STATE_DIR != _REAL_DEFAULT_STATE_DIR, (
        "db_mod.DEFAULT_STATE_DIR still points at the REAL "
        f"{_REAL_DEFAULT_STATE_DIR} -- the autouse isolation fixture in "
        "tests/conftest.py is missing or was not applied for this test."
    )
    # It must be rooted under pytest's own tmp_path tree for THIS test (i.e.
    # genuinely per-test and disposable), not just "some other directory".
    assert tmp_path in db_mod.DEFAULT_STATE_DIR.parents or db_mod.DEFAULT_STATE_DIR == tmp_path


def test_resolve_project_state_dir_never_touches_real_default_state_dir(monkeypatch, tmp_path):
    """The exact leak path: resolve_project_state_dir() for a brand-new
    project_id -- called on essentially every CLI/server/connect() path --
    must let register_known_project()'s best-effort registry write land only
    in the isolated db_mod.DEFAULT_STATE_DIR, never the real one.

    This test deliberately mimics the ~19 "innocent" test files elsewhere in
    this suite: it isolates only its OWN project's state dir (via
    REMAGRAPH_STATE_DIR, the ordinary documented mechanism) and does nothing
    special for the registry itself -- protection must come entirely from
    the autouse conftest fixture.
    """
    # Guard FIRST -- if isolation was not already applied (autouse fixture
    # missing/broken), fail immediately WITHOUT ever calling
    # resolve_project_state_dir(), so this regression test can never itself
    # leak into the real ~/.local/state/remagraph/remagraph.db.
    assert db_mod.DEFAULT_STATE_DIR != _REAL_DEFAULT_STATE_DIR, (
        "db_mod.DEFAULT_STATE_DIR is still the REAL default state dir -- "
        "the autouse isolation fixture in tests/conftest.py is missing or "
        "was not applied. Refusing to call resolve_project_state_dir() so "
        "as to not actually leak into the real directory."
    )

    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)
    monkeypatch.delenv("REMAGRAPH_PROJECT", raising=False)
    own_project_dir = tmp_path / "own-project-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(own_project_dir))

    project_id = f"leak-probe-{uuid.uuid4().hex}"
    resolved = maint_mod.resolve_project_state_dir(project_id)
    assert resolved == own_project_dir.resolve()

    # The registry write is a best-effort side effect of the call above --
    # confirm it landed in the CURRENTLY isolated db_mod.DEFAULT_STATE_DIR
    # (wherever the autouse fixture pointed it for this test), not the real
    # machine directory.
    registry_db_path = db_mod.DEFAULT_STATE_DIR / db_mod.DB_FILENAME
    assert registry_db_path.exists(), (
        "expected register_known_project()'s best-effort write to have "
        "created the isolated registry db"
    )

    conn = sqlite3.connect(str(registry_db_path))
    try:
        row = conn.execute(
            "SELECT state_dir FROM project_registry WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == str(own_project_dir.resolve())

    # And, the actual point of this whole test: the REAL directory on this
    # machine must not contain this fresh, never-before-seen project_id.
    if _REAL_DEFAULT_STATE_DIR.exists():
        real_db_path = _REAL_DEFAULT_STATE_DIR / db_mod.DB_FILENAME
        if real_db_path.exists():
            real_conn = sqlite3.connect(f"file:{real_db_path}?mode=ro", uri=True)
            try:
                real_row = real_conn.execute(
                    "SELECT 1 FROM project_registry WHERE project_id = ?",
                    (project_id,),
                ).fetchone()
            except sqlite3.OperationalError:
                real_row = None
            finally:
                real_conn.close()
            assert real_row is None, (
                f"LEAK: project_id={project_id!r} was written into the REAL "
                f"{real_db_path} -- the autouse DEFAULT_STATE_DIR isolation "
                "fixture failed to take effect"
            )
