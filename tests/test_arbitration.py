"""測試 arbitration.py — 仲裁規則 #1, #2, #3, #5 + reason codes，涵蓋 D05 A1-A9。"""

import sqlite3

import pytest

from remagraph.arbitration import (
    ArbitrationResult,
    InvalidateResult,
    SupersedeResult,
    invalidate_constraints,
    run_arbitration_rules_cheap,
    supersede_status_updates,
    validate_agent_id,
    validate_handoff_note,
    validate_labels,
    validate_learnings,
    validate_summary_length,
)
from remagraph.models import StoreRequest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn():
    """in-memory SQLite，含完整 schema。"""
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA foreign_keys=ON")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id          TEXT PRIMARY KEY,
            project_id  TEXT NOT NULL DEFAULT 'default',
            kind        TEXT NOT NULL CHECK (
                kind IN ('task_handoff', 'status_update', 'discovered_constraint', 'fleet_member')
            ),
            task_id     TEXT NOT NULL,
            agent_id    TEXT NOT NULL,
            timestamp   TEXT NOT NULL,
            summary     TEXT NOT NULL,
            learnings   TEXT NOT NULL DEFAULT '[]',
            handoff_note TEXT NOT NULL DEFAULT '',
            tags        TEXT NOT NULL DEFAULT '[]',
            status      TEXT NOT NULL DEFAULT 'active' CHECK (
                status IN ('active', 'superseded', 'invalidated')
            ),
            embedding   BLOB,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
    """)
    return c


def _valid_sample_request(**overrides) -> StoreRequest:
    """建立合法的 StoreRequest 供測試複用。"""
    defaults = {
        "project_id": "testproj",
        "task_id": "task-2026-07-21-003",
        "agent_id": "oc-dspro",
        "kind": "task_handoff",
        "summary": (
            "嘗試修復 subagent 委派 + deny-all 時的 acpx 連線錯誤，這是一個需要深入調查的複雜問題"
        ),
        "learnings": ["錯誤發生在 opencode task tool 生成 child session 之後"],
        "handoff_note": "接手者請注意：此錯誤與 G1 不同，G1 是 child session 未被註冊",
        "tags": ["acpx", "subagent", "bug"],
    }
    defaults.update(overrides)
    return StoreRequest(**defaults)


# ---------------------------------------------------------------------------
# A1: validate_summary_length（規則 #1）
# ---------------------------------------------------------------------------
def test_summary_too_short():
    """A1: summary < 30 codepoint 應回傳 summary_too_short。"""
    result = validate_summary_length("太短")
    assert result.passed is False
    assert result.reason == "summary_too_short"
    assert "30" in result.detail


def test_summary_exactly_30_chars():
    """A1: summary 恰好 30 字應通過。"""
    s = "一二三四五六七八九十一二三四五六七八九十一二三四五六七八九十"
    assert len(s.strip()) == 30
    result = validate_summary_length(s)
    assert result.passed is True
    assert result.reason is None


def test_summary_only_whitespace():
    """A1: summary 僅含空白字元應被拒絕。"""
    result = validate_summary_length("   \n  \t  ")
    assert result.passed is False
    assert result.reason == "summary_too_short"


def test_summary_empty_string():
    """A1: summary 為空字串。"""
    result = validate_summary_length("")
    assert result.passed is False
    assert result.reason == "summary_too_short"


def test_summary_passes_with_enough_chars():
    """A1: summary 超過 30 字應通過。"""
    s = "這是一個足夠長的 summary 字串，包含超過三十個中文字元，確保可以通過規則一的檢查測試"
    assert len(s.strip()) >= 30
    result = validate_summary_length(s)
    assert result.passed is True


def test_summary_unicode_codepoint_count():
    """A1: Unicode codepoint 計數（非 byte）。"""
    s = "a" * 30
    result = validate_summary_length(s)
    assert result.passed is True


# ---------------------------------------------------------------------------
# A2: validate_learnings（規則 #2）
# ---------------------------------------------------------------------------
def test_learnings_empty_list():
    """A2: learnings 為空陣列應拒絕。"""
    result = validate_learnings([])
    assert result.passed is False
    assert result.reason == "learnings_empty"


def test_learnings_single_empty_string():
    """A2: learnings 只有一個空字串。"""
    result = validate_learnings([""])
    assert result.passed is False
    assert result.reason == "learnings_empty"


def test_learnings_all_whitespace():
    """A2: learnings 所有元素都是空白。"""
    result = validate_learnings(["  ", "\n", "\t"])
    assert result.passed is False
    assert result.reason == "learnings_empty"


def test_learnings_single_valid():
    """A2: learnings 有一筆有效內容。"""
    result = validate_learnings(["有意義的學習內容"])
    assert result.passed is True


def test_learnings_mixed_valid_and_empty():
    """A2: learnings 混合有效與空白元素仍通過（因至少一筆有效）。"""
    result = validate_learnings(["", "  ", "有效內容"])
    assert result.passed is True


# ---------------------------------------------------------------------------
# A3: validate_handoff_note（規則 #3）
# ---------------------------------------------------------------------------
def test_handoff_note_too_short_for_task_handoff():
    """A3: kind=task_handoff 且 handoff_note < 20 字應拒絕。"""
    result = validate_handoff_note("task_handoff", "接手者請注意")
    assert result.passed is False
    assert result.reason == "handoff_note_too_short"


def test_handoff_note_passes_for_task_handoff():
    """A3: kind=task_handoff 且 handoff_note ≥ 20 字應通過。"""
    result = validate_handoff_note("task_handoff", "接手者請注意：此錯誤與 G1 不同，需要特別處理")
    assert result.passed is True


def test_handoff_note_skipped_for_status_update():
    """A3: kind=status_update 時跳過 handoff_note 檢查。"""
    result = validate_handoff_note("status_update", "")
    assert result.passed is True


def test_handoff_note_skipped_for_discovered_constraint():
    """A3: kind=discovered_constraint 時跳過 handoff_note 檢查。"""
    result = validate_handoff_note("discovered_constraint", "")
    assert result.passed is True


def test_handoff_note_exactly_20_chars():
    """A3: handoff_note 恰好 20 字應通過。"""
    s = "一二三四五六七八九十一二三四五六七八九十"
    assert len(s.strip()) == 20
    result = validate_handoff_note("task_handoff", s)
    assert result.passed is True


# ---------------------------------------------------------------------------
# A5: validate_agent_id（規則 #5）
# ---------------------------------------------------------------------------
def test_valid_agent_id():
    """A5: 合法的 agent_id 通過。"""
    result = validate_agent_id("oc-dspro")
    assert result.passed is True


def test_agent_id_with_uppercase():
    """A5: 含大寫字母應拒絕。"""
    result = validate_agent_id("OC-DSPRO")
    assert result.passed is False
    assert result.reason == "invalid_agent_id"


def test_agent_id_too_short():
    """A5: 長度 < 3 應拒絕。"""
    result = validate_agent_id("ab")
    assert result.passed is False
    assert result.reason == "invalid_agent_id"


def test_agent_id_too_long():
    """A5: 長度 > 64 應拒絕。"""
    result = validate_agent_id("a" * 65)
    assert result.passed is False
    assert result.reason == "invalid_agent_id"


def test_agent_id_min_length():
    """A5: 長度恰好 3 應通過。"""
    result = validate_agent_id("abc")
    assert result.passed is True


def test_agent_id_max_length():
    """A5: 長度恰好 64 應通過。"""
    result = validate_agent_id("a" * 64)
    assert result.passed is True


def test_agent_id_with_dot():
    """A5: 含 . 應拒絕。"""
    result = validate_agent_id("claude.sonnet")
    assert result.passed is False
    assert result.reason == "invalid_agent_id"


def test_agent_id_with_special_chars():
    """A5: 含特殊字元應拒絕。"""
    result = validate_agent_id("agent@test")
    assert result.passed is False
    assert result.reason == "invalid_agent_id"


def test_agent_id_with_chinese():
    """A5: 含中文字元應拒絕。"""
    result = validate_agent_id("agent測試")
    assert result.passed is False
    assert result.reason == "invalid_agent_id"


# ---------------------------------------------------------------------------
# A6: run_arbitration_rules_cheap（順序 fail-fast：便宜規則 #1, #2, #3, #5）
# ---------------------------------------------------------------------------
def test_run_arbitration_cheap_all_pass():
    """A6: 全部便宜規則通過。"""
    req = _valid_sample_request()
    result = run_arbitration_rules_cheap(req)
    assert result.passed is True


def test_run_arbitration_cheap_stops_at_first_failure():
    """A6: 規則 #1 失敗時應立即停止。"""
    req = _valid_sample_request(summary="太短")
    result = run_arbitration_rules_cheap(req)
    assert result.passed is False
    assert result.reason == "summary_too_short"


