# SPDX-License-Identifier: Apache-2.0
"""Regression tests for cross-project label search (PPLX 架構改善計畫 item 4b,
Part 3).

背景：item 4a 建立了共用的 project registry（db.register_known_project /
db.list_known_projects / db.connect_foreign_project_readonly），本檔驗證
建立在其上的實際跨專案標籤搜尋能力 —— search.search_memories() 在
SearchRequest.cross_project_label 有值時，除了查詢『目前這個連線』所屬的
資料庫，還會透過 registry 找出其他已知專案，逐一以唯讀連線查詢各自的
memory_labels 表，合併結果並標記各筆結果的來源 project_id。

CRITICAL 測試隔離（與 tests/test_project_registry.py 相同理由）：registry
永遠落在 db.DEFAULT_STATE_DIR，因此必須連這個模組常數本身都 monkeypatch
成 tmp_path 底下的假路徑，絕不能讓任何一個測試寫到真實的
~/.local/state/remagraph*。
"""

from __future__ import annotations

import shutil

import pytest

import remagraph.cli as cli_mod
import remagraph.server as server_mod
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


def _make_project_with_labeled_memory(
    tmp_path,
    monkeypatch,
    project_id: str,
    *,
    mem_id: str,
    label: str,
    summary: str,
    status: str = "active",
):
    """建立一個真實、獨立的 project（各自獨立的 state_dir/DB 檔案），內含一筆
    掛有指定 label 的記憶。透過 db.connect() 走完整 migration chain
    （確保 memory_labels 表存在），並藉此自然觸發 item 4a 的自動登記副作用
    （resolve_project_state_dir -> register_known_project）。
    """
    state_dir = tmp_path / f"{project_id}-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", project_id)

    conn = db_mod.connect(project_id=project_id)
    now = "2026-07-24T00:00:00Z"
    conn.execute(
        """
        INSERT INTO memories (
            id, project_id, kind, task_id, agent_id, timestamp,
            summary, learnings, handoff_note, tags, status, created_at, updated_at
        ) VALUES (?, ?, 'task_handoff', 'task-fixture', 'agent-fixture', ?,
                  ?, '[]', '', '[]', ?, ?, ?)
        """,
        (mem_id, project_id, now, summary, status, now, now),
    )
    conn.execute(
        "INSERT INTO memory_labels (memory_id, label) VALUES (?, ?)",
        (mem_id, label),
    )
    conn.close()
    return state_dir


# ---------------------------------------------------------------------------
# 1. 正常（非跨專案）情境：目前連線所屬專案自己就能靠 label 找到記憶
# ---------------------------------------------------------------------------


def test_current_project_only_search_finds_memory_by_label(tmp_path, monkeypatch):
    """未涉及任何其他已知專案時，cross_project_label 搜尋仍須能在『目前這個
    連線』自己的資料庫內，透過 memory_labels 找到相符的記憶（(a) 的部分）。
    """
    state_dir = _make_project_with_labeled_memory(
        tmp_path,
        monkeypatch,
        "solo-proj",
        mem_id="mem-solo-1",
        label="topic:auth",
        summary="solo project fixture summary for label search",
    )
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "solo-proj")
    conn = db_mod.connect(project_id="solo-proj")

    request = SearchRequest(cross_project_label="topic:auth", project_id="solo-proj")
    response = search_mod.search_memories(conn, request)

    assert len(response.results) == 1
    assert response.results[0]["id"] == "mem-solo-1"
    assert response.results[0]["source_project_id"] == "solo-proj"
    assert response.cross_project_fanout_capped is False
    conn.close()


# ---------------------------------------------------------------------------
# 1b. Bug 回歸：project_id=None 時不得把呼叫端自己的專案在 fan-out 迴圈中
#     當成「別的」專案重新查一次，導致同一筆記憶重複出現
# ---------------------------------------------------------------------------


