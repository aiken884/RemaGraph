# SPDX-License-Identifier: Apache-2.0
"""TDD regression tests -- run_maintenance()（force=False 正常路徑）的四個
決策 stub（_should_checkpoint / _should_prune / _should_optimize_fts /
_get_db_size_mb）過去永遠回傳 falsy 值，導致 WAL checkpoint、prune、FTS
optimize、依大小 VACUUM 這四項維護操作在 force=False 的正常呼叫下從未真正
執行過（只有 ANALYZE 與 integrity check 真的會跑）。

本檔針對每個函式各自驗證其行為，並補一個端到端測試證明 run_maintenance
(force=False) 在門檻被跨過時真的會觸發對應操作。
"""

from __future__ import annotations

import sqlite3

import pytest

from remagraph import db as db_mod
from remagraph import maintenance as maint_mod


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)
    monkeypatch.delenv("REMAGRAPH_PROJECT", raising=False)
    yield


def _insert_memory(
    conn: sqlite3.Connection, *, id_suffix: str, project_id: str = "testproj"
) -> None:
    """直接以 raw SQL 寫入一筆合法 memories row（略過 model2vec 編碼與仲裁
    流程，只為了讓 memories/memories_fts 表有實際內容可供 stub 函式查詢）。
    """
    conn.execute(
        """
        INSERT INTO memories (
            id, project_id, kind, task_id, agent_id, timestamp,
            summary, learnings, handoff_note, tags, status,
            created_at, updated_at
        ) VALUES (?, ?, 'status_update', 'task-1', 'agent-1', '2026-07-29T00:00:00Z',
                   ?, '[]', '', '[]', 'active',
                   '2026-07-29T00:00:00Z', '2026-07-29T00:00:00Z')
        """,
        (f"mem-{id_suffix}", project_id, f"summary text for {id_suffix} padded to be long enough"),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# _get_db_size_mb
# ---------------------------------------------------------------------------


def test_get_db_size_mb_returns_zero_when_no_files_exist(tmp_path):
    state_dir = tmp_path / "nonexistent"
    assert maint_mod._get_db_size_mb(state_dir) == 0.0


def test_get_db_size_mb_counts_main_db_file(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    db_path = db_mod.get_db_path(state_dir=state_dir)
    db_path.write_bytes(b"x" * (2 * 1024 * 1024))  # 2 MB

    size_mb = maint_mod._get_db_size_mb(state_dir)
    assert size_mb == pytest.approx(2.0, rel=1e-6)


def test_get_db_size_mb_includes_wal_and_shm_sidecar_files(tmp_path):
    """VACUUM 是否值得執行，關心的是資料庫目前實際佔用的磁碟空間 --
    WAL 模式下尚未 checkpoint 回主檔的資料停留在 -wal 檔，只看主檔會低估
    真實佔用，因此主檔 + -wal + -shm 都要計入。"""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    db_path = db_mod.get_db_path(state_dir=state_dir)
    db_path.write_bytes(b"x" * (1 * 1024 * 1024))  # 1 MB
    db_path.with_name(db_path.name + "-wal").write_bytes(b"y" * (1 * 1024 * 1024))  # 1 MB
    db_path.with_name(db_path.name + "-shm").write_bytes(b"z" * (512 * 1024))  # 0.5 MB

    size_mb = maint_mod._get_db_size_mb(state_dir)
    assert size_mb == pytest.approx(2.5, rel=1e-6)


# ---------------------------------------------------------------------------
# _should_checkpoint
# ---------------------------------------------------------------------------


def test_should_checkpoint_false_for_fresh_connection_with_no_writes(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    conn = db_mod.connect(project_id="testproj", skip_maintenance=True)
    try:
        policy = maint_mod.MaintenancePolicy()
        assert maint_mod._should_checkpoint(conn, policy) is False
    finally:
        conn.close()


def test_should_checkpoint_true_once_wal_grows_past_low_threshold(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    conn = db_mod.connect(project_id="testproj", skip_maintenance=True)
    try:
        _insert_memory(conn, id_suffix="1")
        policy = maint_mod.MaintenancePolicy(wal_checkpoint_interval_ops=1)
        assert maint_mod._should_checkpoint(conn, policy) is True
    finally:
        conn.close()


def test_should_checkpoint_false_when_threshold_far_from_reached(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    conn = db_mod.connect(project_id="testproj", skip_maintenance=True)
    try:
        _insert_memory(conn, id_suffix="1")
        policy = maint_mod.MaintenancePolicy(wal_checkpoint_interval_ops=10_000_000)
        assert maint_mod._should_checkpoint(conn, policy) is False
    finally:
        conn.close()


def test_should_checkpoint_resets_after_truncate_checkpoint(tmp_path, monkeypatch):
    """TRUNCATE checkpoint 會把 -wal 檔截斷為 0 -- 驗證這個訊號確實會
    在 checkpoint 後重置（近似『距離上次 checkpoint 累積量』的 interval
    語意，而非一次性門檻）。"""
    state_dir = tmp_path / "state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    conn = db_mod.connect(project_id="testproj", skip_maintenance=True)
    try:
        _insert_memory(conn, id_suffix="1")
        policy = maint_mod.MaintenancePolicy(wal_checkpoint_interval_ops=1)
        assert maint_mod._should_checkpoint(conn, policy) is True

        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        assert maint_mod._should_checkpoint(conn, policy) is False
    finally:
        conn.close()


def test_should_checkpoint_false_for_memory_only_database():
    """:memory: 資料庫沒有實體檔案，PRAGMA database_list 的 file 欄位為
    空字串 -- 沒有 WAL 檔可查時必須安全回傳 False，而非拋出例外。"""
    conn = sqlite3.connect(":memory:")
    try:
        policy = maint_mod.MaintenancePolicy()
        assert maint_mod._should_checkpoint(conn, policy) is False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# _should_prune
# ---------------------------------------------------------------------------


def test_should_prune_always_true():
    """設計取捨：_prune_superseded 本身已有 created_at 年齡過濾
    （只刪除真的夠舊的資料）+ prune_superseded_max_per_task 限制影響範圍，
    因此不需要額外的時間頻率門檻 -- 每次呼叫都嘗試 prune 是安全的簡化。"""
    assert maint_mod._should_prune() is True


# ---------------------------------------------------------------------------
# _prune_superseded -- LIMIT 節流（問題 2 修復）
# ---------------------------------------------------------------------------


def _insert_superseded_memory(
    conn: sqlite3.Connection, *, id_suffix: str, project_id: str, created_at: str
) -> None:
    """插入一筆足夠舊（created_at 早於 prune_superseded_age_days 門檻）且
    status='superseded' 的 memories row，供 _prune_superseded 測試使用。"""
    conn.execute(
        """
        INSERT INTO memories (
            id, project_id, kind, task_id, agent_id, timestamp,
            summary, learnings, handoff_note, tags, status,
            created_at, updated_at
        ) VALUES (?, ?, 'status_update', 'task-1', 'agent-1', ?,
                   ?, '[]', '', '[]', 'superseded',
                   ?, ?)
        """,
        (
            f"mem-old-{id_suffix}",
            project_id,
            created_at,
            f"old summary {id_suffix} padded to be long enough for storage",
            created_at,
            created_at,
        ),
    )


def test_prune_superseded_respects_max_per_task_limit(tmp_path, monkeypatch):
    """回歸測試（問題 2 修復）：過去 SQL 完全沒有 LIMIT，
    prune_superseded_max_per_task 這個欄位從未真正限制過任何查詢 -- 插入
    超過上限筆數的可清除資料，驗證 _prune_superseded 單次呼叫刪除的筆數
    不會超過 policy.prune_superseded_max_per_task。"""
    project_id = "testproj"
    state_dir = tmp_path / "state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    conn = db_mod.connect(project_id=project_id, skip_maintenance=True)
    try:
        old_ts = "2000-01-01T00:00:00Z"
        for i in range(8):
            _insert_superseded_memory(
                conn, id_suffix=str(i), project_id=project_id, created_at=old_ts
            )
        conn.commit()

        policy = maint_mod.MaintenancePolicy(prune_superseded_max_per_task=3)
        deleted = maint_mod._prune_superseded(conn, policy, project_id)
        assert deleted == 3

        remaining = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE project_id = ? AND status = 'superseded'",
            (project_id,),
        ).fetchone()[0]
        assert remaining == 8 - 3
    finally:
        conn.close()


def test_prune_superseded_deletes_all_when_below_limit(tmp_path, monkeypatch):
    """回歸保護：筆數本來就在上限之下時，LIMIT 不應該少刪任何一筆。"""
    project_id = "testproj"
    state_dir = tmp_path / "state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    conn = db_mod.connect(project_id=project_id, skip_maintenance=True)
    try:
        old_ts = "2000-01-01T00:00:00Z"
        for i in range(2):
            _insert_superseded_memory(
                conn, id_suffix=str(i), project_id=project_id, created_at=old_ts
            )
        conn.commit()

        policy = maint_mod.MaintenancePolicy(prune_superseded_max_per_task=5)
        deleted = maint_mod._prune_superseded(conn, policy, project_id)
        assert deleted == 2
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# _should_optimize_fts
# ---------------------------------------------------------------------------


def test_should_optimize_fts_false_below_interval_threshold(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    conn = db_mod.connect(project_id="testproj", skip_maintenance=True)
    try:
        _insert_memory(conn, id_suffix="1")
        _insert_memory(conn, id_suffix="2")
        policy = maint_mod.MaintenancePolicy(fts_optimize_interval=5)
        assert maint_mod._should_optimize_fts(conn, policy) is False
    finally:
        conn.close()


def test_should_optimize_fts_true_once_row_count_reaches_interval(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))
    conn = db_mod.connect(project_id="testproj", skip_maintenance=True)
    try:
        for i in range(5):
            _insert_memory(conn, id_suffix=str(i))
        policy = maint_mod.MaintenancePolicy(fts_optimize_interval=5)
        assert maint_mod._should_optimize_fts(conn, policy) is True
    finally:
        conn.close()


def test_should_optimize_fts_true_on_query_failure_is_safe_default(tmp_path):
    """memories 表不存在（例如尚未初始化 schema 的連線）時查詢會失敗 --
    optimize 操作本身冪等安全，查詢失敗時保守選擇「執行」而非「略過」。"""
    conn = sqlite3.connect(":memory:")
    try:
        policy = maint_mod.MaintenancePolicy()
        assert maint_mod._should_optimize_fts(conn, policy) is True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# _should_vacuum / _read_last_vacuum_size_mb / _record_vacuum_size_mb
# （VACUUM 節流機制 -- 問題 1 修復）
# ---------------------------------------------------------------------------


def test_read_last_vacuum_size_mb_none_when_meta_table_missing():
    """_meta 表尚不存在（例如尚未初始化 schema 的連線）時必須安全回傳
    None，而非拋出例外。"""
    conn = sqlite3.connect(":memory:")
    try:
        assert maint_mod._read_last_vacuum_size_mb(conn) is None
    finally:
        conn.close()


def test_record_and_read_last_vacuum_size_mb_roundtrip():
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        assert maint_mod._read_last_vacuum_size_mb(conn) is None

        maint_mod._record_vacuum_size_mb(conn, 42.5)
        assert maint_mod._read_last_vacuum_size_mb(conn) == pytest.approx(42.5)

        # INSERT OR REPLACE：再次記錄要覆蓋舊值，而不是累加新 row。
        maint_mod._record_vacuum_size_mb(conn, 100.0)
        assert maint_mod._read_last_vacuum_size_mb(conn) == pytest.approx(100.0)
    finally:
        conn.close()


def test_should_vacuum_false_at_or_below_static_threshold():
    conn = sqlite3.connect(":memory:")
    try:
        policy = maint_mod.MaintenancePolicy(vacuum_threshold_mb=50)
        assert maint_mod._should_vacuum(conn, policy, 50.0) is False
        assert maint_mod._should_vacuum(conn, policy, 10.0) is False
    finally:
        conn.close()


def test_should_vacuum_true_above_threshold_with_no_recorded_baseline():
    """從未記錄過基準值（例如從來沒做過 VACUUM）時，視為成長無限大，一律
    允許執行 -- 與修復前『完全沒有節流機制』時的行為一致，確保不影響第一
    次 VACUUM 的既有行為。"""
    conn = sqlite3.connect(":memory:")
    try:
        policy = maint_mod.MaintenancePolicy(vacuum_threshold_mb=50)
        assert maint_mod._should_vacuum(conn, policy, 100.0) is True
    finally:
        conn.close()


def test_should_vacuum_false_when_growth_below_ratio_threshold():
    """核心節流案例：活資料本身就超過門檻，VACUUM 後大小幾乎不變 --
    只成長 10%（< 20% 門檻）不該再觸發下一次 VACUUM。"""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        maint_mod._record_vacuum_size_mb(conn, 100.0)

        policy = maint_mod.MaintenancePolicy(vacuum_threshold_mb=50)
        assert maint_mod._should_vacuum(conn, policy, 110.0) is False
    finally:
        conn.close()


def test_should_vacuum_true_when_growth_meets_ratio_threshold():
    """成長達到（或超過）20% 門檻時，仍然允許再次 VACUUM -- 節流不能變成
    永久封印，資料庫真的長大過一輪還是要能被瘦身。"""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        maint_mod._record_vacuum_size_mb(conn, 100.0)

        policy = maint_mod.MaintenancePolicy(vacuum_threshold_mb=50)
        assert maint_mod._should_vacuum(conn, policy, 120.0) is True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 端到端：run_maintenance(force=False) 在門檻被跨過時真的觸發對應操作
# ---------------------------------------------------------------------------


def test_run_maintenance_normal_path_actually_triggers_checkpoint_and_fts_when_thresholds_crossed(
    tmp_path, monkeypatch
):
    project_id = "testproj"
    state_dir = tmp_path / "state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    # 保持第一條連線開著，模擬「應用程式自己活著的主連線」——若在呼叫
    # run_maintenance() 之前就 close()，SQLite 會在最後一條連線關閉時自動
    # 執行 WAL auto-checkpoint，導致 -wal 檔在維護真正開始檢查前就被清空，
    # 這與正式路徑（light_maintenance_on_connect 在主連線仍存活時，另外
    # 開一條獨立內部連線做維護）不符。run_maintenance(conn=None) 內部會
    # 自行以 _raw_connect 開第二條連線，正是要驗證的目標路徑。
    conn = db_mod.connect(project_id=project_id, skip_maintenance=True)
    try:
        _insert_memory(conn, id_suffix="1", project_id=project_id)
        _insert_memory(conn, id_suffix="2", project_id=project_id)

        policy = maint_mod.MaintenancePolicy(
            wal_checkpoint_interval_ops=1,
            fts_optimize_interval=2,
            vacuum_threshold_mb=0,  # 任何大小都觸發 vacuum，驗證 _get_db_size_mb 不再恆為 0
        )
        stats = maint_mod.run_maintenance(policy, project_id, force=False)
    finally:
        conn.close()

    assert "skipped" not in stats
    assert stats.get("wal_checkpoint") == "done"
    assert stats.get("fts_optimized") is True
    assert stats.get("vacuum") == "done"
    assert stats.get("size_before_mb", 0) > 0
    # _should_prune() 永遠 True，prune 分支必然執行過（即使刪除筆數為 0，
    # 因為兩筆資料都是 status='active' 且未過期）。
    assert stats.get("pruned_count") == 0
    assert stats["integrity"] == "ok"


def test_run_maintenance_normal_path_skips_checkpoint_and_fts_below_thresholds(
    tmp_path, monkeypatch
):
    """回歸保護：門檻未達成時，force=False 依然不應觸發 checkpoint/fts
    optimize/vacuum -- 不能因為本次修復而變成『永遠全部觸發』。"""
    project_id = "testproj"
    state_dir = tmp_path / "state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    conn = db_mod.connect(project_id=project_id, skip_maintenance=True)
    _insert_memory(conn, id_suffix="1", project_id=project_id)
    conn.close()

    policy = maint_mod.MaintenancePolicy(
        wal_checkpoint_interval_ops=10_000_000,
        fts_optimize_interval=10_000,
        vacuum_threshold_mb=10_000,
    )
    stats = maint_mod.run_maintenance(policy, project_id, force=False)

    assert "skipped" not in stats
    assert "wal_checkpoint" not in stats
    assert "fts_optimized" not in stats
    assert "vacuum" not in stats
    assert stats["integrity"] == "ok"


# ---------------------------------------------------------------------------
# 端到端：run_maintenance -- VACUUM 節流（問題 1 修復的核心回歸測試）
# ---------------------------------------------------------------------------


def test_run_maintenance_does_not_vacuum_twice_in_a_row_when_size_persistently_over_threshold(
    tmp_path, monkeypatch
):
    """問題 1 的核心回歸測試：模擬「一個 project 的活資料本身就超過
    vacuum_threshold_mb」的情境 -- size_mb 持續超過門檻、且兩次呼叫之間
    完全沒有成長。修復前沒有節流機制時，_get_db_size_mb 每次都 > 門檻，
    第二次呼叫 run_maintenance 一樣會再跑一次全庫 VACUUM，變成每次
    db.connect() 都要付出的永久性效能稅。

    透過 monkeypatch 固定 _get_db_size_mb 的回傳值，隔離掉真實檔案大小的
    時間/環境差異，只驗證節流邏輯本身：連續兩次呼叫 run_maintenance
    （force=False），第二次不應該再觸發 VACUUM。
    """
    project_id = "testproj"
    state_dir = tmp_path / "state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    conn = db_mod.connect(project_id=project_id, skip_maintenance=True)
    _insert_memory(conn, id_suffix="1", project_id=project_id)
    conn.close()

    # 固定回傳一個持續超過門檻、且不隨呼叫次數變化的 size_mb。
    monkeypatch.setattr(maint_mod, "_get_db_size_mb", lambda state_dir: 100.0)

    policy = maint_mod.MaintenancePolicy(vacuum_threshold_mb=50)

    stats1 = maint_mod.run_maintenance(policy, project_id, force=False)
    assert stats1.get("vacuum") == "done"

    stats2 = maint_mod.run_maintenance(policy, project_id, force=False)
    assert "vacuum" not in stats2


def test_run_maintenance_vacuums_again_after_significant_growth(tmp_path, monkeypatch):
    """節流不能變成永久封印：資料庫在兩次呼叫之間真的顯著成長（>= 20%）
    時，第二次呼叫仍然應該觸發 VACUUM。"""
    project_id = "testproj"
    state_dir = tmp_path / "state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    conn = db_mod.connect(project_id=project_id, skip_maintenance=True)
    _insert_memory(conn, id_suffix="1", project_id=project_id)
    conn.close()

    fake_size = {"current_mb": 100.0}
    monkeypatch.setattr(
        maint_mod, "_get_db_size_mb", lambda state_dir: fake_size["current_mb"]
    )

    policy = maint_mod.MaintenancePolicy(vacuum_threshold_mb=50)

    stats1 = maint_mod.run_maintenance(policy, project_id, force=False)
    assert stats1.get("vacuum") == "done"

    fake_size["current_mb"] = 130.0  # 相對上次基準值成長 30%，超過 20% 門檻
    stats2 = maint_mod.run_maintenance(policy, project_id, force=False)
    assert stats2.get("vacuum") == "done"