def test_run_arbitration_cheap_rule2_fails():
    """A6: 規則 #2 失敗。"""
    req = _valid_sample_request(learnings=[])
    result = run_arbitration_rules_cheap(req)
    assert result.passed is False
    assert result.reason == "learnings_empty"


def test_run_arbitration_cheap_rule3_fails():
    """A6: 規則 #3 失敗。"""
    req = _valid_sample_request(handoff_note="太短")
    result = run_arbitration_rules_cheap(req)
    assert result.passed is False
    assert result.reason == "handoff_note_too_short"


def test_run_arbitration_cheap_rule5_fails():
    """A6: 規則 #5 失敗。"""
    req = _valid_sample_request(agent_id="INVALID")
    result = run_arbitration_rules_cheap(req)
    assert result.passed is False
    assert result.reason == "invalid_agent_id"


def test_run_arbitration_cheap_rule3_skipped_for_status_update():
    """A6: kind=status_update 時跳過規則 #3。"""
    req = _valid_sample_request(kind="status_update", handoff_note="")
    result = run_arbitration_rules_cheap(req)
    assert result.passed is True


# ---------------------------------------------------------------------------
# A7: supersede_status_updates
# ---------------------------------------------------------------------------
def test_supersede_status_updates(conn):
    """A7: 將同 task_id 的 active status_update 標記為 superseded。"""
    conn.execute(
        "INSERT INTO memories (id, project_id, kind, task_id, agent_id, timestamp, summary, "
        "learnings, handoff_note, tags, status, created_at, updated_at) VALUES "
        "('mem-001', 'testproj', 'status_update', 'task-a', 'test', '2026-07-21T00:00:00Z', "
        "'summary must be at least thirty characters long for the test to be valid', "
        "'[\"learn\"]', '', '[]', 'active', '2026-07-21T00:00:00Z', '2026-07-21T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO memories (id, project_id, kind, task_id, agent_id, timestamp, summary, "
        "learnings, handoff_note, tags, status, created_at, updated_at) VALUES "
        "('mem-002', 'testproj', 'status_update', 'task-a', 'test', '2026-07-21T01:00:00Z', "
        "'another summary that must be at least thirty characters long for the test', "
        "'[\"learn\"]', '', '[]', 'active', '2026-07-21T01:00:00Z', '2026-07-21T01:00:00Z')"
    )

    result = supersede_status_updates("testproj", "task-a", conn)

    assert isinstance(result, SupersedeResult)
    assert result.superseded_count == 2

    rows = conn.execute(
        "SELECT status FROM memories WHERE task_id='task-a' AND kind='status_update'"
    ).fetchall()
    assert all(r["status"] == "superseded" for r in rows)


