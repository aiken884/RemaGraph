"""測試 audit.py —— WU-7 audit writer，強制 temp state。"""

import json
import os

import pytest

from remagraph.audit import _sanitize_detail, append_audit
from remagraph.models import StoreRequest, StoreResponse

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def audit_file(tmp_path):
    """temp audit.jsonl，路徑由 monkeypatch 注入。"""
    return tmp_path / "remagraph" / "audit.jsonl"


@pytest.fixture
def stub_request():
    return StoreRequest(
        project_id="testproj",
        task_id="task-audit-001",
        agent_id="test-agent",
        kind="task_handoff",
        summary="this is a test summary that must be at least thirty characters long",
        learnings=["test learning"],
        handoff_note="handoff note at least twenty chars",
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _read_jsonl(path):
    """讀取 JSONL 檔案全部記錄。"""
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


# ---------------------------------------------------------------------------
# stored
# ---------------------------------------------------------------------------


def test_append_audit_stored(audit_file, stub_request, monkeypatch):
    """stored 事件寫入 JSONL，包含必要欄位。"""
    monkeypatch.setattr("remagraph.audit._audit_path", lambda: audit_file)

    response = StoreResponse(status="stored", id="mem-20260721-001")
    append_audit(response, stub_request)

    records = _read_jsonl(audit_file)
    assert len(records) == 1
    r = records[0]
    assert r["action"] == "remagraph_store"
    assert r["status"] == "stored"
    assert r["task_id"] == "task-audit-001"
    assert r["agent_id"] == "test-agent"
    assert r["kind"] == "task_handoff"
    assert r["id"] == "mem-20260721-001"
    assert "timestamp" in r
    # stored 不應有 reason/detail
    assert "reason" not in r
    assert "detail" not in r


# ---------------------------------------------------------------------------
# error
# ---------------------------------------------------------------------------


def test_append_audit_error(audit_file, stub_request, monkeypatch):
    """error 事件寫入 JSONL，包含 reason/detail。"""
    monkeypatch.setattr("remagraph.audit._audit_path", lambda: audit_file)

    response = StoreResponse(
        status="error",
        reason="db_error",
        detail="database is locked",
    )
    append_audit(response, stub_request)

    records = _read_jsonl(audit_file)
    assert len(records) == 1
    r = records[0]
    assert r["action"] == "remagraph_store"
    assert r["status"] == "error"
    assert r["task_id"] == "task-audit-001"
    assert r["reason"] == "db_error"
    assert r["detail"] == "database is locked"
    # error 不應有 id
    assert "id" not in r


# ---------------------------------------------------------------------------
# rejected NOT written
# ---------------------------------------------------------------------------


def test_append_audit_rejected_not_written(audit_file, stub_request, monkeypatch):
    """rejected status 不寫入 audit。"""
    monkeypatch.setattr("remagraph.audit._audit_path", lambda: audit_file)

    response = StoreResponse(status="rejected", reason="summary_too_short")
    append_audit(response, stub_request)

    # 檔案不應被建立（因為沒有事件要寫入）
    assert not audit_file.exists()


# ---------------------------------------------------------------------------
# no traceback
# ---------------------------------------------------------------------------


def test_append_audit_no_traceback(audit_file, stub_request, monkeypatch):
    """error detail 含 traceback 時被 sanitize，只保留錯誤訊息。"""
    monkeypatch.setattr("remagraph.audit._audit_path", lambda: audit_file)

    response = StoreResponse(
        status="error",
        reason="db_error",
        detail=(
            "Traceback (most recent call last):\n"
            '  File "store.py", line 42, in process_store\n'
            "    conn.execute(...)\n"
            "sqlite3.OperationalError: database is locked"
        ),
    )
    append_audit(response, stub_request)

    records = _read_jsonl(audit_file)
    r = records[0]
    # detail 不應包含 "Traceback" 或 "File "
    assert "Traceback" not in r["detail"]
    assert "File " not in r["detail"]
    assert "database is locked" in r["detail"]


def test_append_audit_no_traceback_simple_error(audit_file, stub_request, monkeypatch):
    """不含 traceback 的 error detail 原樣保留。"""
    monkeypatch.setattr("remagraph.audit._audit_path", lambda: audit_file)

    response = StoreResponse(
        status="error",
        reason="db_error",
        detail="database is locked",
    )
    append_audit(response, stub_request)

    records = _read_jsonl(audit_file)
    assert records[0]["detail"] == "database is locked"


# ---------------------------------------------------------------------------
# permissions
# ---------------------------------------------------------------------------


def test_append_audit_file_permissions(audit_file, stub_request, monkeypatch):
    """audit.jsonl 權限為 0600。"""
    monkeypatch.setattr("remagraph.audit._audit_path", lambda: audit_file)

    response = StoreResponse(status="stored", id="mem-20260721-001")
    append_audit(response, stub_request)

    stat = os.stat(audit_file)
    actual = stat.st_mode & 0o777
    assert actual == 0o600, f"expected 0o600, got {oct(actual)}"


def test_append_audit_dir_permissions(audit_file, stub_request, monkeypatch):
    """audit 目錄權限為 0700。"""
    monkeypatch.setattr("remagraph.audit._audit_path", lambda: audit_file)

    response = StoreResponse(status="stored", id="mem-20260721-001")
    append_audit(response, stub_request)

    stat = os.stat(audit_file.parent)
    actual = stat.st_mode & 0o777
    assert actual == 0o700, f"expected 0o700, got {oct(actual)}"


# ---------------------------------------------------------------------------
# append to existing file
# ---------------------------------------------------------------------------


def test_append_audit_existing_file(audit_file, stub_request, monkeypatch):
    """追加寫入既有 audit.jsonl，不覆蓋。"""
    monkeypatch.setattr("remagraph.audit._audit_path", lambda: audit_file)

    r1 = StoreResponse(status="stored", id="mem-20260721-001")
    r2 = StoreResponse(status="stored", id="mem-20260721-002")

    append_audit(r1, stub_request)
    append_audit(r2, stub_request)

    records = _read_jsonl(audit_file)
    assert len(records) == 2
    assert records[0]["id"] == "mem-20260721-001"
    assert records[1]["id"] == "mem-20260721-002"


# ---------------------------------------------------------------------------
# failure silent
# ---------------------------------------------------------------------------


def test_append_audit_failure_silent(stub_request, monkeypatch):
    """寫入失敗（OSError）不拋例外。"""

    # 讓 open 拋出 PermissionError，模擬寫入失敗
    def _fail(*args, **kwargs):
        raise PermissionError("permission denied")

    monkeypatch.setattr("builtins.open", _fail)

    response = StoreResponse(status="stored", id="mem-20260721-001")
    # 不應拋出例外
    append_audit(response, stub_request)


# ---------------------------------------------------------------------------
# _sanitize_detail 單元
# ---------------------------------------------------------------------------


def test_sanitize_detail_none():
    assert _sanitize_detail(None) is None


def test_sanitize_detail_plain():
    assert _sanitize_detail("database is locked") == "database is locked"


def test_sanitize_detail_with_traceback():
    detail = (
        "Traceback (most recent call last):\n"
        '  File "foo.py", line 10, in bar\n'
        "ValueError: something went wrong"
    )
    result = _sanitize_detail(detail)
    assert "Traceback" not in result
    assert "File " not in result
    assert "something went wrong" in result
