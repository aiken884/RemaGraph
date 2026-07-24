"""Regression tests for maintenance.py misusing append_audit() (Bug 2).

append_audit(response: StoreResponse, request: StoreRequest) is strictly typed
for logging remagraph_store outcomes. maintenance.py used to call it three
times with a plain string + plain dict instead of real model instances:

- _record_violation:            append_audit("safety_violation", {...})
- run_maintenance:               append_audit("maintenance_completed", {...})
- light_maintenance_on_connect:  append_audit("maintenance_light_failed", {...})

Every one of these crashed at `if response.status not in (...)` with
AttributeError: 'str' object has no attribute 'status', because `response`
was actually a plain string, not a StoreResponse.

The fix adds a distinct, correctly-typed `append_event(action, detail)` for
generic maintenance/lifecycle events, reusing the same audit-YYYYMM.jsonl
file, and updates all three maintenance.py call sites to use it.
"""

from __future__ import annotations

import json

import pytest

from remagraph import db as db_mod
from remagraph import maintenance as maint_mod
from remagraph.audit import append_event


def _read_jsonl(path):
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


@pytest.fixture
def audit_file(tmp_path, monkeypatch):
    """Route audit writes to a temp audit.jsonl and expose its path.

    Accepts an optional state_dir positional arg (ignored) so this fixture
    stays compatible with append_event's state_dir override parameter
    (added for the audit/memory directory-consistency fix) -- every write
    routed through this fixture lands in the same fixed temp file regardless
    of whether a caller passes an explicit state_dir.
    """
    path = tmp_path / "audit-current.jsonl"
    monkeypatch.setattr("remagraph.audit._audit_path", lambda state_dir=None: path)
    return path


# ---------------------------------------------------------------------------
# append_event itself
# ---------------------------------------------------------------------------


def test_append_event_writes_jsonl_with_action_and_detail(audit_file):
    append_event("maintenance_completed", {"project_id": "meganote", "pruned_count": 3})

    records = _read_jsonl(audit_file)
    assert len(records) == 1
    r = records[0]
    assert r["action"] == "maintenance_completed"
    assert r["project_id"] == "meganote"
    assert r["pruned_count"] == 3
    assert "timestamp" in r


def test_append_event_appends_without_overwrite(audit_file):
    append_event("safety_violation", {"project_id": "p1", "reason": "r1"})
    append_event("safety_violation", {"project_id": "p2", "reason": "r2"})

    records = _read_jsonl(audit_file)
    assert len(records) == 2
    assert records[0]["project_id"] == "p1"
    assert records[1]["project_id"] == "p2"


def test_append_event_never_raises_on_write_failure(monkeypatch):
    def _fail(*args, **kwargs):
        raise PermissionError("permission denied")

    monkeypatch.setattr("builtins.open", _fail)
    # Must not raise.
    append_event("maintenance_completed", {"project_id": "x"})


def test_append_event_non_serializable_detail_does_not_raise(audit_file):
    """Round 2 hardening: before the fix, append_event wrote with
    json.dump(record, f) directly into the open file handle. json.dump
    writes incrementally *before* raising on a non-serializable value, so a
    broken fragment was left appended to the audit file, and only OSError
    was caught -- the TypeError escaped uncaught.
    """

    class _Unserializable:
        """Stand-in for e.g. an exception object passed by mistake."""

    # Must not raise, even though `detail` contains a value json can't encode.
    append_event("safety_violation", {"project_id": "p1", "bad_value": _Unserializable()})


def test_append_event_non_serializable_detail_leaves_no_corrupted_line(audit_file):
    class _Unserializable:
        pass

    append_event("safety_violation", {"project_id": "p1", "bad_value": _Unserializable()})
    # A subsequent, valid event must still append cleanly.
    append_event("safety_violation", {"project_id": "p2", "reason": "ok"})

    assert audit_file.exists()
    with open(audit_file, "r", encoding="utf-8") as f:
        lines = [line for line in f.read().split("\n") if line]
    # Every remaining line must be a single, complete, valid JSON object --
    # no truncated/partial fragment from the failed serialization.
    for line in lines:
        json.loads(line)  # raises if any line is corrupted/partial
    # Only the successful event should have been written.
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["project_id"] == "p2"


