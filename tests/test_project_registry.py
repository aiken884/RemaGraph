# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the cross-project registry (PPLX 架構改善計畫 item 4a).

背景：目前每個 project_id 各自對應完全獨立的 state_dir / DB 檔案（見
maintenance.resolve_project_state_dir），彼此互不知道對方存在 —— 每個專案的
資料庫是一座孤島。後續項目（4b 跨專案標籤搜尋、5 recall_related）都需要一個
輕量、共用的「登記簿」，記錄哪些 project_id 存在、各自的 state_dir 在哪裡。
本檔驗證的就是這個登記簿本身（db.register_known_project /
db.list_known_projects / db.connect_foreign_project_readonly），以及
maintenance.resolve_project_state_dir 呼叫時的自動登記副作用。

CRITICAL 測試隔離：registry 永遠落在 db.DEFAULT_STATE_DIR（這是它存在的
理由 —— 唯一不需要任何專案專屬設定就能解析出來的共用位置），因此單靠
monkeypatch REMAGRAPH_STATE_DIR 環境變數不足以隔離測試 —— 必須連
db.DEFAULT_STATE_DIR 這個模組常數本身都 monkeypatch 成 tmp_path 底下的路徑，
絕不能讓任何一個測試寫到這台機器真正的 ~/.local/state/remagraph*。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from remagraph import db as db_mod
from remagraph import maintenance as maint_mod


@pytest.fixture(autouse=True)
def isolated_default_state_dir(tmp_path, monkeypatch):
    """每個測試都套用的嚴格隔離：

    - 清掉可能從執行 shell 洩漏進來的 REMAGRAPH_STATE_DIR / REMAGRAPH_PROJECT。
    - 把 db.DEFAULT_STATE_DIR（registry 唯一的落地位置）monkeypatch 成
      tmp_path 底下、每個測試各自獨立的假路徑 —— 這是唯一能讓 registry
      讀寫完全不觸碰真實 ~/.local/state/remagraph 的方法，因為 registry
      的設計就是「刻意不受 REMAGRAPH_STATE_DIR 環境變數影響」。
    """
    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)
    monkeypatch.delenv("REMAGRAPH_PROJECT", raising=False)
    fake_default = tmp_path / "fake-default-state"
    monkeypatch.setattr(db_mod, "DEFAULT_STATE_DIR", fake_default)
    return fake_default


def _insert_fixture_memory(conn, *, mem_id: str, project_id: str, summary: str) -> None:
    now = "2026-07-24T00:00:00Z"
    conn.execute(
        """
        INSERT INTO memories (
            id, project_id, kind, task_id, agent_id, timestamp,
            summary, learnings, handoff_note, tags, status, created_at, updated_at
        ) VALUES (?, ?, 'task_handoff', 'task-fixture', 'agent-fixture', ?,
                  ?, '[]', '', '[]', 'active', ?, ?)
        """,
        (mem_id, project_id, now, summary, now, now),
    )


# ---------------------------------------------------------------------------
# 1. resolve_project_state_dir 對兩個全新 project_id 的自動登記
# ---------------------------------------------------------------------------


def test_resolve_registers_two_new_projects_discoverable_via_list(tmp_path, monkeypatch):
    proj_a_dir = tmp_path / "proj-a-state"
    proj_b_dir = tmp_path / "proj-b-state"

    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(proj_a_dir))
    resolved_a = maint_mod.resolve_project_state_dir("proj-a")
    assert resolved_a == proj_a_dir.resolve()

    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(proj_b_dir))
    resolved_b = maint_mod.resolve_project_state_dir("proj-b")
    assert resolved_b == proj_b_dir.resolve()

    known = {p["project_id"]: p for p in db_mod.list_known_projects()}
    assert "proj-a" in known
    assert "proj-b" in known
    assert known["proj-a"]["state_dir"] == str(proj_a_dir.resolve())
    assert known["proj-b"]["state_dir"] == str(proj_b_dir.resolve())
    assert known["proj-a"]["first_seen"]
    assert known["proj-a"]["last_seen"]