def test_supersede_does_not_affect_other_task_ids(conn):
    """A7: supersede 不影響不同 task_id。"""
    conn.execute(
        "INSERT INTO memories (id, project_id, kind, task_id, agent_id, timestamp, summary, "
        "learnings, handoff_note, tags, status, created_at, updated_at) VALUES "
        "('mem-001', 'testproj', 'status_update', 'task-a', 'test', '2026-07-21T00:00:00Z', "
        "'summary must be at least thirty characters long for testing purpose here', "
        "'[\"learn\"]', '', '[]', 'active', '2026-07-21T00:00:00Z', '2026-07-21T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO memories (id, project_id, kind, task_id, agent_id, timestamp, summary, "
        "learnings, handoff_note, tags, status, created_at, updated_at) VALUES "
        "('mem-002', 'testproj', 'status_update', 'task-b', 'test', '2026-07-21T00:00:00Z', "
        "'another summary that must be at least thirty characters long for the test', "
        "'[\"learn\"]', '', '[]', 'active', '2026-07-21T00:00:00Z', '2026-07-21T00:00:00Z')"
    )

    result = supersede_status_updates("testproj", "task-a", conn)
    assert result.superseded_count == 1

    row = conn.execute("SELECT status FROM memories WHERE id='mem-002'").fetchone()
    assert row["status"] == "active"