def test_append_event_sanitizes_traceback_in_string_detail_values(audit_file):
    """append_event must enforce the no-traceback-leakage guarantee in code
    (like append_audit's _sanitize_detail), not merely rely on caller
    discipline, per the existing docstring convention.
    """
    traceback_like = (
        "Traceback (most recent call last):\n"
        '  File "maintenance.py", line 42, in run_maintenance\n'
        "    conn.execute(...)\n"
        "sqlite3.OperationalError: database is locked"
    )
    append_event("maintenance_light_failed", {"project_id": "p1", "error": traceback_like})

    records = _read_jsonl(audit_file)
    assert len(records) == 1
    assert "Traceback" not in records[0]["error"]
    assert "File " not in records[0]["error"]
    assert "database is locked" in records[0]["error"]


# ---------------------------------------------------------------------------
# The three real call sites must no longer crash / silently drop the event
# ---------------------------------------------------------------------------


def test_run_maintenance_completes_and_logs_without_crashing(tmp_path, monkeypatch, audit_file):
    """This is the exact call site that used to crash on the success path:
    run_maintenance's final step (append_audit("maintenance_completed", ...))
    ran unconditionally with no enclosing except, so ANY successful
    maintenance run raised AttributeError instead of returning stats.
    """
    state_dir = tmp_path / "remagraph-meganote"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    monkeypatch.delenv("REMAGRAPH_PROJECT", raising=False)

    # Build a plain connection without going through project_id auto-maintenance,
    # to isolate Bug 2 from Bug 1.
    conn = db_mod.connect(state_dir=state_dir)

    policy = maint_mod.MaintenancePolicy()
    stats = maint_mod.run_maintenance(policy, "meganote", force=True, conn=conn)

    assert stats["project_id"] == "meganote"
    assert stats["integrity"] == "ok"

    records = _read_jsonl(audit_file)
    completed = [r for r in records if r["action"] == "maintenance_completed"]
    assert len(completed) == 1
    assert completed[0]["project_id"] == "meganote"


def test_light_maintenance_on_connect_logs_failure_without_raising(
    tmp_path, monkeypatch, audit_file
):
    """light_maintenance_on_connect's own `except Exception as e:` handler used
    to crash with AttributeError while trying to log the failure -- i.e. the
    logging call itself was unguarded and could turn a benign failure into an
    unhandled crash bubbling out of db.connect().
    """
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path / "remagraph-meganote"))

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated maintenance failure")

    monkeypatch.setattr(maint_mod, "run_maintenance", _boom)

    # Must not raise.
    maint_mod.light_maintenance_on_connect("meganote")

    records = _read_jsonl(audit_file)
    failed = [r for r in records if r["action"] == "maintenance_light_failed"]
    assert len(failed) == 1
    assert failed[0]["project_id"] == "meganote"
    assert "simulated maintenance failure" in failed[0]["error"]


def test_record_violation_logs_safety_violation_event(tmp_path, monkeypatch, audit_file):
    """_record_violation wraps append_audit in a blanket try/except: pass, so
    the AttributeError used to fail silently -- meaning a real safety-valve
    violation event never actually got logged to the audit trail. Verify the
    event is now actually recorded.
    """
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path / "remagraph-meganote"))

    maint_mod._record_violation("meganote", "state_dir_mismatch")

    records = _read_jsonl(audit_file)
    violations = [r for r in records if r["action"] == "safety_violation"]
    assert len(violations) == 1
    assert violations[0]["project_id"] == "meganote"
    assert violations[0]["reason"] == "state_dir_mismatch"