# ---------------------------------------------------------------------------
# 2. 對已登記的 project_id 再次呼叫 -> 更新 last_seen、不重複列、不遺失 first_seen
# ---------------------------------------------------------------------------


def test_resolve_again_updates_last_seen_without_duplicate_or_losing_first_seen(
    tmp_path, monkeypatch
):
    proj_dir = tmp_path / "proj-repeat-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(proj_dir))

    monkeypatch.setattr(db_mod, "_utcnow_iso", lambda: "2026-01-01T00:00:00Z")
    maint_mod.resolve_project_state_dir("proj-repeat")

    monkeypatch.setattr(db_mod, "_utcnow_iso", lambda: "2026-01-01T00:05:00Z")
    maint_mod.resolve_project_state_dir("proj-repeat")

    rows = [p for p in db_mod.list_known_projects() if p["project_id"] == "proj-repeat"]
    assert len(rows) == 1, "re-resolving an already-known project must not duplicate its row"
    assert rows[0]["first_seen"] == "2026-01-01T00:00:00Z"
    assert rows[0]["last_seen"] == "2026-01-01T00:05:00Z"


# ---------------------------------------------------------------------------
# 3. connect_foreign_project_readonly 能讀到「另一個」已知專案透過一般連線
#    寫入的資料，且不觸發該外部專案自己的 light_maintenance_on_connect /
#    safety_validate_project。
# ---------------------------------------------------------------------------


def test_connect_foreign_project_readonly_reads_data_without_foreign_side_effects(
    tmp_path, monkeypatch
):
    foreign_dir = tmp_path / "foreign-proj-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(foreign_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "foreign-proj")

    # 一般連線寫入真實資料 —— 這一步本身也會透過
    # light_maintenance_on_connect -> run_maintenance -> safety_validate_project
    # -> resolve_project_state_dir 自然而然把 foreign-proj 登記進 registry
    # （驗證「自動登記」這個副作用不需要任何額外的顯式呼叫）。
    conn = db_mod.connect(project_id="foreign-proj")
    _insert_fixture_memory(
        conn, mem_id="mem-foreign-1", project_id="foreign-proj", summary="foreign fixture summary"
    )
    conn.close()

    known = {p["project_id"] for p in db_mod.list_known_projects()}
    assert "foreign-proj" in known

    # 呼叫端切換到完全不同的另一個專案情境。
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path / "own-context-state"))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "own-context")

    light_maintenance_calls: list[str] = []
    safety_validate_calls: list[str] = []
    monkeypatch.setattr(
        maint_mod,
        "light_maintenance_on_connect",
        lambda project_id="default": light_maintenance_calls.append(project_id),
    )
    original_safety_validate = maint_mod.safety_validate_project

    def _spy_safety_validate(project_id, **kwargs):
        safety_validate_calls.append(project_id)
        return original_safety_validate(project_id, **kwargs)

    monkeypatch.setattr(maint_mod, "safety_validate_project", _spy_safety_validate)

    foreign_conn = db_mod.connect_foreign_project_readonly("foreign-proj")
    assert foreign_conn is not None
    try:
        row = foreign_conn.execute(
            "SELECT summary FROM memories WHERE id = 'mem-foreign-1'"
        ).fetchone()
        assert row is not None
        assert row["summary"] == "foreign fixture summary"

        # 真正唯讀：嘗試寫入必須被 SQLite 本身擋下。
        with pytest.raises(Exception):
            foreign_conn.execute(
                "INSERT INTO memories (id, project_id, kind, task_id, agent_id, timestamp, "
                "summary, learnings, handoff_note, tags, status, created_at, updated_at) "
                "VALUES ('should-fail', 'foreign-proj', 'task_handoff', 't', 'a', "
                "'2026-07-24T00:00:00Z', 'x', '[]', '', '[]', 'active', "
                "'2026-07-24T00:00:00Z', '2026-07-24T00:00:00Z')"
            )
    finally:
        foreign_conn.close()

    assert light_maintenance_calls == [], (
        "connect_foreign_project_readonly must never trigger the foreign project's own "
        "light_maintenance_on_connect"
    )
    assert safety_validate_calls == [], (
        "connect_foreign_project_readonly must never go through safety_validate_project "
        "enforcement for the foreign project"
    )