def test_supersede_does_not_affect_task_handoff(conn):
    """A7: supersede 不影響 task_handoff。"""
    conn.execute(
        "INSERT INTO memories (id, project_id, kind, task_id, agent_id, timestamp, summary, "
        "learnings, handoff_note, tags, status, created_at, updated_at) VALUES "
        "('mem-001', 'testproj', 'task_handoff', 'task-c', 'test', '2026-07-21T00:00:00Z', "
        "'summary must be at least thirty characters long for testing purpose here', "
        "'[\"learn\"]', 'handoff note here for test', '[]', 'active', "
        "'2026-07-21T00:00:00Z', '2026-07-21T00:00:00Z')"
    )

    result = supersede_status_updates("testproj", "task-c", conn)
    assert result.superseded_count == 0
    row = conn.execute("SELECT status FROM memories WHERE id='mem-001'").fetchone()
    assert row is not None
    assert row["status"] == "active"


def test_supersede_no_matching_records(conn):
    """A7: 無符合條件的記錄回傳 0。"""
    result = supersede_status_updates("default", "nonexistent-task", conn)
    assert result.superseded_count == 0


# ---------------------------------------------------------------------------
# A8, A9: invalidate_constraints
# ---------------------------------------------------------------------------
def test_invalidate_constraints_basic(conn):
    """A8: 基礎 invalidate 流程。"""
    conn.execute(
        "INSERT INTO memories (id, project_id, kind, task_id, agent_id, timestamp, summary, "
        "learnings, handoff_note, tags, status, created_at, updated_at) VALUES "
        "('mem-001', 'testproj', 'discovered_constraint', 'task-d', 'test', "
        "'2026-07-21T00:00:00Z', 'summary must be at least thirty chars here', "
        "'[\"learn\"]', '', '[]', 'active', '2026-07-21T00:00:00Z', '2026-07-21T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO memories (id, project_id, kind, task_id, agent_id, timestamp, summary, "
        "learnings, handoff_note, tags, status, created_at, updated_at) VALUES "
        "('mem-002', 'testproj', 'discovered_constraint', 'task-d', 'test', "
        "'2026-07-21T00:00:00Z', 'another summary at least thirty chars for test', "
        "'[\"learn\"]', '', '[]', 'active', '2026-07-21T00:00:00Z', '2026-07-21T00:00:00Z')"
    )

    result = invalidate_constraints(["mem-001", "mem-002"], conn)

    assert isinstance(result, InvalidateResult)
    assert result.invalidated_count == 2

    rows = conn.execute("SELECT status FROM memories WHERE id IN ('mem-001', 'mem-002')").fetchall()
    assert all(r["status"] == "invalidated" for r in rows)


def test_invalidate_constraints_not_found(conn):
    """A8: 指定不存在的 id。"""
    result = invalidate_constraints(["mem-999"], conn)
    assert isinstance(result, ArbitrationResult)
    assert result.passed is False
    assert result.reason == "invalidates_not_found"