def test_cross_project_label_search_with_project_id_none_does_not_duplicate_own_project(
    tmp_path, monkeypatch
):
    """Regression（bug 1）：SearchRequest.project_id 是完全合法、容易觸發的
    省略值 —— remagraph_search 工具本身在呼叫端未提供 project_id 時的預設
    值，也會在 all_projects=True 併用 cross_project_label 時出現（見
    server.remagraph_search 的 eff_project = None if all_projects else
    project_id）。

    修復前：`_search_cross_project_by_label` 用 `own_project_id =
    request.project_id` 判斷是否該在 fan-out 迴圈中跳過『目前這個連線自己
    所屬的專案』；但 project_id=None 時 own_project_id 為 falsy，
    `if own_project_id and pid == own_project_id: continue` 恆為 False，
    導致目前這個連線自己的專案（雖然已透過 conn 直接查過）又被當成『別的』
    已知專案，透過 connect_foreign_project_readonly 重新開一次連線查一次，
    回傳同一筆記憶兩次。

    修復後：不論 request.project_id 是否明確提供，caller 自己的資料都不得
    在最終結果中重複出現。
    """
    state_dir = _make_project_with_labeled_memory(
        tmp_path,
        monkeypatch,
        "proj-x",
        mem_id="mem-x-1",
        label="topic:test",
        summary="single memory fixture summary for project_id none regression test",
    )
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "proj-x")
    conn = db_mod.connect(project_id="proj-x")

    request = SearchRequest(cross_project_label="topic:test", project_id=None)
    response = search_mod.search_memories(conn, request)

    assert len(response.results) == 1, (
        "project_id=None 時，caller 自己的專案不得在 fan-out 迴圈中被當成"
        "『別的』專案重新查一次而造成重複結果；預期恰好 1 筆，實際"
        f"{len(response.results)} 筆：{response.results!r}"
    )
    assert response.results[0]["id"] == "mem-x-1"
    conn.close()


# ---------------------------------------------------------------------------
# 2. 跨專案：至少 2 個獨立假專案，結果正確標記來源 project_id
# ---------------------------------------------------------------------------


def test_cross_project_label_search_merges_results_from_two_fake_projects(tmp_path, monkeypatch):
    _make_project_with_labeled_memory(
        tmp_path,
        monkeypatch,
        "proj-alpha",
        mem_id="mem-alpha-1",
        label="dep:opencode",
        summary="alpha project depends on opencode fixture summary",
    )
    _make_project_with_labeled_memory(
        tmp_path,
        monkeypatch,
        "proj-beta",
        mem_id="mem-beta-1",
        label="dep:opencode",
        summary="beta project also depends on opencode fixture summary",
    )

    # 呼叫端目前情境切到第三個、完全不相干的專案。
    caller_state_dir = tmp_path / "proj-caller-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(caller_state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "proj-caller")
    conn = db_mod.connect(project_id="proj-caller")

    request = SearchRequest(cross_project_label="dep:opencode", project_id="proj-caller")
    response = search_mod.search_memories(conn, request)

    ids_by_project = {r["source_project_id"]: r["id"] for r in response.results}
    assert ids_by_project == {
        "proj-alpha": "mem-alpha-1",
        "proj-beta": "mem-beta-1",
    }
    assert response.cross_project_fanout_capped is False
    conn.close()


# ---------------------------------------------------------------------------
# 3. 韌性：其中一個已登記專案目前不可達（目錄已被刪除），須跳過並繼續
# ---------------------------------------------------------------------------


def test_cross_project_label_search_skips_unreachable_project(tmp_path, monkeypatch):
    _make_project_with_labeled_memory(
        tmp_path,
        monkeypatch,
        "proj-reachable",
        mem_id="mem-reachable-1",
        label="kind:bug",
        summary="reachable project fixture summary for resilience test",
    )
    gone_state_dir = _make_project_with_labeled_memory(
        tmp_path,
        monkeypatch,
        "proj-gone",
        mem_id="mem-gone-1",
        label="kind:bug",
        summary="this project will be deleted before the cross-project search runs",
    )

    # 模擬「已登記但目前不可達」：整個目錄被刪掉（例如另一行程清理了它）。
    shutil.rmtree(gone_state_dir)

    caller_state_dir = tmp_path / "proj-caller2-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(caller_state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "proj-caller2")
    conn = db_mod.connect(project_id="proj-caller2")

    request = SearchRequest(cross_project_label="kind:bug", project_id="proj-caller2")
    response = search_mod.search_memories(conn, request)

    # 不可達的 proj-gone 必須被跳過（不拋例外、不讓整個搜尋失敗），
    # 仍正確回傳可達的 proj-reachable 結果。
    assert len(response.results) == 1
    assert response.results[0]["id"] == "mem-reachable-1"
    assert response.results[0]["source_project_id"] == "proj-reachable"
    conn.close()