# ---------------------------------------------------------------------------
# 4. 防禦性回傳 None：未知 project_id、以及 registry 已過期（目錄已被刪除）
# ---------------------------------------------------------------------------


def test_connect_foreign_project_readonly_returns_none_for_unknown_project(tmp_path, monkeypatch):
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path / "some-state"))
    result = db_mod.connect_foreign_project_readonly("totally-unknown-project-xyz")
    assert result is None


def test_connect_foreign_project_readonly_returns_none_when_directory_deleted(
    tmp_path, monkeypatch
):
    gone_dir = tmp_path / "will-be-deleted-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(gone_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "ephemeral-proj")

    conn = db_mod.connect(project_id="ephemeral-proj")
    conn.close()

    known = {p["project_id"] for p in db_mod.list_known_projects()}
    assert "ephemeral-proj" in known, "sanity check: project must be registered before deletion"

    shutil.rmtree(gone_dir)

    result = db_mod.connect_foreign_project_readonly("ephemeral-proj")
    assert result is None


# ---------------------------------------------------------------------------
# 6. TOCTOU 硬化：exists() 預檢查與實際 connect() 之間的競態窗口
#    （PPLX 架構改善計畫 item 4b Part 1，追蹤事項 #22）
# ---------------------------------------------------------------------------


def test_connect_foreign_project_readonly_closes_toctou_gap_via_ro_uri(tmp_path, monkeypatch):
    """回歸測試：TOCTOU 縫隙必須被 connect() 本身（mode=ro URI）杜絕，而不是
    只靠 exists() 預檢查恰好夠即時。

    背景（獨立對抗式審查發現）：舊實作是
    `if foreign_db_path.exists(): sqlite3.connect(str(foreign_db_path))`。
    若檔案在 exists() 檢查『之後』、connect() 呼叫『之前』被刪除（例如另一
    行程剛好清掉了該 project 的 state_dir），plain sqlite3.connect() 並不會
    拋出例外——它會悄悄在該路徑建立一個全新的空白資料庫檔案，讓呼叫端拿到
    一個「看起來正常、實則完全空白」的連線，直接違反本函式的既有承諾（絕不
    憑空生出一個新的空資料庫，見上方
    test_connect_foreign_project_readonly_returns_none_when_directory_deleted）。

    真正修復：改用 `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`——
    mode=ro 讓 SQLite 在檔案不存在時於 connect() 呼叫當下就直接拋出
    sqlite3.OperationalError（因為 mode=ro 明確禁止建立新檔案），而不是像
    預設模式一樣「先成功打開／建立，之後才可能出錯」。

    本測試刻意讓 exists() 預檢查本身「說謊」（在檔案真的已被刪除後，仍對
    這一個特定路徑回報 True），藉此排除「只是因為 exists() 檢查剛好夠即時、
    從未真的暴露競態」這種假通過的可能性——唯有當真正的安全機制是 connect()
    本身（而非可能受競態影響的 exists() 預檢查）時，本測試才會通過。若實作
    退回成單純移除 exists() 檢查、但底層仍用一般（非 mode=ro）的
    sqlite3.connect()，本測試一樣會抓到（因為一般模式一樣會悄悄建立空檔案）。
    """
    proj_dir = tmp_path / "toctou-proj-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(proj_dir))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "toctou-proj")

    conn = db_mod.connect(project_id="toctou-proj")
    conn.close()

    db_path = proj_dir.resolve() / db_mod.DB_FILENAME
    assert db_path.exists(), "sanity check: db 檔案須先真的存在"

    known = {p["project_id"] for p in db_mod.list_known_projects()}
    assert "toctou-proj" in known, "sanity check: project 須先被登記"

    # 模擬競態：真的刪除檔案，但讓 Path.exists() 對「這一個特定路徑」持續
    # 說謊回報 True —— 重現「exists() 檢查當下檔案還在，但 connect() 呼叫
    # 當下檔案已經消失」的競態窗口。
    original_exists = Path.exists
    db_path.unlink()
    assert not original_exists(db_path), "sanity check: 檔案須先真的被刪除"

    def _lying_exists(self: Path) -> bool:
        if self == db_path:
            return True
        return original_exists(self)

    monkeypatch.setattr(Path, "exists", _lying_exists, raising=True)

    result = db_mod.connect_foreign_project_readonly("toctou-proj")

    assert result is None, (
        "即使 exists() 預檢查說謊回報檔案仍存在，connect_foreign_project_readonly "
        "仍必須回傳 None —— 真正的安全機制必須是 connect() 本身（mode=ro），"
        "而不是可能受競態影響的 exists() 預檢查"
    )
    assert not original_exists(db_path), "絕不能憑空在該路徑生出一個新的空白資料庫檔案"