def test_invalidate_constraints_kind_mismatch(conn):
    """A8: 試圖 invalidate 非 discovered_constraint。"""
    conn.execute(
        "INSERT INTO memories (id, project_id, kind, task_id, agent_id, timestamp, summary, "
        "learnings, handoff_note, tags, status, created_at, updated_at) VALUES "
        "('mem-001', 'testproj', 'task_handoff', 'task-e', 'test', '2026-07-21T00:00:00Z', "
        "'summary must be at least thirty characters long for testing purpose here', "
        "'[\"learn\"]', 'handoff note here for the test purpose', '[]', 'active', "
        "'2026-07-21T00:00:00Z', '2026-07-21T00:00:00Z')"
    )

    result = invalidate_constraints(["mem-001"], conn)
    assert isinstance(result, ArbitrationResult)
    assert result.passed is False
    assert result.reason == "invalidates_kind_mismatch"


def test_invalidate_constraints_empty_list(conn):
    """A9: invalidates 為空時不報錯。"""
    result = invalidate_constraints([], conn)
    assert isinstance(result, InvalidateResult)
    assert result.invalidated_count == 0


# ---------------------------------------------------------------------------
# validate_labels — bug 回歸：`$` 錨點的尾端換行縫隙 + label 長度上限
# ---------------------------------------------------------------------------


def test_validate_labels_accepts_well_formed_label():
    """健全性檢查：正常格式的 label 應通過（作為下方回歸測試的對照組）。"""
    result = validate_labels(["dep:opencode"])
    assert result.passed is True


def test_validate_labels_rejects_trailing_newline():
    """Regression（bug 2）：Python re 的 `$` 錨點不只匹配字串結尾，也會匹配
    『結尾前恰有一個換行字元』的位置——即使該換行字元本身並不在
    LABEL_REGEX 宣告的 value 字元類別 [a-zA-Z0-9_-] 之內，`dep:foo\\n`
    仍會被 `^[a-z]+:[a-zA-Z0-9_-]+$`.match() 誤判為通過。
    修復後必須確實拒絕，reason 仍為 invalid_label。"""
    result = validate_labels(["dep:foo\n"])
    assert result.passed is False, (
        "trailing-newline label 'dep:foo\\n' 不應通過 validate_labels —— "
        "Python `$` 錨點會匹配『結尾前一個換行字元』的既有陷阱"
    )
    assert result.reason == "invalid_label"


def test_validate_labels_rejects_embedded_newline_followed_by_more_text():
    """確認 `$` 的另一個邊界情況維持正確拒絕：換行後面還有更多文字時，
    非 MULTILINE 模式的 `$` 本來就不會匹配到字串中段，這裡作為對照組
    確保修復（改用 \\Z 或 fullmatch）沒有意外改變這個既有正確行為。"""
    result = validate_labels(["dep:foo\nbar:baz"])
    assert result.passed is False
    assert result.reason == "invalid_label"


def test_validate_labels_rejects_label_exceeding_max_length():
    """Regression（minor hardening）：目前 LABEL_REGEX 對 value 部分的
    字元類別沒有數量上限，一個 100KB+ 的字串會被接受。比照 models.py
    既有 task_id/agent_id 的 64 字元上限慣例，加上長度檢查。"""
    too_long_value = "x" * 100_000
    result = validate_labels([f"dep:{too_long_value}"])
    assert result.passed is False
    assert result.reason == "invalid_label"


def test_validate_labels_boundary_max_length_accepted_one_over_rejected():
    """邊界測試：剛好等於上限的 label 應通過，超過 1 個字元則被拒絕。"""
    from remagraph.arbitration import LABEL_MAX_LENGTH

    at_max = "d:" + "x" * (LABEL_MAX_LENGTH - 2)
    assert len(at_max) == LABEL_MAX_LENGTH
    result = validate_labels([at_max])
    assert result.passed is True, f"剛好 {LABEL_MAX_LENGTH} 字元的 label 應通過"

    over_max = at_max + "x"
    assert len(over_max) == LABEL_MAX_LENGTH + 1
    result = validate_labels([over_max])
    assert result.passed is False, f"超過 {LABEL_MAX_LENGTH} 字元的 label 應被拒絕"
    assert result.reason == "invalid_label"
