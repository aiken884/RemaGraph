# SPDX-License-Identifier: Apache-2.0
"""0.7.0 項目 C：併發 store 的鎖逾時重試（PPLX 兩輪審查定案設計）。

三層架構：L1 WAL（既有）；L2 建線時固定 busy_timeout=150ms（取代 v2 的
per-transaction PRAGMA 切換——check_same_thread=False 的共用連線上動態
切換有競態面，建線固定值徹底根除）；L3 應用層 BEGIN IMMEDIATE 重試
3 次、退避 0.1/0.2/0.4s ± jitter、僅 "locked" 訊息重試。
最壞預算 (0.15×4)+(0.7)+jitter ≈ 1.45s 上限。
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

import pytest

from remagraph import db as db_mod
from remagraph import store as store_mod
from remagraph.models import StoreRequest

SUMMARY = "一筆長度足夠通過仲裁下限的測試 summary，內容填充填充填充填充"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("REMAGRAPH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("REMAGRAPH_PROJECT", raising=False)


def _request() -> StoreRequest:
    return StoreRequest(
        project_id="retry-proj", task_id="retry-proj-task-1", agent_id="agent-1",
        kind="task_handoff", summary=SUMMARY,
        learnings=["一條有效的 learning 記錄"],
        handoff_note="一段長度足夠通過驗證的 handoff note 內容",
        tags=[],
    )


class _LockedNTimesConn:
    """代理連線：前 N 次 BEGIN 拋 locked，之後放行；記錄每次 BEGIN 時刻。"""

    def __init__(self, real: sqlite3.Connection, fail_times: int) -> None:
        self._real = real
        self._fail_remaining = fail_times
        self.begin_timestamps: list[float] = []

    def execute(self, sql: str, *args: Any):
        if sql.strip().upper().startswith("BEGIN"):
            self.begin_timestamps.append(time.monotonic())
            if self._fail_remaining > 0:
                self._fail_remaining -= 1
                raise sqlite3.OperationalError("database is locked")
        return self._real.execute(sql, *args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def test_store_retries_and_succeeds_on_second_attempt(tmp_path):
    real = db_mod.connect(project_id="retry-proj")
    conn = _LockedNTimesConn(real, fail_times=1)
    response = store_mod.process_store(_request(), conn)
    assert response.status == "stored"
    assert len(conn.begin_timestamps) == 2
    real.close()


def test_store_retry_exhaustion_returns_error_with_retry_note(tmp_path):
    real = db_mod.connect(project_id="retry-proj")
    conn = _LockedNTimesConn(real, fail_times=10)
    response = store_mod.process_store(_request(), conn)
    assert response.status == "error"
    assert "retried 3 times" in (response.detail or "")
    # 1 次原始 + 3 次重試 = 4 次 BEGIN
    assert len(conn.begin_timestamps) == 4
    real.close()


def test_retry_backoff_has_jitter_and_increases(tmp_path):
    """每次重試間有延遲、非固定值（jitter 存在）、且趨勢遞增。"""
    real = db_mod.connect(project_id="retry-proj")
    conn = _LockedNTimesConn(real, fail_times=10)
    store_mod.process_store(_request(), conn)
    ts = conn.begin_timestamps
    gaps = [ts[i + 1] - ts[i] for i in range(len(ts) - 1)]
    assert len(gaps) == 3
    # 名目退避 0.1/0.2/0.4 ± 0.05 jitter；上限放寬容忍 time.sleep 的
    # 系統調度過睡（實測 macOS 負載下可達 +0.1s）
    assert 0.05 <= gaps[0] <= 0.4
    assert 0.1 <= gaps[1] <= 0.6
    assert 0.3 <= gaps[2] <= 0.9
    # 退避遞增趨勢（指數退避的可觀測性質）
    assert gaps[2] > gaps[0]
    real.close()


def test_non_locked_operational_error_is_not_retried(tmp_path):
    class _DiskFullConn(_LockedNTimesConn):
        def execute(self, sql: str, *args: Any):
            if sql.strip().upper().startswith("BEGIN"):
                self.begin_timestamps.append(time.monotonic())
                raise sqlite3.OperationalError("database or disk is full")
            return self._real.execute(sql, *args)

    real = db_mod.connect(project_id="retry-proj")
    conn = _DiskFullConn(real, fail_times=0)
    response = store_mod.process_store(_request(), conn)
    assert response.status == "error"
    assert len(conn.begin_timestamps) == 1, "非 locked 錯誤不得重試"
    real.close()


def test_connections_have_150ms_busy_timeout(tmp_path):
    """L2：建線時固定 busy_timeout=150ms（db.connect 與 connect_at_state_dir）。"""
    conn = db_mod.connect(project_id="retry-proj")
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 150
    conn.close()
    conn2 = db_mod.connect_at_state_dir(tmp_path / "other-state")
    assert conn2.execute("PRAGMA busy_timeout").fetchone()[0] == 150
    conn2.close()


def test_memory_id_generation_error_removed():
    """死例外（docstring 宣稱有重試、實作沒有）已刪除。"""
    assert not hasattr(store_mod, "MemoryIDGenerationError")
