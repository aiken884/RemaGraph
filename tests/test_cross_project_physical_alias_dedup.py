# SPDX-License-Identifier: Apache-2.0
"""Regression tests: _cross_project_fanout() 必須以『實體資料庫檔案路徑』
判斷某個候選 project_id 是否與呼叫端自己的連線是同一個 SQLite 檔案，而不是
只比對 project_id 字串。

真實回歸 bug（已於實機重現）：在使用者的機器上，'default' state dir 與
已註冊的 'RemaGraph' 專案剛好都解析到同一個實體路徑
（~/.local/state/remagraph/remagraph.db）。修復前，_cross_project_fanout()
只用 `pid == own_project_id` 這個純字串比對決定是否跳過某個候選——
own_project_id 為 None（未帶 --project）、或候選的邏輯名稱與自己不同時，
這個字串比對永遠不會命中，導致同一個實體資料庫檔案被開啟第二次、同一筆
記憶被回傳兩次；且因為兩次出現的 source_project_id 標籤字串不同
（None/'default' vs 'RemaGraph'），連最終依 (source_project_id, id) 的
去重步驟也攔不住。

本檔案分別針對兩個共用 _cross_project_fanout() 的呼叫端各驗證一次：
1. _search_cross_project_by_label（cross_project_label 搜尋，item 4b）
2. _search_related_projects（include_related 搜尋，item 5）

CRITICAL 測試隔離（與 tests/test_cross_project_label_search.py、
tests/test_project_edges_and_recall_related.py 相同理由）：registry/edges
永遠落在 db.DEFAULT_STATE_DIR，因此必須連這個模組常數本身都 monkeypatch 成
tmp_path 底下的假路徑，絕不能讓任何一個測試寫到真實的
~/.local/state/remagraph*。
"""

from __future__ import annotations

import pytest

from remagraph import db as db_mod
from remagraph import search as search_mod
from remagraph.models import SearchRequest


@pytest.fixture(autouse=True)
def isolated_default_state_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)
    monkeypatch.delenv("REMAGRAPH_PROJECT", raising=False)
    fake_default = tmp_path / "fake-default-state"
    monkeypatch.setattr(db_mod, "DEFAULT_STATE_DIR", fake_default)
    return fake_default


# ---------------------------------------------------------------------------
# 1. cross_project_label：own_project_id=None，候選 'RemaGraph' 與 default
#    state dir 物理上是同一個檔案
# ---------------------------------------------------------------------------


def test_cross_project_label_search_dedupes_default_and_aliased_registered_project(
    tmp_path, monkeypatch, isolated_default_state_dir
):
    """實測還原：`remagraph search --cross-project-label ...`（無 --project，
    即 SearchRequest.project_id=None）在 default state dir 與一個已註冊的
    'RemaGraph' 專案實際指向同一個實體目錄時，不得回傳同一筆記憶兩次。
    """
    fake_default = isolated_default_state_dir

    # 呼叫端目前這個連線：沒有 --project、沒有 REMAGRAPH_PROJECT env，
    # 因而落在 default state dir（走過完整 migration chain，含
    # memory_labels 表）。
    conn = db_mod.connect()
    now = "2026-07-24T00:00:00Z"
    conn.execute(
        """
        INSERT INTO memories (
            id, project_id, kind, task_id, agent_id, timestamp,
            summary, learnings, handoff_note, tags, status, created_at, updated_at
        ) VALUES ('mem-alias-1', 'default', 'task_handoff', 'task-fixture',
                  'agent-fixture', ?,
                  'aliasing bug fixture memory for default vs RemaGraph',
                  '[]', '', '[]', 'active', ?, ?)
        """,
        (now, now, now),
    )
    conn.execute(
        "INSERT INTO memory_labels (memory_id, label) VALUES "
        "('mem-alias-1', 'topic:remagraph-new-version')"
    )

    # 模擬「已註冊的 'RemaGraph' 專案剛好指向與 default 完全相同的實體
    # 目錄」——真實情境下，這是使用者自己曾在該目錄以 project_id='RemaGraph'
    # 執行過 remagraph，才被自動登記進共用 registry（resolve_project_state_dir
    # -> register_known_project 的既有副作用）。
    db_mod.register_known_project("RemaGraph", fake_default)

    request = SearchRequest(cross_project_label="topic:remagraph-new-version", project_id=None)
    response = search_mod.search_memories(conn, request)

    assert len(response.results) == 1, (
        "own_project_id=None 且候選專案 'RemaGraph' 與目前連線實際指向"
        "同一個實體 SQLite 檔案時，同一筆記憶不得被回傳兩次；實際回傳 "
        f"{len(response.results)} 筆：{response.results!r}"
    )
    assert response.results[0]["id"] == "mem-alias-1"
    conn.close()


# ---------------------------------------------------------------------------
# 2. include_related：own_project_id 有明確字串值，但候選關聯專案的登記
#    名稱與自己不同、卻物理上是同一個實體檔案
# ---------------------------------------------------------------------------


def test_include_related_search_dedupes_own_project_and_aliased_related_project(
    tmp_path, monkeypatch
):
    """同一個 _cross_project_fanout() 也供 include_related 使用，須同樣
    正確處理：own_project_id 有明確字串值（'proj-caller'），但透過
    project_edges 宣告關聯的候選專案 'proj-caller-alias' 剛好登記指向與
    'proj-caller' 完全相同的實體目錄（而非另一個真正獨立的資料庫）。
    """
    caller_state_dir = tmp_path / "caller-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(caller_state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "proj-caller")
    conn = db_mod.connect(project_id="proj-caller")

    now = "2026-07-24T00:00:00Z"
    conn.execute(
        """
        INSERT INTO memories (
            id, project_id, kind, task_id, agent_id, timestamp,
            summary, learnings, handoff_note, tags, status, created_at, updated_at
        ) VALUES ('mem-related-alias-1', 'proj-caller', 'task_handoff', 'task-fixture',
                  'agent-fixture', ?,
                  'distinctivemarker aliasing bug fixture for include_related',
                  '[]', '', '[]', 'active', ?, ?)
        """,
        (now, now, now),
    )

    # 'proj-caller-alias' 剛好登記指向與 'proj-caller' 完全相同的實體目錄
    # （模擬同一個實體 DB 檔案被兩個不同的邏輯 project_id 名稱指到）。
    db_mod.register_known_project("proj-caller-alias", caller_state_dir)
    db_mod.declare_project_edge("proj-caller", "proj-caller-alias", "sibling")

    request = SearchRequest(
        query="distinctivemarker",
        project_id="proj-caller",
        include_related=True,
        related_hops=1,
        top_k=10,
    )
    response = search_mod.search_memories(conn, request)

    assert len(response.results) == 1, (
        "候選關聯專案 'proj-caller-alias' 與呼叫端自己的專案 'proj-caller' "
        "物理上是同一個 SQLite 檔案時，同一筆記憶不得被回傳兩次；實際回傳 "
        f"{len(response.results)} 筆：{response.results!r}"
    )
    assert response.results[0]["id"] == "mem-related-alias-1"
    conn.close()
