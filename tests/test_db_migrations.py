"""Tests for db migrations and schema version handling.

- test_connect_creates_meta_with_schema_version: fresh DB should get schema_version set to SCHEMA_VERSION.
- test_connect_raises_on_newer_schema: if DB has newer schema_version than code SCHEMA_VERSION, connect() should raise MigrationError.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from remagraph import db


def test_connect_creates_meta_with_schema_version(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    # ensure empty state dir
    conn = db.connect()
    # query _meta
    cur = conn.execute("SELECT value FROM _meta WHERE key='schema_version'")
    row = cur.fetchone()
    assert row is not None
    assert int(row[0]) == db.SCHEMA_VERSION
    conn.close()


def test_connect_raises_on_newer_schema(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    # Create DB and set schema_version to higher than code
    db_path = db.get_db_path()
    conn = sqlite3.connect(db_path)
    # create _meta and insert schema_version using parameterized execute
    conn.execute("CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);")
    conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', ?)", (str(db.SCHEMA_VERSION + 1),))
    conn.commit()
    conn.close()

    # Now connect should raise MigrationError
    with pytest.raises(db.MigrationError):
        db.connect()
