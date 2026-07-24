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

import os
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path

import pytest

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


# ---------------------------------------------------------------------------
# Regression: an AMBIENT REMAGRAPH_HOME env var (e.g. exported in the
# developer's shell -- plausible, since REMAGRAPH_HOME exists specifically to
# support external subprocess testing) must not defeat the isolation above.
#
# Root cause: db._resolve_default_state_dir() checks
# os.environ.get("REMAGRAPH_HOME") FIRST, unconditionally, before ever
# falling back to the (monkeypatched) DEFAULT_STATE_DIR module attribute --
# but the autouse `_isolate_default_state_dir` fixture in conftest.py only
# does monkeypatch.setattr(db_mod, "DEFAULT_STATE_DIR", fake_default) and
# (before the fix) never clears REMAGRAPH_HOME. So an ambient REMAGRAPH_HOME
# silently wins over the monkeypatched attribute for the ENTIRE suite,
# defeating isolation exactly like the pre-fix bug this file otherwise
# guards against.
#
# Note this can't simply be reproduced by setting REMAGRAPH_HOME from inside
# a test body: by the time the test body runs, the function-scoped autouse
# fixture has ALREADY executed. To genuinely simulate "the shell already had
# it exported before pytest started", we use a MODULE-scoped autouse fixture
# below -- pytest instantiates higher-scoped fixtures before lower-scoped
# ones within the same test request, so this sets REMAGRAPH_HOME before the
# function-scoped `_isolate_default_state_dir` fixture ever runs for any test
# in this module.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _ambient_remagraph_home_env():
    """Simulate an ambient REMAGRAPH_HOME already exported in the shell,
    BEFORE the per-test (function-scoped) autouse isolation fixture in
    conftest.py runs. Module scope + autouse guarantees this fixture sets up
    first for every test in this module (pytest instantiates higher-scoped
    fixtures before lower-scoped ones), then tears down only after every test
    in the module has finished -- restoring whatever REMAGRAPH_HOME was (or
    was not) set to beforehand, and removing the decoy directory.
    """
    decoy_dir = Path(tempfile.mkdtemp(prefix="remagraph-ambient-decoy-"))
    original = os.environ.get("REMAGRAPH_HOME")
    os.environ["REMAGRAPH_HOME"] = str(decoy_dir)
    try:
        yield decoy_dir
    finally:
        if original is None:
            os.environ.pop("REMAGRAPH_HOME", None)
        else:
            os.environ["REMAGRAPH_HOME"] = original
        shutil.rmtree(decoy_dir, ignore_errors=True)


def test_ambient_remagraph_home_does_not_defeat_autouse_isolation(
    tmp_path, _ambient_remagraph_home_env
):
    """The exact scenario the independent reviewer reproduced: a developer
    with REMAGRAPH_HOME set in their shell runs pytest in that same shell.
    The autouse `_isolate_default_state_dir` fixture's monkeypatch of
    DEFAULT_STATE_DIR must still be what wins for this test's registry
    writes -- the ambient REMAGRAPH_HOME must never take precedence.
    """
    decoy_dir = _ambient_remagraph_home_env

    db_mod.register_known_project("proj-ambient-remagraph-home-check", tmp_path / "proj-state")

    isolated_registry_db = db_mod.DEFAULT_STATE_DIR / db_mod.DB_FILENAME
    decoy_registry_db = decoy_dir / db_mod.DB_FILENAME

    assert isolated_registry_db.exists(), (
        "expected register_known_project()'s best-effort write to land in "
        "the autouse-isolated DEFAULT_STATE_DIR, even with an ambient "
        "REMAGRAPH_HOME env var set -- the autouse fixture must clear "
        "REMAGRAPH_HOME so it cannot silently override the monkeypatched "
        "DEFAULT_STATE_DIR for the rest of this test"
    )
    assert not decoy_registry_db.exists(), (
        f"LEAK: the registry write landed in the ambient REMAGRAPH_HOME "
        f"decoy dir {decoy_dir} instead of the isolated DEFAULT_STATE_DIR -- "
        "an ambient REMAGRAPH_HOME env var defeated the autouse isolation "
        "fixture for this test"
    )
