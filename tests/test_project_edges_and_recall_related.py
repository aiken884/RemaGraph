# SPDX-License-Identifier: Apache-2.0
"""Regression tests for project_edges + recall_related (PPLX 架構改善計畫 item 5).

背景：item 4a 建立了共用的 project registry（db.register_known_project /
db.list_known_projects / db.connect_foreign_project_readonly）；item 4b 在此
之上建立了「依 label 跨『所有』已知專案搜尋」的 fan-out 機制
（search._search_cross_project_by_label）。本檔驗證的是 item 5：
project_edges 這張『專案之間關聯』表（同樣落在 DEFAULT_STATE_DIR 的
remagraph.db，與 project_registry 同一份檔案）、db.declare_project_edge /
db.get_project_edges / db.recall_related 這三個函式，以及建立在其上、範圍
限縮為「明確宣告為圖形關聯」專案的 include_related 搜尋 fan-out（重用 item
4b 的 fan-out/去重/上限機制，見 search._cross_project_fanout）。

CRITICAL 測試隔離（與 tests/test_project_registry.py、
tests/test_cross_project_label_search.py 相同理由）：registry/edges 永遠落在
db.DEFAULT_STATE_DIR，因此必須連這個模組常數本身都 monkeypatch 成 tmp_path
底下的假路徑，絕不能讓任何一個測試寫到真實的 ~/.local/state/remagraph*。
"""

from __future__ import annotations

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


