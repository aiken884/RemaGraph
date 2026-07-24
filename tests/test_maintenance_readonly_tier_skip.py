# SPDX-License-Identifier: Apache-2.0
"""Regression tests -- maintenance must never run against a read-only
(tier-2 schema-compat) connection.

背景（獨立對抗式審查發現的缺口，追蹤事項 #18）：

db.connect() 在成功走完 _run_migrations 之後，只要提供了 project_id 且
skip_maintenance 不為 True，就會呼叫 maintenance.light_maintenance_on_connect
(project_id)（見 db.py connect() 尾端）。若該連線已被 _run_migrations 內的
_handle_newer_than_code_schema 判定為 tier 2（讀相容、寫不安全，見
db.READ_ONLY_ATTR），store.process_store() 已知道要檢查這個標記並乾淨拒絕
寫入 —— 但 light_maintenance_on_connect -> run_maintenance 開的是它自己
「另一條、獨立」的內部連線（經 maintenance.py 內的 _raw_connect，即
db.connect 的別名），從未檢查過這條內部連線是否也一樣被標記唯讀，就直接對
它執行 WAL checkpoint / prune 的 DELETE / VACUUM / ANALYZE 等一整組寫入
操作 —— 完全繞過了唯讀分級原本要提供的保護。

現況（修復前）唯一會真正執行的操作是 conn.execute("ANALYZE")（因為
_should_checkpoint / _should_prune / _get_db_size_mb 目前都還是未實作的
stub，永遠回傳 falsy 值），但 ANALYZE 本身就會建立/覆寫 sqlite_stat1 這張
系統表 —— 這是一個對「程式碼尚未完全理解其寫入安全性」的新 schema 資料庫
的真實寫入，正是唯讀分級要防止的事。之後 stub 被實作、真正的 checkpoint /
prune / vacuum 上線後，同樣的缺口會讓這些操作也一併悄悄對唯讀分級的資料庫
執行。

修復：run_maintenance()（無論是外部呼叫端傳入既有 conn，或如常態般自行以
_raw_connect 開一條全新的內部連線）現在會先檢查該 conn 本身的
db.READ_ONLY_ATTR 標記，若為 True，直接略過所有維護操作並提前返回，絕不
呼叫任何一個 conn.execute()。

注意（本檔範圍限制的說明）：本次修復刻意只改動 src/remagraph/maintenance.py
（db.py 目前由另一個 agent 併行修改中，不得觸碰）。因此 db.connect() 尾端
「if project_id and not skip_maintenance: light_maintenance_on_connect
(project_id)」這一行呼叫本身仍是無條件的 —— light_maintenance_on_connect
在 tier-2 情境下依然「會被呼叫」，但修復後它會立刻偵測到內部連線本身也
同樣被標記唯讀（因為兩者是對同一個資料庫檔案、以同一份程式碼跑同一套
_run_migrations 判斷，結果必然一致）並整個 no-op、不執行任何一條寫入 SQL。
下方測試因此驗證的是這個可達成、且已被直接證明的不變量 ——「零筆維護寫入
真的執行到資料庫檔案」——而非文字上「light_maintenance_on_connect 完全未
被呼叫」（那需要修改 db.py 呼叫點本身，超出本次授權範圍）。
"""

from __future__ import annotations

import sqlite3

import pytest

from remagraph import db
from remagraph import maintenance as maint_mod


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)
    monkeypatch.delenv("REMAGRAPH_PROJECT", raising=False)
    yield


def _set_meta(state_dir, updates: dict[str, str | None]) -> None:
    """直接以底層 sqlite3 連線覆寫 _meta 表欄位，模擬 tier-2 資料庫狀態。"""
    db_path = db.get_db_path(state_dir=state_dir)
    conn = sqlite3.connect(db_path)
    for key, value in updates.items():
        if value is None:
            conn.execute("DELETE FROM _meta WHERE key = ?", (key,))
        else:
            conn.execute(
                "INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)", (key, value)
            )
    conn.commit()
    conn.close()


