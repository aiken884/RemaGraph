# SPDX-License-Identifier: Apache-2.0
"""Integration tests for discovered_constraint invalidation logic.

Covers:
- successful invalidation when invalidates references existing discovered_constraint
- rejection when invalidates references non-existent id
- rejection when invalidates references a memory of wrong kind
"""

from __future__ import annotations

import pytest

import remagraph.server as server


def _reset_conn():
    server._conn = None


@pytest.fixture(autouse=True)
def setup_test_env(tmp_path, monkeypatch):
    state_dir = str(tmp_path / "state")
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", state_dir)
    _reset_conn()
    yield
    _reset_conn()


def test_invalidate_discovered_constraint_success():
    # create a discovered_constraint memory to be invalidated
    r1 = server.remagraph_store(
        project_id="testproj",
        task_id="task-inv-001",
        agent_id="inv-tester",
        kind="discovered_constraint",
        summary=(
            "這是一個發現的限制，描述需足夠長以通過仲裁規則，至少三十個字元以上。"
            "為了保險，額外補充更多細節文字以超過門檻並提供真實情境說明。"
        ),
        learnings=["constraint: cannot access resource X"],
        handoff_note="",
        tags=["constraint"],
    )
    assert r1["status"] == "stored"
    mem_id = r1["id"]

    # now store another discovered_constraint that invalidates the first
    r2 = server.remagraph_store(
        project_id="testproj",
        task_id="task-inv-002",
        agent_id="inv-tester",
        kind="discovered_constraint",
        summary=(
            "為了修正需求，這筆發現的限制會 invalidates 前一筆記憶，描述同樣要足夠長。"
            "額外加入一些上下文與詳細說明來確保超過仲裁規則的長度門檻並避免被拒絕。"
        ),
        learnings=["invalidates previous constraint"],
        handoff_note="",
        tags=["constraint"],
        invalidates=[mem_id],
    )

    assert r2["status"] == "stored"
    assert r2["invalidated_count"] == 1


def test_invalidate_not_found_rejected():
    # attempt to invalidate a non-existent id
    r = server.remagraph_store(
        project_id="testproj",
        task_id="task-inv-003",
        agent_id="inv-tester",
        kind="discovered_constraint",
        summary=("此筆嘗試 invalidates 不存在的 id，應被拒絕並回傳 invalidates_not_found。"),
        learnings=["testing invalidates_not_found"],
        handoff_note="",
        tags=[],
        invalidates=["mem-999999-000"],
    )

    assert r["status"] == "rejected"
    assert r.get("reason") == "invalidates_not_found"


def test_invalidate_kind_mismatch_rejected():
    # create a task_handoff memory
    r1 = server.remagraph_store(
        project_id="testproj",
        task_id="task-inv-004",
        agent_id="inv-tester",
        kind="task_handoff",
        summary=(
            "這是一個 task_handoff，會被用來測試 kind mismatch 的情況，需長一點文字。"
            "額外加入更多描述以確保 summary 超過仲裁規則要求的最小長度，避免被拒絕。"
        ),
        learnings=["handoff example"],
        handoff_note=(
            "這是交接筆記，長度充分以通過檢查，請在接手時注意環境設定與初始化流程，"
            "至少要二十個中文字以上。"
        ),
        tags=["test"],
    )
    assert r1["status"] == "stored"
    mem_id = r1["id"]

    # attempt to invalidate the task_handoff with a discovered_constraint
    r2 = server.remagraph_store(
        project_id="testproj",
        task_id="task-inv-005",
        agent_id="inv-tester",
        kind="discovered_constraint",
        summary=(
            "嘗試用 discovered_constraint invalidates 一個非 discovered_constraint，應被拒絕。"
        ),
        learnings=["testing kind mismatch"],
        handoff_note="",
        tags=[],
        invalidates=[mem_id],
    )

    assert r2["status"] == "rejected"
    assert r2.get("reason") == "invalidates_kind_mismatch"