def _make_project_with_memory(
    tmp_path,
    monkeypatch,
    project_id: str,
    *,
    mem_id: str,
    summary: str,
    status: str = "active",
):
    """建立一個真實、獨立的 project（各自獨立的 state_dir/DB 檔案），內含一筆
    記憶。透過 db.connect() 走完整 migration chain，並藉此自然觸發 item 4a
    的自動登記副作用（resolve_project_state_dir -> register_known_project）。
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
    conn.close()
    return state_dir


# ---------------------------------------------------------------------------
# 1. project_edges 可宣告並讀回 -- 四種 relation 各自
# ---------------------------------------------------------------------------


def test_declare_and_read_back_project_edge_for_each_relation_type(tmp_path, monkeypatch):
    relations = ["depends_on", "sibling", "shares_upstream", "monorepo_member"]
    for relation in relations:
        db_mod.declare_project_edge("proj-a", "proj-b", relation)

    edges = db_mod.get_project_edges("proj-a")
    found_relations = {e["relation"] for e in edges}
    assert found_relations == set(relations)
    for e in edges:
        assert e["from_project"] == "proj-a"
        assert e["to_project"] == "proj-b"
        assert e["created_at"]


def test_declare_project_edge_rejects_invalid_relation(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        db_mod.declare_project_edge("proj-a", "proj-b", "not_a_real_relation")


# ---------------------------------------------------------------------------
# 2. get_project_edges 不論宣告時哪一側是 from/to，都能從另一側找到
# ---------------------------------------------------------------------------


def test_get_project_edges_finds_edge_regardless_of_declaring_side(tmp_path, monkeypatch):
    db_mod.declare_project_edge("proj-x", "proj-y", "depends_on")

    edges_from_x = db_mod.get_project_edges("proj-x")
    edges_from_y = db_mod.get_project_edges("proj-y")

    assert len(edges_from_x) == 1
    assert len(edges_from_y) == 1
    assert edges_from_x[0]["from_project"] == "proj-x"
    assert edges_from_x[0]["to_project"] == "proj-y"
    # 同一筆 edge，從被宣告的「另一側」(to_project) 查詢一樣要找得到。
    assert edges_from_y[0]["from_project"] == "proj-x"
    assert edges_from_y[0]["to_project"] == "proj-y"


# ---------------------------------------------------------------------------
# 3. 1-hop include_related 搜尋能在直接關聯的專案中找到真實記憶
# ---------------------------------------------------------------------------


def test_one_hop_include_related_search_finds_memory_in_directly_related_project(
    tmp_path, monkeypatch
):
    _make_project_with_memory(
        tmp_path,
        monkeypatch,
        "hop-related",
        mem_id="mem-related-1",
        summary="distinctivemarker memory living in the directly related project",
    )

    caller_state_dir = tmp_path / "hop-caller-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(caller_state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "hop-caller")
    conn = db_mod.connect(project_id="hop-caller")

    db_mod.declare_project_edge("hop-caller", "hop-related", "depends_on")

    request = SearchRequest(
        query="distinctivemarker",
        project_id="hop-caller",
        include_related=True,
        related_hops=1,
        top_k=10,
    )
    response = search_mod.search_memories(conn, request)

    ids_by_project = {r["source_project_id"]: r["id"] for r in response.results}
    assert ids_by_project == {"hop-related": "mem-related-1"}
    assert response.cross_project_fanout_capped is False
    conn.close()


# ---------------------------------------------------------------------------
# 4. 2+ hop traversal：只有透過中介專案才能到達的專案，且受 hops 參數限制
# ---------------------------------------------------------------------------


def test_two_hop_traversal_reaches_project_only_via_intermediate_and_is_bounded_by_hops(
    tmp_path, monkeypatch
):
    _make_project_with_memory(
        tmp_path,
        monkeypatch,
        "far-away",
        mem_id="mem-far-1",
        summary="distinctivemarker memory only reachable via two hops from origin",
    )
    _make_project_with_memory(
        tmp_path,
        monkeypatch,
        "middle-hop",
        mem_id="mem-middle-1",
        summary="distinctivemarker memory living in the intermediate project itself",
    )

    caller_state_dir = tmp_path / "twohop-caller-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(caller_state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "twohop-caller")
    conn = db_mod.connect(project_id="twohop-caller")

    # origin -> middle-hop -> far-away (only path to far-away)
    db_mod.declare_project_edge("twohop-caller", "middle-hop", "depends_on")
    db_mod.declare_project_edge("middle-hop", "far-away", "sibling")

    # hops=1: only middle-hop is reachable, far-away must NOT be found.
    request_1hop = SearchRequest(
        query="distinctivemarker",
        project_id="twohop-caller",
        include_related=True,
        related_hops=1,
        top_k=10,
    )
    response_1hop = search_mod.search_memories(conn, request_1hop)
    sources_1hop = {r["source_project_id"] for r in response_1hop.results}
    assert sources_1hop == {"middle-hop"}
    assert "far-away" not in sources_1hop

    # hops=2: both middle-hop and far-away must be found.
    request_2hop = SearchRequest(
        query="distinctivemarker",
        project_id="twohop-caller",
        include_related=True,
        related_hops=2,
        top_k=10,
    )
    response_2hop = search_mod.search_memories(conn, request_2hop)
    sources_2hop = {r["source_project_id"] for r in response_2hop.results}
    assert sources_2hop == {"middle-hop", "far-away"}
    conn.close()


# ---------------------------------------------------------------------------
# 5. 環狀 edge graph（A-B-C-A）不會造成無窮迴圈
# ---------------------------------------------------------------------------


def test_recall_related_handles_cycle_without_infinite_loop(tmp_path, monkeypatch):
    db_mod.declare_project_edge("cyc-a", "cyc-b", "sibling")
    db_mod.declare_project_edge("cyc-b", "cyc-c", "sibling")
    db_mod.declare_project_edge("cyc-c", "cyc-a", "sibling")

    # 只有 3 個節點的環；不論 hops 設多大，結果集不得超過另外 2 個節點，
    # 且函式必須在合理時間內回傳（不會卡在無窮迴圈）。
    related = db_mod.recall_related("cyc-a", hops=5)
    assert related == {"cyc-b", "cyc-c"}

    related_1hop = db_mod.recall_related("cyc-a", hops=1)
    assert related_1hop == {"cyc-b", "cyc-c"}


# ---------------------------------------------------------------------------
# 6. include_related=True 但 project_id=None：不得崩潰，優雅退化
# ---------------------------------------------------------------------------


def test_include_related_with_project_id_none_does_not_crash_and_falls_back(
    tmp_path, monkeypatch, caplog
):
    state_dir = tmp_path / "noproj-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "noproj-caller")
    conn = db_mod.connect(project_id="noproj-caller")

    now = "2026-07-24T00:00:00Z"
    conn.execute(
        """
        INSERT INTO memories (
            id, project_id, kind, task_id, agent_id, timestamp,
            summary, learnings, handoff_note, tags, status, created_at, updated_at
        ) VALUES ('mem-noproj-1', 'noproj-caller', 'task_handoff', 'task-fixture',
                  'agent-fixture', ?, 'distinctivemarker summary present in own db only',
                  '[]', '', '[]', 'active', ?, ?)
        """,
        (now, now, now),
    )

    import logging

    with caplog.at_level(logging.WARNING):
        request = SearchRequest(
            query="distinctivemarker",
            project_id=None,
            include_related=True,
            related_hops=1,
            top_k=10,
        )
        response = search_mod.search_memories(conn, request)

    # 沒有崩潰，且優雅退化為一般搜尋（本身這個連線的結果仍然找得到）。
    assert len(response.results) == 1
    assert response.results[0]["id"] == "mem-noproj-1"
    conn.close()


# ---------------------------------------------------------------------------
# 7. 三個能力彼此完全脫鉤：cross_project_label / include_related / all_projects
#    各自獨立運作，不會觸發彼此的 fan-out 邏輯
# ---------------------------------------------------------------------------


def test_cross_project_label_include_related_all_projects_are_fully_decoupled(
    tmp_path, monkeypatch
):
    state_dir = tmp_path / "decouple-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "decouple-proj")
    conn = db_mod.connect(project_id="decouple-proj")

    now = "2026-07-24T00:00:00Z"
    conn.execute(
        """
        INSERT INTO memories (
            id, project_id, kind, task_id, agent_id, timestamp,
            summary, learnings, handoff_note, tags, status, created_at, updated_at
        ) VALUES ('mem-decouple-1', 'decouple-proj', 'task_handoff', 'task-fixture',
                  'agent-fixture', ?, 'decouplemarker summary for isolation test',
                  '[]', '', '[]', 'active', ?, ?)
        """,
        (now, now, now),
    )
    conn.execute(
        "INSERT INTO memory_labels (memory_id, label) VALUES ('mem-decouple-1', 'topic:iso')"
    )

    # (a) cross_project_label 單獨使用：不得呼叫 recall_related。
    recall_related_calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        db_mod,
        "recall_related",
        lambda pid, hops=1: (recall_related_calls.append((pid, hops)) or set()),
    )
    request_label = SearchRequest(cross_project_label="topic:iso", project_id="decouple-proj")
    response_label = search_mod.search_memories(conn, request_label)
    assert len(response_label.results) == 1
    assert recall_related_calls == [], (
        "cross_project_label 單獨使用時絕不能觸發 include_related 的 "
        "recall_related traversal"
    )

    # (b) include_related 單獨使用：不得呼叫 list_known_projects（item 4b 的
    # label fan-out 專用機制）。
    list_known_calls: list[None] = []
    monkeypatch.setattr(
        db_mod, "list_known_projects", lambda: (list_known_calls.append(None) or [])
    )
    request_related = SearchRequest(
        query="decouplemarker",
        project_id="decouple-proj",
        include_related=True,
        related_hops=1,
        top_k=10,
    )
    response_related = search_mod.search_memories(conn, request_related)
    assert len(response_related.results) == 1
    assert list_known_calls == [], (
        "include_related 單獨使用時絕不能觸發 cross_project_label 的 "
        "list_known_projects 全量 fan-out"
    )

    # (c) all_projects（project_id=None，且未設定 cross_project_label /
    # include_related）：不得觸發任何一種 fan-out。
    # 快照 (a)/(b) 各自已合法累積的呼叫次數，(c) 之後不得再新增任何一筆。
    list_known_calls_before_c = len(list_known_calls)
    recall_related_calls_before_c = len(recall_related_calls)
    connect_foreign_calls: list[str] = []
    monkeypatch.setattr(
        db_mod,
        "connect_foreign_project_readonly",
        lambda pid: (connect_foreign_calls.append(pid) or None),
    )
    request_all = SearchRequest(query="decouplemarker", project_id=None, top_k=10)
    response_all = search_mod.search_memories(conn, request_all)
    assert len(response_all.results) == 1
    assert connect_foreign_calls == [], (
        "all_projects 語意單獨使用時絕不能觸發任何一種跨專案 fan-out"
    )
    assert len(list_known_calls) == list_known_calls_before_c
    assert len(recall_related_calls) == recall_related_calls_before_c
    conn.close()


# ---------------------------------------------------------------------------
# 8. 端到端外層接線：server.remagraph_search / CLI `search --include-related`
# ---------------------------------------------------------------------------


def test_server_remagraph_search_wires_include_related_end_to_end(tmp_path, monkeypatch):
    _make_project_with_memory(
        tmp_path,
        monkeypatch,
        "srv-related-a",
        mem_id="mem-srv-related-1",
        summary="distinctivemarker server wiring fixture in related project",
    )

    caller_state_dir = tmp_path / "srv-related-caller-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(caller_state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "srv-related-caller")
    server_mod._conn = None

    db_mod.declare_project_edge("srv-related-caller", "srv-related-a", "depends_on")

    result = server_mod.remagraph_search(
        query="distinctivemarker",
        project_id="srv-related-caller",
        include_related=True,
        related_hops=1,
    )

    ids_by_project = {r["source_project_id"]: r["id"] for r in result["results"]}
    assert ids_by_project == {"srv-related-a": "mem-srv-related-1"}
    assert result["cross_project_fanout_capped"] is False

    server_mod._conn = None


def test_cli_search_wires_include_related_end_to_end(tmp_path, monkeypatch, capsys):
    _make_project_with_memory(
        tmp_path,
        monkeypatch,
        "cli-related-a",
        mem_id="mem-cli-related-1",
        summary="distinctivemarker cli wiring fixture in related project",
    )

    caller_state_dir = tmp_path / "cli-related-caller-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(caller_state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "cli-related-caller")

    db_mod.declare_project_edge("cli-related-caller", "cli-related-a", "sibling")

    args = cli_mod.build_parser().parse_args(
        [
            "search",
            "--project",
            "cli-related-caller",
            "--query",
            "distinctivemarker",
            "--include-related",
            "--related-hops",
            "1",
        ]
    )
    cli_mod.cmd_search(args)

    import json

    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip())
    ids_by_project = {r["source_project_id"]: r["id"] for r in payload["results"]}
    assert ids_by_project == {"cli-related-a": "mem-cli-related-1"}


# ---------------------------------------------------------------------------
# 9. Fan-out 上限重用：include_related 也套用 _CROSS_PROJECT_FANOUT_CAP
# ---------------------------------------------------------------------------


def test_include_related_reports_fanout_cap_when_exceeded(tmp_path, monkeypatch):
    """驗證 include_related 確實重用 item 4b 既有的
    _CROSS_PROJECT_FANOUT_CAP 常數與 cross_project_fanout_capped 回報
    慣例（透過共用的 _cross_project_fanout helper），而不是各自維護一份
    獨立、可能漂移的上限邏輯。"""
    cap = search_mod._CROSS_PROJECT_FANOUT_CAP

    caller_state_dir = tmp_path / "fanout-caller-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(caller_state_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "fanout-caller")
    conn = db_mod.connect(project_id="fanout-caller")

    for i in range(cap + 3):
        pid = f"fanout-related-{i:02d}"
        _make_project_with_memory(
            tmp_path,
            monkeypatch,
            pid,
            mem_id=f"mem-fanout-{i:02d}",
            summary=f"distinctivemarker fanout cap fixture project number {i}",
        )
        db_mod.declare_project_edge("fanout-caller", pid, "sibling")

    request = SearchRequest(
        query="distinctivemarker",
        project_id="fanout-caller",
        include_related=True,
        related_hops=1,
        top_k=100,
    )
    response = search_mod.search_memories(conn, request)

    assert response.cross_project_fanout_capped is True, (
        "已宣告的關聯專案數超過 _CROSS_PROJECT_FANOUT_CAP 時，include_related "
        "必須跟 cross_project_label 一樣回報 capped=True"
    )
    assert len(response.results) == cap
    conn.close()


def test_cli_link_subcommand_declares_edge(tmp_path, monkeypatch, capsys):
    args = cli_mod.build_parser().parse_args(
        [
            "link",
            "--from",
            "cli-link-a",
            "--to",
            "cli-link-b",
            "--relation",
            "monorepo_member",
        ]
    )
    cli_mod.cmd_link(args)

    edges = db_mod.get_project_edges("cli-link-a")
    assert len(edges) == 1
    assert edges[0]["from_project"] == "cli-link-a"
    assert edges[0]["to_project"] == "cli-link-b"
    assert edges[0]["relation"] == "monorepo_member"
