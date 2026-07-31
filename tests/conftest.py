# SPDX-License-Identifier: Apache-2.0
"""Shared pytest fixtures and markers for RemaGraph tests."""

import pytest

from remagraph import db as db_mod


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: tests that require real model2vec model download")


@pytest.fixture(autouse=True)
def _isolate_default_state_dir(tmp_path, monkeypatch):
    """Autouse, suite-wide: never let ANY test write into this machine's
    real ~/.local/state/remagraph/remagraph.db.

    Root cause this closes: maintenance.resolve_project_state_dir() --
    reached on essentially every CLI/server/connect() path to resolve a
    project's OWN isolated per-project state directory -- always ALSO calls
    db.register_known_project(project_id, resolved) as a best-effort side
    effect (PPLX 架構改善計畫 item 4a). That function (and the
    project_edges helpers built on the same mechanism, item 5) unconditionally
    open db._connect_default_registry_db(), which uses the real module-level
    constant db.DEFAULT_STATE_DIR (= Path.home() / ".local" / "state" /
    "remagraph") -- NOT whatever REMAGRAPH_STATE_DIR env var or tmp_path a
    test set up for its own project's data. Setting REMAGRAPH_STATE_DIR or
    using tmp_path for a test's own project state does NOT protect this
    path; DEFAULT_STATE_DIR is a module-level constant computed once from
    the real HOME at db.py import time, so the ONLY way to isolate it is to
    directly monkeypatch the module attribute remagraph.db.DEFAULT_STATE_DIR
    itself -- which is exactly what this fixture does, for every test,
    automatically.

    Safe to coexist with the 4 test files that already monkeypatch
    db_mod.DEFAULT_STATE_DIR themselves (test_cross_project_label_search.py,
    test_project_registry.py, test_safety_violation_dir_consistency.py,
    test_project_edges_and_recall_related.py): monkeypatch's undo stack
    handles repeated setattr() calls on the same attribute within one test
    correctly regardless of which fixture performed them (all fixtures in a
    single test share the same `monkeypatch` instance), so a test-file-level
    patch applied after this one simply takes precedence for that test's
    duration, and everything unwinds correctly on teardown.

    No `from remagraph.db import DEFAULT_STATE_DIR` (a separate bound name
    this patch would not reach) exists anywhere else in the codebase --
    confirmed by grep; only `remagraph.db.DEFAULT_STATE_DIR` /
    `db_mod.DEFAULT_STATE_DIR` module-attribute references are used, all of
    which resolve through the module's own namespace at call time and are
    therefore all covered by patching the attribute here.

    Also clears REMAGRAPH_HOME (see db._resolve_default_state_dir()): that
    function checks this env var FIRST, unconditionally, before ever falling
    back to the DEFAULT_STATE_DIR attribute patched above. An ambient
    REMAGRAPH_HOME set in a developer's shell (plausible -- this env var
    exists specifically to support external subprocess testing) would
    otherwise silently win over the monkeypatch above for this test and
    every other test in the suite, defeating this fixture's entire purpose.
    Mirrors the same defensive delenv pattern several test files already use
    for REMAGRAPH_STATE_DIR/REMAGRAPH_PROJECT.
    """
    fake_default = tmp_path / "isolated-default-state-dir"
    monkeypatch.setattr(db_mod, "DEFAULT_STATE_DIR", fake_default)
    monkeypatch.delenv("REMAGRAPH_HOME", raising=False)
    return fake_default