def _sqlite_stat1_exists(state_dir) -> bool:
    """查詢 sqlite_stat1 系統表是否存在 —— ANALYZE 執行後會建立/填入此表，
    是判斷「ANALYZE 是否真的執行過」最直接、不受 WAL 緩衝影響的訊號（透過
    一般連線讀取，會正確看穿 WAL 尚未 checkpoint 回主檔案的內容）。
    """
    db_path = db.get_db_path(state_dir=state_dir)
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sqlite_stat1'"
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _drop_sqlite_stat1(state_dir) -> None:
    db_path = db.get_db_path(state_dir=state_dir)
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE IF EXISTS sqlite_stat1")
    conn.commit()
    conn.close()


def _make_tier2_db(tmp_path, monkeypatch, project_id: str = "testproj"):
    """建立一個一般（tier-1）資料庫，再把 _meta 改成 tier-2（讀相容、寫不
    安全）狀態，回傳 state_dir。"""
    state_dir = tmp_path / "state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    conn0 = db.connect(project_id=project_id)
    conn0.close()

    # 清除 step 1（tier-1 連線的一般 light maintenance）留下的 sqlite_stat1，
    # 讓後續斷言只反映 tier-2 嘗試本身是否又執行了一次 ANALYZE。
    _drop_sqlite_stat1(state_dir)

    _set_meta(
        state_dir,
        {
            "schema_version": str(db.SCHEMA_VERSION + 1),
            "min_reader_version": "1",
            "min_writer_version": str(db.SCHEMA_VERSION + 1),
        },
    )
    return state_dir


# ---------------------------------------------------------------------------
# 核心修復：run_maintenance() 對已標記唯讀的 conn 必須整個 no-op
# ---------------------------------------------------------------------------


def test_run_maintenance_skips_entirely_for_read_only_marked_connection(tmp_path, monkeypatch):
    """直接呼叫 run_maintenance()（conn=None，讓它自行開內部連線）：若該內部
    連線因 tier-2 schema 相容性被標記唯讀，必須整個略過所有維護操作 ——
    不得出現 wal_checkpoint / pruned_count / fts_optimized / vacuum /
    integrity 等任一鍵，且 sqlite_stat1 不得被建立（即 ANALYZE 從未執行）。
    """
    project_id = "testproj"
    state_dir = _make_tier2_db(tmp_path, monkeypatch, project_id)

    assert _sqlite_stat1_exists(state_dir) is False  # 確認乾淨基準

    policy = maint_mod.MaintenancePolicy()
    stats = maint_mod.run_maintenance(policy, project_id, force=False)

    assert stats.get("skipped") is True
    assert stats.get("skip_reason") == "read_only_schema_tier"
    for forbidden_key in (
        "wal_checkpoint",
        "pruned_count",
        "fts_optimized",
        "vacuum",
        "integrity",
    ):
        assert forbidden_key not in stats

    # 最直接的證據：資料庫檔案完全沒有被寫入過 —— ANALYZE 沒有執行。
    assert _sqlite_stat1_exists(state_dir) is False


def test_run_maintenance_skips_even_with_force_true(tmp_path, monkeypatch):
    """force=True 代表『呼叫端明確要求執行維護』，但唯讀分級要防範的是
    schema 相容性風險，與呼叫端意願正交 —— force 不得繞過這層保護（CLI
    `remagraph maintenance --force` 與 server.py 的 force 分支都會走到這
    條路徑，見 cli.py / server.py 對 run_maintenance(force=...) 的呼叫）。
    """
    project_id = "testproj"
    state_dir = _make_tier2_db(tmp_path, monkeypatch, project_id)

    policy = maint_mod.MaintenancePolicy()
    stats = maint_mod.run_maintenance(policy, project_id, force=True)

    assert stats.get("skipped") is True
    assert stats.get("skip_reason") == "read_only_schema_tier"
    assert "vacuum" not in stats
    assert "integrity" not in stats
    assert _sqlite_stat1_exists(state_dir) is False


def test_run_maintenance_skips_for_explicitly_passed_read_only_conn(tmp_path, monkeypatch):
    """呼叫端直接傳入既有 conn（而非讓 run_maintenance 自行開連線）的分支
    也必須一樣受保護 —— 唯讀檢查必須套用在『這條』conn 本身，不論它是誰
    開的。"""
    project_id = "testproj"
    _make_tier2_db(tmp_path, monkeypatch, project_id)

    conn = db.connect(project_id=project_id)
    assert getattr(conn, db.READ_ONLY_ATTR, False) is True

    policy = maint_mod.MaintenancePolicy()
    stats = maint_mod.run_maintenance(policy, project_id, force=True, conn=conn)

    assert stats.get("skipped") is True
    assert stats.get("skip_reason") == "read_only_schema_tier"