# ---------------------------------------------------------------------------
# 5. registry 寫入失敗必須是防禦性的：resolve_project_state_dir 仍須正常回傳
# ---------------------------------------------------------------------------


def test_registry_write_failure_does_not_break_resolve_project_state_dir(
    tmp_path, monkeypatch, isolated_default_state_dir
):
    # 讓 registry 真正的落地位置（db.DEFAULT_STATE_DIR）無法被當成目錄使用：
    # 該路徑上已經有一個同名的普通檔案，於是 mkdir(parents=True, exist_ok=True)
    # 會拋出例外 —— 模擬「registry 自己的資料庫寫入因故失敗」（例如唯讀檔案系統
    # 路徑或檔案被鎖定）。
    isolated_default_state_dir.write_text("blocking file, not a directory")

    proj_dir = tmp_path / "normal-proj-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(proj_dir))

    # 必須不拋出例外，且回傳值完全不受 registry 寫入失敗影響。
    resolved = maint_mod.resolve_project_state_dir("normal-proj")
    assert resolved == proj_dir.resolve()

    # 佐證：registry 確實真的寫入失敗了（而不是測試設置本身有誤導致假通過）。
    assert db_mod.list_known_projects() == []


def test_register_known_project_itself_never_raises_on_failure(
    tmp_path, isolated_default_state_dir
):
    isolated_default_state_dir.write_text("blocking file, not a directory")
    # 直接呼叫底層函式本身也必須是防禦性的，不僅僅是靠呼叫端的 try/except。
    db_mod.register_known_project("some-project", tmp_path / "some-project-state")


# ---------------------------------------------------------------------------
# 8. 端對端：3 個獨立假專案 + 從第 4 個「呼叫端情境」跨專案讀取其中一個
# ---------------------------------------------------------------------------


def test_end_to_end_three_fake_projects_and_cross_project_read(tmp_path, monkeypatch):
    project_names = ["alpha", "beta", "gamma"]
    expected_dirs: dict[str, str] = {}

    for name in project_names:
        state_dir = tmp_path / f"{name}-state"
        monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
        monkeypatch.setenv("REMAGRAPH_PROJECT", name)

        conn = db_mod.connect(project_id=name)
        _insert_fixture_memory(
            conn, mem_id=f"mem-{name}-1", project_id=name, summary=f"{name} fixture summary"
        )
        conn.close()
        expected_dirs[name] = str(state_dir.resolve())

    # 呼叫端目前情境切換成完全不同的第 4 個專案。
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path / "delta-caller-state"))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "delta-caller")

    known = {p["project_id"]: p for p in db_mod.list_known_projects()}
    assert set(known) == set(project_names)
    for name in project_names:
        assert known[name]["state_dir"] == expected_dirs[name]
        assert known[name]["first_seen"]
        assert known[name]["last_seen"]

    foreign_conn = db_mod.connect_foreign_project_readonly("beta")
    assert foreign_conn is not None
    try:
        row = foreign_conn.execute(
            "SELECT summary FROM memories WHERE id = 'mem-beta-1'"
        ).fetchone()
        assert row is not None
        assert row["summary"] == "beta fixture summary"
    finally:
        foreign_conn.close()
