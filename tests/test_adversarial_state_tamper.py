# SPDX-License-Identifier: Apache-2.0
"""Adversarial tests: simulate state tampering and permission issues.

- test_db_file_corruption_raises: corrupt the sqlite DB file
  and assert connect() fails with DatabaseError.
- test_state_dir_permission_denied: make state dir non-writable
  and assert connect() raises OSError.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from remagraph import db


def test_db_file_corruption_raises(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    # create a valid DB first
    conn = db.connect()
    conn.close()

    db_path = db.get_db_path()
    assert db_path.exists()

    # corrupt the DB file
    with open(db_path, "wb") as f:
        f.write(b"CORRUPTED")

    # subsequent connect should raise sqlite3.DatabaseError (or other sqlite error)
    with pytest.raises(sqlite3.DatabaseError):
        db.connect()


def test_state_dir_permission_denied(tmp_path, monkeypatch):
    state_dir = tmp_path / "state_perm"
    # create dir and then remove write permissions
    state_dir.mkdir(parents=True)
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    # remove write/search permissions for owner
    try:
        # monkeypatch Path.mkdir to raise OSError for this specific
        # path to simulate permission denied
        orig_mkdir = Path.mkdir

        def fake_mkdir(self, *a, **k):
            if str(self) == str(state_dir):
                raise OSError("simulated permission denied")
            return orig_mkdir(self, *a, **k)

        monkeypatch.setattr(Path, "mkdir", fake_mkdir)
        with pytest.raises(OSError):
            db.connect()
    finally:
        # restore original mkdir to allow cleanup
        monkeypatch.setattr(Path, "mkdir", orig_mkdir)