# ---------------------------------------------------------------------------
# 端到端：透過 db.connect() 實際觸發的路徑（真實 bug 重現場景）
# ---------------------------------------------------------------------------


def test_connect_in_tier2_mode_triggers_no_real_maintenance_writes(tmp_path, monkeypatch):
    """重現 bug 報告描述的實際路徑：db.connect(project_id=...) 對一個 tier-2
    資料庫回傳唯讀連線時，其尾端無條件呼叫的
    light_maintenance_on_connect(project_id) 內部開的獨立維護連線，必須完全
    不對資料庫執行任何寫入 —— 以 sqlite_stat1（ANALYZE 的直接證據）與
    run_maintenance 的回傳 stats 雙重驗證。

    範圍說明：db.py 尾端呼叫 light_maintenance_on_connect(project_id) 本身
    在本次修復中維持不變（不得修改 db.py，見本檔頂端 docstring）——因此下方
    透過 spy 觀察到的是「run_maintenance 被呼叫、但立即偵測唯讀並整個
    no-op」，而不是「完全未被呼叫」。
    """
    project_id = "testproj"
    state_dir = _make_tier2_db(tmp_path, monkeypatch, project_id)

    captured_stats: list[dict] = []
    original_run_maintenance = maint_mod.run_maintenance

    def _spy(*args, **kwargs):
        result = original_run_maintenance(*args, **kwargs)
        captured_stats.append(result)
        return result

    monkeypatch.setattr(maint_mod, "run_maintenance", _spy)

    conn_ro = db.connect(project_id=project_id)
    try:
        assert getattr(conn_ro, db.READ_ONLY_ATTR, False) is True

        assert len(captured_stats) == 1
        assert captured_stats[0].get("skipped") is True
        assert captured_stats[0].get("skip_reason") == "read_only_schema_tier"

        # 最終、對資料庫檔案本身的證明：ANALYZE 從未執行過。
        assert _sqlite_stat1_exists(state_dir) is False
    finally:
        conn_ro.close()


# ---------------------------------------------------------------------------
# 無回歸：tier-1（一般、完全相容）連線的維護行為必須完全不變
# ---------------------------------------------------------------------------


def test_connect_in_tier1_mode_still_runs_light_maintenance_as_before(tmp_path, monkeypatch):
    """tier-1（完全相容）連線必須繼續一如既往地觸發真正的維護（本例會實際
    執行 ANALYZE），不得被本次修復意外連坐略過。"""
    project_id = "testproj"
    state_dir = tmp_path / "state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    captured_stats: list[dict] = []
    original_run_maintenance = maint_mod.run_maintenance

    def _spy(*args, **kwargs):
        result = original_run_maintenance(*args, **kwargs)
        captured_stats.append(result)
        return result

    monkeypatch.setattr(maint_mod, "run_maintenance", _spy)

    conn = db.connect(project_id=project_id)
    try:
        assert getattr(conn, db.READ_ONLY_ATTR, False) is False

        assert len(captured_stats) == 1
        stats = captured_stats[0]
        assert "skipped" not in stats
        assert stats.get("integrity") == "ok"

        # tier-1 的維護必須真的執行過 ANALYZE。
        assert _sqlite_stat1_exists(state_dir) is True
    finally:
        conn.close()


def test_run_maintenance_completes_normally_for_tier1_explicit_conn(tmp_path, monkeypatch):
    """對照組（既有行為的直接單元測試）：tier-1、由呼叫端顯式傳入的 conn，
    force=True 時完整維護流程必須照常執行到底（含 integrity 檢查與
    maintenance_completed 事件），不受本次修復影響。"""
    project_id = "testproj"
    state_dir = tmp_path / "state"
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(state_dir))

    conn = db.connect(state_dir=state_dir)
    policy = maint_mod.MaintenancePolicy()
    stats = maint_mod.run_maintenance(policy, project_id, force=True, conn=conn)

    assert "skipped" not in stats
    assert stats["project_id"] == project_id
    assert stats["integrity"] == "ok"