# ---------------------------------------------------------------------------
# 4. Fan-out 上限：已知專案數超過上限時，只查前 N 個並回報 capped=True
# ---------------------------------------------------------------------------


def test_cross_project_label_search_reports_fanout_cap_when_exceeded(tmp_path, monkeypatch):
    cap = search_mod._CROSS_PROJECT_FANOUT_CAP
    # 建立 cap + 3 個假專案，全部掛上同一個 label，確保已知專案數確實超過上限。
    for i in range(cap + 3):
        _make_project_with_labeled_memory(
            tmp_path,
            monkeypatch,
            f"proj-many-{i:02d}",
            mem_id=f"mem-many-{i:02d}",
            label="topic:fanout-test",
            summary=f"project number {i} fixture summary for fan-out cap test",
        )

    caller_state_dir = tmp_path / "proj-caller3-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(caller_state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "proj-caller3")
    conn = db_mod.connect(project_id="proj-caller3")

    request = SearchRequest(
        cross_project_label="topic:fanout-test", project_id="proj-caller3", top_k=100
    )
    response = search_mod.search_memories(conn, request)

    assert response.cross_project_fanout_capped is True, (
        "已知專案數超過 _CROSS_PROJECT_FANOUT_CAP 時必須回報 capped=True，"
        "讓呼叫端知道結果可能不完整，而不是悄悄截斷佯裝完整"
    )
    # 實際查詢到的外部專案數不得超過上限（+ 目前呼叫端自己這一個，但呼叫端
    # 自己沒有掛這個 label，故結果數應恰好等於 cap）。
    assert len(response.results) == cap
    conn.close()


# ---------------------------------------------------------------------------
# 5. 既有行為不受影響：tags 過濾、all_projects 語意（project_id=None）
#    完全不受本次新增的 cross_project_label 分支影響
# ---------------------------------------------------------------------------


def test_tags_filter_unaffected_by_cross_project_label_addition(tmp_path, monkeypatch):
    """既有 tags 過濾行為（與 cross_project_label 完全無關的既有功能）須
    維持不變 —— 不帶 cross_project_label 時，走既有程式碼路徑，結果不受
    任何影響。"""
    state_dir = tmp_path / "tags-proj-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "tags-proj")
    conn = db_mod.connect(project_id="tags-proj")

    now = "2026-07-24T00:00:00Z"
    conn.execute(
        """
        INSERT INTO memories (
            id, project_id, kind, task_id, agent_id, timestamp,
            summary, learnings, handoff_note, tags, status, created_at, updated_at
        ) VALUES ('mem-tags-1', 'tags-proj', 'task_handoff', 'task-fixture',
                  'agent-fixture', ?, 'a summary with an irrelevant distinctive marker word',
                  '[]', '', '["urgent","db"]', 'active', ?, ?)
        """,
        (now, now, now),
    )

    request = SearchRequest(query="", tags=["urgent"], project_id="tags-proj", top_k=10)
    response = search_mod.search_memories(conn, request)

    assert len(response.results) == 1
    assert response.results[0]["id"] == "mem-tags-1"
    assert response.cross_project_fanout_capped is False
    conn.close()


