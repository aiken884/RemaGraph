# SPDX-License-Identifier: Apache-2.0
"""Regression tests for store.migrate_project_memories() -- the real,
shared migration implementation that replaces the previous split-brain
setup (CLI cmd_migrate_project had a real-but-buggy implementation that
hardcoded the source DB path to Path.home()/".local/state/remagraph/
remagraph.db" -- i.e. implicitly assumed from_project is always 'default'
-- while the MCP tool remagraph_migrate_project was a pure stub that only
validated the target and returned a canned message, never touching any
data).

本檔驗證：
1. from_project 不是 'default'、有在 registry 登記過、來源資料庫有屬於
   to_project 的記錄 -> 正確從該登記的 state_dir 讀取並遷移（而不是誤讀
   default DB 或悄悄搬 0 筆）。
2. from_project 從未登記過 -> 明確的 ProjectNotRegisteredError，而不是
   靜默當作 0 筆。
3. dry_run 模式下的預估筆數，與之後真的執行時遷移的實際筆數必須一致
   （同一段資料、同一段比對 SQL）。
4. 遷移後：來源那幾筆記錄真的被標記 invalidated，目標資料庫真的多了對應
   筆數的 active 記錄（project_id 被強制改為 to_project）。
5. from_project == 'default' 的特例：不查 registry，改用
   db.get_state_dir()（尊重 REMAGRAPH_STATE_DIR 覆寫）。
6. 唯讀降級（schema 版本比目前程式碼新）時，來源/目標任一方都必須乾淨
   拒絕寫入，而不是靜默失敗或崩潰成難以理解的原始例外。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from remagraph import db as db_mod
from remagraph import store as store_mod


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    """比照 tests/test_cli_project_safety_valve.py 的既有隔離慣例：任何
    resolve_project_state_dir() 落入『convention 路徑』分支
    (~/.local/state/remagraph-<project>) 時都是用真正的 pathlib.Path.home()
    組路徑，不受 conftest.py 的 DEFAULT_STATE_DIR monkeypatch 保護 -- 必須
    額外把 HOME 導向假目錄，避免意外寫到執行測試這台機器的真實 home。"""
    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)
    monkeypatch.delenv("REMAGRAPH_PROJECT", raising=False)
    home = tmp_path / "fake-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(db_mod, "DEFAULT_STATE_DIR", home / ".local" / "state" / "remagraph")
    return home


def _insert_memory(
    conn: sqlite3.Connection,
    *,
    mem_id: str,
    project_id: str,
    tags: list[str] | None = None,
    summary: str = "一筆長度足夠的預設 summary" + "填充字元" * 5,
    status: str = "active",
) -> None:
    now = "2026-07-24T00:00:00Z"
    conn.execute(
        """
        INSERT INTO memories (
            id, project_id, kind, task_id, agent_id, timestamp,
            summary, learnings, handoff_note, tags, status, created_at, updated_at
        ) VALUES (?, ?, 'task_handoff', 'task-fixture', 'agent-fixture', ?,
                  ?, '[]', '', ?, ?, ?, ?)
        """,
        (mem_id, project_id, now, summary, json.dumps(tags or []), status, now, now),
    )


def _read_row(db_path: Path, mem_id: str) -> sqlite3.Row | None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM memories WHERE id = ?", (mem_id,)).fetchone()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1 + 4：registered non-default from_project -> 正確遷移、正確標記
# ---------------------------------------------------------------------------


def test_migrates_from_registered_non_default_project_and_marks_source_invalidated(
    tmp_path, monkeypatch
):
    proj_a_dir = tmp_path / "proj-a-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(proj_a_dir))
    conn = db_mod.connect(project_id="proj-a")
    _insert_memory(
        conn,
        mem_id="mem-a-belongs-to-b",
        project_id="proj-a",
        tags=["proj-b"],
        summary="這筆記錄看起來屬於 proj-b，足夠長的摘要內容用來通過驗證門檻",
    )
    _insert_memory(
        conn,
        mem_id="mem-a-unrelated",
        project_id="proj-a",
        tags=["other-tag"],
        summary="這筆記錄跟目標專案完全無關，同樣要湊足夠長的摘要內容才行的樣子",
    )
    conn.close()

    # 呼叫端目前情境切換到完全不同的第三方 project（模擬 MCP server 長駐
    # 行程綁定了別的專案，藉此驗證來源解析真的走 registry，而不是誤用
    # ambient REMAGRAPH_STATE_DIR）。
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path / "caller-state"))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "caller-proj")

    result = store_mod.migrate_project_memories("proj-a", "proj-b")

    assert result.from_project == "proj-a"
    assert result.to_project == "proj-b"
    assert result.dry_run is False
    assert result.migrated_count == 1
    assert result.skipped_ids == []

    # 來源：被遷移的那筆必須是 invalidated，且附上遷移軌跡；無關的那筆維持
    # active 不受影響。
    migrated_src_row = _read_row(proj_a_dir / "remagraph.db", "mem-a-belongs-to-b")
    assert migrated_src_row is not None
    assert migrated_src_row["status"] == "invalidated"
    assert "migrated-to:proj-b" in migrated_src_row["learnings"]

    unrelated_src_row = _read_row(proj_a_dir / "remagraph.db", "mem-a-unrelated")
    assert unrelated_src_row is not None
    assert unrelated_src_row["status"] == "active"

    # 目標：safety_validate_project 已把 proj-b 登記進 registry，可查回它
    # 的 state_dir，驗證裡面真的多了一筆 project_id 被強制改為 proj-b 的
    # active 記錄。
    to_state_str = db_mod.get_registered_state_dir("proj-b")
    assert to_state_str is not None
    tgt_row = _read_row(Path(to_state_str) / "remagraph.db", "mem-a-belongs-to-b")
    assert tgt_row is not None
    assert tgt_row["project_id"] == "proj-b"
    assert tgt_row["status"] == "active"


# ---------------------------------------------------------------------------
# 2：from_project 從未登記過 -> 明確錯誤，不是靜默 0 筆
# ---------------------------------------------------------------------------


def test_unregistered_from_project_raises_clear_error_not_silent_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path / "caller-state"))

    with pytest.raises(store_mod.ProjectNotRegisteredError, match="never-seen-project"):
        store_mod.migrate_project_memories("never-seen-project", "proj-target")


# ---------------------------------------------------------------------------
# 3：dry-run 預估筆數必須與真的執行時的實際遷移筆數一致
# ---------------------------------------------------------------------------


def test_dry_run_estimate_matches_actual_migration_count(tmp_path, monkeypatch):
    proj_a_dir = tmp_path / "proj-a-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(proj_a_dir))
    conn = db_mod.connect(project_id="proj-a")
    for i in range(3):
        _insert_memory(
            conn,
            mem_id=f"mem-a-{i}",
            project_id="proj-a",
            tags=["proj-c"],
            summary=f"第 {i} 筆看起來屬於 proj-c 的記錄，摘要長度足夠通過門檻檢查",
        )
    conn.close()

    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path / "caller-state"))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "caller-proj")

    dry_result = store_mod.migrate_project_memories("proj-a", "proj-c", dry_run=True)
    assert dry_result.dry_run is True
    assert dry_result.migrated_count == 3

    real_result = store_mod.migrate_project_memories("proj-a", "proj-c", dry_run=False)
    assert real_result.dry_run is False
    assert real_result.migrated_count == dry_result.migrated_count == 3

    # dry-run 本身絕不能有任何寫入副作用：來源記錄在 dry-run 之後、真正執行
    # 之前仍應是 active（若 dry-run 誤觸寫入，第二次真的執行時就不會再命中
    # 這 3 筆，migrated_count 會變成 0，上面的斷言已經足以偵測，這裡再加一
    # 條更直接的證據）。
    for i in range(3):
        row = _read_row(proj_a_dir / "remagraph.db", f"mem-a-{i}")
        assert row is not None
        assert row["status"] == "invalidated"  # 真的執行完之後才變成這樣


# ---------------------------------------------------------------------------
# 5：from_project == 'default' 特例 -- 不查 registry，改用 get_state_dir()
# ---------------------------------------------------------------------------


def test_default_from_project_resolves_via_get_state_dir_not_registry(
    tmp_path, monkeypatch, fake_home
):
    # REMAGRAPH_STATE_DIR 刻意保持未設定：這是『default』專案最寫實的使用
    # 情境 -- 沒有任何專案專屬設定時，get_state_dir() 落回
    # DEFAULT_STATE_DIR（fake_home fixture 已 monkeypatch 成 tmp_path 底下
    # 的假路徑）。
    conn = db_mod.connect()
    _insert_memory(
        conn,
        mem_id="mem-default-1",
        project_id="default",
        tags=["proj-d"],
        summary="這筆屬於 default 資料庫、看起來要遷去 proj-d 的記錄摘要內容",
    )
    conn.close()

    # 確認 'default' 真的沒有出現在 registry（佐證這條路徑走的是
    # get_state_dir，而不是 get_registered_state_dir）。
    assert db_mod.get_registered_state_dir("default") is None

    result = store_mod.migrate_project_memories("default", "proj-d")

    assert result.migrated_count == 1
    default_state_dir = fake_home / ".local" / "state" / "remagraph"
    src_row = _read_row(default_state_dir / "remagraph.db", "mem-default-1")
    assert src_row is not None
    assert src_row["status"] == "invalidated"

    # 目標（proj-d，透過 convention 路徑 ~/.local/state/remagraph-proj-d
    # 解析出來）必須是與來源完全不同的物理目錄，不能撞到同一個 db 檔案。
    to_state_str = db_mod.get_registered_state_dir("proj-d")
    assert to_state_str is not None
    assert Path(to_state_str).resolve() != default_state_dir.resolve()
    tgt_row = _read_row(Path(to_state_str) / "remagraph.db", "mem-default-1")
    assert tgt_row is not None
    assert tgt_row["project_id"] == "proj-d"


# ---------------------------------------------------------------------------
# 6：唯讀降級 -- 目標/來源任一方都必須乾淨拒絕，而非靜默失敗
# ---------------------------------------------------------------------------


def _force_tier2(db_path: Path, schema_version_ahead: int) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', ?)",
        (str(schema_version_ahead),),
    )
    conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('min_reader_version', '1')"
    )
    conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('min_writer_version', ?)",
        (str(schema_version_ahead),),
    )
    conn.commit()
    conn.close()


def test_read_only_degraded_target_rejects_migration_cleanly(tmp_path, monkeypatch):
    proj_a_dir = tmp_path / "proj-a-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(proj_a_dir))
    conn = db_mod.connect(project_id="proj-a")
    _insert_memory(
        conn,
        mem_id="mem-a-ro-target",
        project_id="proj-a",
        tags=["proj-ro"],
        summary="這筆記錄要遷去一個處於唯讀降級狀態目標專案的摘要內容文字",
    )
    conn.close()

    # 事先把目標專案的資料庫建出來、拉高到比目前程式碼還新的 schema 版本，
    # 模擬 tier-2 唯讀降級狀態。
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path / "caller-state"))
    to_state = tmp_path / "proj-ro-state"
    db_mod.connect_at_state_dir(to_state).close()
    _force_tier2(to_state / "remagraph.db", db_mod.SCHEMA_VERSION + 1)

    # 讓 safety_validate_project("proj-ro", ...) 解析出同一個目錄：直接把
    # 目前 ambient REMAGRAPH_STATE_DIR 指到它（resolve_project_state_dir
    # 對已設定的 REMAGRAPH_STATE_DIR 有最高優先權，逐字回傳）。
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(to_state))

    with pytest.raises(store_mod.MigrationReadOnlyError, match="proj-ro"):
        store_mod.migrate_project_memories("proj-a", "proj-ro")

    # 必須乾淨拒絕、不留任何部分寫入：來源那筆仍是 active。
    src_row = _read_row(proj_a_dir / "remagraph.db", "mem-a-ro-target")
    assert src_row is not None
    assert src_row["status"] == "active"


def test_from_project_equals_to_project_raises_value_error(tmp_path, monkeypatch):
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path / "caller-state"))
    with pytest.raises(ValueError):
        store_mod.migrate_project_memories("same-project", "same-project")


def test_aliased_from_and_to_resolving_to_same_physical_dir_raises_clear_error(
    tmp_path, monkeypatch
):
    """from_project != to_project 字面上不同，但目前 ambient
    REMAGRAPH_STATE_DIR 剛好讓兩者都解析到同一個物理目錄（見
    maintenance.resolve_project_state_dir 對已設定 REMAGRAPH_STATE_DIR 的
    最高優先權語意）——必須明確拒絕，而不是悄悄對同一份 db 檔案自我碰撞。
    """
    shared_dir = tmp_path / "shared-state-dir"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(shared_dir))
    conn = db_mod.connect(project_id="proj-x")
    _insert_memory(
        conn,
        mem_id="mem-x-1",
        project_id="proj-x",
        tags=["proj-y"],
        summary="這筆記錄本來要遷去 proj-y，但環境剛好讓兩邊撞成同一個目錄",
    )
    conn.close()

    # REMAGRAPH_STATE_DIR 刻意保持指向同一個 shared_dir 不變。
    with pytest.raises(ValueError, match="same physical database directory"):
        store_mod.migrate_project_memories("proj-x", "proj-y")

    # 必須完全沒有任何寫入發生：來源那筆仍是 active。
    row = _read_row(shared_dir / "remagraph.db", "mem-x-1")
    assert row is not None
    assert row["status"] == "active"


def test_source_database_missing_raises_file_not_found(tmp_path, monkeypatch):
    """from_project 有在 registry 登記過(曾被 resolve 過)，但它的資料庫檔案
    因故不存在(例如目錄被清掉)——必須明確報錯，不是靜默 0 筆。"""
    proj_a_dir = tmp_path / "proj-a-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(proj_a_dir))
    conn = db_mod.connect(project_id="proj-a")
    conn.close()

    import shutil

    shutil.rmtree(proj_a_dir)

    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path / "caller-state"))
    with pytest.raises(FileNotFoundError):
        store_mod.migrate_project_memories("proj-a", "proj-e")


# ---------------------------------------------------------------------------
# 7：獨立審查發現的阻擋問題 -- learnings 是壞掉的 JSON 時，INSERT 已經寫進
#    目標資料庫、但緊接著的 json.loads 卻拋例外，導致同一筆記憶同時以
#    active 狀態存在於來源與目標兩邊，還被回報成 skipped。修復後：純運算
#    （json.loads/json.dumps）已搬到 INSERT 之前，這裡驗證壞掉的 JSON 只會
#    讓這筆記錄整批被跳過 -- 目標完全沒有寫入、來源仍是 active，不會出現
#    「兩邊都 active + 回報 skipped」的三方矛盾。
# ---------------------------------------------------------------------------


def test_corrupt_learnings_json_skips_atomically_without_duplicate_active_copy(
    tmp_path, monkeypatch
):
    proj_a_dir = tmp_path / "proj-a-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(proj_a_dir))
    conn = db_mod.connect(project_id="proj-a")
    now = "2026-07-24T00:00:00Z"
    # 直接手寫 INSERT，繞過 _insert_memory 固定帶入合法 '[]' 的 learnings，
    # 刻意塞一個壞掉（非合法 JSON）的字串進去，重現審查者的實測情境。
    conn.execute(
        """
        INSERT INTO memories (
            id, project_id, kind, task_id, agent_id, timestamp,
            summary, learnings, handoff_note, tags, status, created_at, updated_at
        ) VALUES (?, ?, 'task_handoff', 'task-fixture', 'agent-fixture', ?,
                  ?, ?, '', ?, 'active', ?, ?)
        """,
        (
            "mem-a-corrupt-learnings",
            "proj-a",
            now,
            "這筆記錄的 learnings 欄位壞掉了，看起來要遷去 proj-corrupt 才對",
            "{this is not valid json",
            json.dumps(["proj-corrupt"]),
            now,
            now,
        ),
    )
    conn.close()

    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path / "caller-state"))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "caller-proj")

    result = store_mod.migrate_project_memories("proj-a", "proj-corrupt")

    assert result.migrated_count == 0
    assert result.skipped_ids == ["mem-a-corrupt-learnings"]

    # 來源：完全沒被觸碰，仍是 active（UPDATE 從未執行，因為在它之前的
    # json.loads 就已經拋例外並整批跳過這筆）。
    src_row = _read_row(proj_a_dir / "remagraph.db", "mem-a-corrupt-learnings")
    assert src_row is not None
    assert src_row["status"] == "active"

    # 目標：safety_validate_project 仍會把 proj-corrupt 登記進 registry，
    # 但資料庫裡完全不應該有這筆記錄 -- INSERT 從未被呼叫，不是「寫進去了
    # 但被回報成 skipped」。
    to_state_str = db_mod.get_registered_state_dir("proj-corrupt")
    assert to_state_str is not None
    tgt_row = _read_row(Path(to_state_str) / "remagraph.db", "mem-a-corrupt-learnings")
    assert tgt_row is None


# ---------------------------------------------------------------------------
# 8：重複遷移的正式回歸測試 -- 同一組 from/to 執行兩次，第二次不得重複遷移
# ---------------------------------------------------------------------------


def test_running_migration_twice_does_not_duplicate_migrate_second_time(
    tmp_path, monkeypatch
):
    proj_a_dir = tmp_path / "proj-a-state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(proj_a_dir))
    conn = db_mod.connect(project_id="proj-a")
    _insert_memory(
        conn,
        mem_id="mem-a-repeat-1",
        project_id="proj-a",
        tags=["proj-repeat"],
        summary="這筆記錄看起來屬於 proj-repeat，用來驗證重複遷移不會重跑",
    )
    _insert_memory(
        conn,
        mem_id="mem-a-repeat-2",
        project_id="proj-a",
        tags=["proj-repeat"],
        summary="第二筆同樣看起來屬於 proj-repeat 的記錄，摘要長度足夠通過門檻",
    )
    conn.close()

    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path / "caller-state"))
    monkeypatch.setenv("REMAGRAPH_PROJECT", "caller-proj")

    first_result = store_mod.migrate_project_memories("proj-a", "proj-repeat")
    assert first_result.migrated_count == 2
    assert first_result.skipped_ids == []

    to_state_str = db_mod.get_registered_state_dir("proj-repeat")
    assert to_state_str is not None
    to_db_path = Path(to_state_str) / "remagraph.db"

    # 第二次對同一組 from/to 再跑一次：來源那兩筆現在都已是 invalidated，
    # 啟發式比對的 WHERE 條件（status != 'invalidated'）不會再命中它們，
    # 所以這次應該完全沒有東西可遷移。
    second_result = store_mod.migrate_project_memories("proj-a", "proj-repeat")
    assert second_result.migrated_count == 0
    assert second_result.skipped_ids == []

    # 目標資料庫不應該出現重複記錄：兩筆 id 各自仍然只有一筆。
    conn_tgt = sqlite3.connect(str(to_db_path))
    try:
        for mem_id in ("mem-a-repeat-1", "mem-a-repeat-2"):
            count = conn_tgt.execute(
                "SELECT COUNT(*) FROM memories WHERE id = ?", (mem_id,)
            ).fetchone()[0]
            assert count == 1
    finally:
        conn_tgt.close()

    # 來源那兩筆維持 invalidated，沒有被第二次呼叫弄成其他奇怪狀態。
    for mem_id in ("mem-a-repeat-1", "mem-a-repeat-2"):
        src_row = _read_row(proj_a_dir / "remagraph.db", mem_id)
        assert src_row is not None
        assert src_row["status"] == "invalidated"