def test_all_projects_flag_unaffected_and_does_not_trigger_fanout(tmp_path, monkeypatch):
    """既有 all_projects 語意（remagraph_search/remagraph_status 呼叫端把
    project_id 設為 None，只移除『目前這個資料庫檔案內』的 project_id
    過濾）必須完全不受影響，且與跨專案標籤搜尋的 fan-out 機制完全脫鉤 ——
    project_id=None、cross_project_label=None 時，絕不應呼叫
    db.list_known_projects()/db.connect_foreign_project_readonly()（沒有
    任何理由需要開啟其他 project 的資料庫檔案）。"""
    state_dir = tmp_path / "allproj-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "allproj-a")
    conn = db_mod.connect(project_id="allproj-a")

    now = "2026-07-24T00:00:00Z"
    for pid, mem_id in (("allproj-a", "mem-aa-1"), ("allproj-b", "mem-bb-1")):
        conn.execute(
            """
            INSERT INTO memories (
                id, project_id, kind, task_id, agent_id, timestamp,
                summary, learnings, handoff_note, tags, status, created_at, updated_at
            ) VALUES (?, ?, 'task_handoff', 'task-fixture', 'agent-fixture', ?,
                      'distinctivemarker summary shared across both fake projects here',
                      '[]', '', '[]', 'active', ?, ?)
            """,
            (mem_id, pid, now, now, now),
        )

    list_known_calls: list[None] = []
    connect_foreign_calls: list[str] = []
    monkeypatch.setattr(
        db_mod, "list_known_projects", lambda: (list_known_calls.append(None) or [])
    )
    monkeypatch.setattr(
        db_mod,
        "connect_foreign_project_readonly",
        lambda pid: (connect_foreign_calls.append(pid) or None),
    )

    request = SearchRequest(query="distinctivemarker", project_id=None, top_k=10)
    response = search_mod.search_memories(conn, request)

    ids = {r["id"] for r in response.results}
    assert ids == {"mem-aa-1", "mem-bb-1"}, (
        "all_projects 語意（project_id=None）須維持不變：回傳同一個資料庫檔案內"
        "跨 project_id 的所有相符記錄"
    )
    assert list_known_calls == [], (
        "project_id=None 但 cross_project_label 未設定時，絕不應呼叫 "
        "db.list_known_projects()"
    )
    assert connect_foreign_calls == [], (
        "project_id=None 但 cross_project_label 未設定時，絕不應呼叫 "
        "db.connect_foreign_project_readonly()"
    )
    conn.close()


# ---------------------------------------------------------------------------
# 6. 端到端外層接線：server.remagraph_search / CLI `search --cross-project-label`
# ---------------------------------------------------------------------------


def test_server_remagraph_search_wires_cross_project_label_end_to_end(tmp_path, monkeypatch):
    """Regression: server.remagraph_search 的 cross_project_label 參數須
    正確傳遞到 search.search_memories()，並在回應中附上
    cross_project_fanout_capped 欄位。"""
    _make_project_with_labeled_memory(
        tmp_path,
        monkeypatch,
        "srv-proj-a",
        mem_id="mem-srv-a-1",
        label="dep:opencode",
        summary="server-level wiring fixture summary for project a",
    )

    caller_state_dir = tmp_path / "srv-caller-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(caller_state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "srv-caller")
    server_mod._conn = None  # 重置 module-level singleton，確保套用新 env

    result = server_mod.remagraph_search(
        query="",
        project_id="srv-caller",
        cross_project_label="dep:opencode",
    )

    assert result["cross_project_fanout_capped"] is False
    ids_by_project = {
        r["source_project_id"]: r["id"] for r in result["results"]
    }
    assert ids_by_project == {"srv-proj-a": "mem-srv-a-1"}

    server_mod._conn = None


def test_cli_search_wires_cross_project_label_end_to_end(tmp_path, monkeypatch, capsys):
    """Regression: CLI `remagraph search --cross-project-label` 須正確傳遞
    到 search.search_memories() 並印出合併後的結果。"""
    _make_project_with_labeled_memory(
        tmp_path,
        monkeypatch,
        "cli-proj-a",
        mem_id="mem-cli-a-1",
        label="topic:auth",
        summary="cli-level wiring fixture summary for project a",
    )

    caller_state_dir = tmp_path / "cli-caller-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(caller_state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "cli-caller")

    args = cli_mod.build_parser().parse_args(
        [
            "search",
            "--project",
            "cli-caller",
            "--cross-project-label",
            "topic:auth",
        ]
    )
    cli_mod.cmd_search(args)

    import json

    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip())
    assert "cross_project_fanout_capped" in payload
    assert payload["cross_project_fanout_capped"] is False
    ids_by_project = {
        r["source_project_id"]: r["id"] for r in payload["results"]
    }
    assert ids_by_project == {"cli-proj-a": "mem-cli-a-1"}
