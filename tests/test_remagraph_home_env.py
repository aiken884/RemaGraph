# SPDX-License-Identifier: Apache-2.0
"""Regression tests for the REMAGRAPH_HOME env var (PPLX 架構改善計畫 —
「外部 subprocess 測試會洩漏真實 DEFAULT_STATE_DIR」缺口修復)。

背景：DEFAULT_STATE_DIR（= Path.home() / ".local" / "state" / "remagraph"）
目前是 db.py 的模組級常數，唯一的隔離方式是 Python 層級 monkeypatch
db_mod.DEFAULT_STATE_DIR（見 tests/conftest.py 的 autouse fixture
_isolate_default_state_dir）——這對「in-process」的 pytest 測試有效，但對任何
透過 subprocess 呼叫真正安裝好的 `remagraph` CLI 的外部整合測試完全無效
（不同 OS process，monkeypatch 不跨 process 邊界）。

本檔驗證新增的 REMAGRAPH_HOME 環境變數：設定時整個蓋掉共用/預設 state 的
落地位置（透過 db._resolve_default_state_dir()，被
db.get_state_dir()/db.is_using_default_state_dir()/
db._connect_default_registry_db() 三處呼叫點使用）；未設定時完全不影響既有
行為 —— 包含完全不影響 conftest.py 既有的 DEFAULT_STATE_DIR monkeypatch
隔離機制（兩者是刻意設計成能同時並存的獨立機制，見
db._resolve_default_state_dir() 的 docstring）。
"""

from __future__ import annotations

import pytest

from remagraph import db as db_mod

# ---------------------------------------------------------------------------
# REMAGRAPH_HOME 未設定 —— 行為應與修改前完全一致
# ---------------------------------------------------------------------------


def test_remagraph_home_unset_resolver_returns_default_state_dir(monkeypatch):
    """未設定 REMAGRAPH_HOME 時，_resolve_default_state_dir() 應直接回傳目前
    db_mod.DEFAULT_STATE_DIR 這個模組屬性目前的值 —— 不論它是否已被
    conftest.py 的 autouse fixture monkeypatch 過。
    """
    monkeypatch.delenv("REMAGRAPH_HOME", raising=False)
    assert db_mod._resolve_default_state_dir() == db_mod.DEFAULT_STATE_DIR


def test_remagraph_home_unset_register_known_project_writes_to_default_state_dir(
    tmp_path, monkeypatch
):
    """REMAGRAPH_HOME 未設定時，register_known_project() 仍應寫入目前
    db_mod.DEFAULT_STATE_DIR（此刻已由 conftest.py 的 autouse fixture
    monkeypatch 成一個隔離的 tmp_path 子目錄）—— 驗證既有 conftest.py 隔離
    機制在本次修改後仍完全生效、未被繞過。
    """
    monkeypatch.delenv("REMAGRAPH_HOME", raising=False)
    db_mod.register_known_project("proj-unset-home", tmp_path / "proj-state")
    registry_db = db_mod.DEFAULT_STATE_DIR / db_mod.DB_FILENAME
    assert registry_db.exists()


# ---------------------------------------------------------------------------
# REMAGRAPH_HOME 設定 —— 應整個覆蓋共用 state 的落地位置
# ---------------------------------------------------------------------------


def test_remagraph_home_set_resolver_returns_resolved_env_path(tmp_path, monkeypatch):
    custom = tmp_path / "remagraph-home"
    monkeypatch.setenv("REMAGRAPH_HOME", str(custom))
    resolved = db_mod._resolve_default_state_dir()
    assert resolved == custom.resolve()
    assert resolved != db_mod.DEFAULT_STATE_DIR


def test_remagraph_home_set_register_known_project_writes_under_it_not_default(
    tmp_path, monkeypatch
):
    """核心情境：REMAGRAPH_HOME 指向一個 tmp_path 目錄時，
    register_known_project()（透過 _connect_default_registry_db()）應該把
    registry 資料庫寫到該目錄下，而不是目前的 db_mod.DEFAULT_STATE_DIR
    （此刻已被 conftest.py monkeypatch 成另一個、完全不同的隔離目錄）。
    """
    custom = tmp_path / "remagraph-home"
    monkeypatch.setenv("REMAGRAPH_HOME", str(custom))

    db_mod.register_known_project("proj-remagraph-home", tmp_path / "proj-state")

    db_under_home = custom / db_mod.DB_FILENAME
    assert db_under_home.exists()

    db_under_stale_default = db_mod.DEFAULT_STATE_DIR / db_mod.DB_FILENAME
    assert not db_under_stale_default.exists()


def test_remagraph_home_set_list_known_projects_reads_from_it(tmp_path, monkeypatch):
    custom = tmp_path / "remagraph-home"
    monkeypatch.setenv("REMAGRAPH_HOME", str(custom))

    db_mod.register_known_project("proj-list-check", tmp_path / "proj-state")
    known = {p["project_id"]: p for p in db_mod.list_known_projects()}

    assert "proj-list-check" in known


# ---------------------------------------------------------------------------
# 驗證邏輯 —— 必須重用 REMAGRAPH_STATE_DIR 既有的同一套規則
# ---------------------------------------------------------------------------


def test_remagraph_home_invalid_characters_raises_value_error(monkeypatch):
    monkeypatch.setenv("REMAGRAPH_HOME", "/tmp/bad;rm -rf$(whoami)")
    with pytest.raises(ValueError):
        db_mod._resolve_default_state_dir()


def test_remagraph_home_forbidden_system_prefix_raises_value_error(monkeypatch):
    """刻意選 /usr 而非 /etc 作為禁止前綴的測試對象：在 macOS 上 /etc 本身是
    指向 /private/etc 的 symlink，Path.resolve() 後前綴會變成 /private/etc、
    不再匹配 forbidden_prefixes 裡的 "/etc" 字串前綴 —— 這是 REMAGRAPH_
    STATE_DIR 既有驗證邏輯本來就有的平台特性（本測試依規格重用同一套邏輯，
    不多寫新驗證，因此原樣繼承這個特性），/usr 在 macOS 上不是 symlink，
    resolve() 後前綴不變，才能穩定驗證『禁止系統目錄』這條規則本身。
    """
    monkeypatch.setenv("REMAGRAPH_HOME", "/usr/remagraph-shared-state")
    with pytest.raises(ValueError):
        db_mod._resolve_default_state_dir()


# ---------------------------------------------------------------------------
# is_using_default_state_dir() 在 REMAGRAPH_HOME 設定時的正確性
# ---------------------------------------------------------------------------


def test_is_using_default_state_dir_true_when_falls_back_to_remagraph_home(
    tmp_path, monkeypatch
):
    """未傳入 state_dir、REMAGRAPH_STATE_DIR 未設定時，get_state_dir() 會走
    resolver 的 fallback 分支；is_using_default_state_dir() 應對『同一個
    resolver 回傳值』的比較恆為 True —— 不會因為 REMAGRAPH_HOME 設定就對著
    已過期的原始 DEFAULT_STATE_DIR 常數誤判為 False。
    """
    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)
    custom = tmp_path / "remagraph-home"
    monkeypatch.setenv("REMAGRAPH_HOME", str(custom))

    assert db_mod.is_using_default_state_dir() is True


def test_is_using_default_state_dir_true_when_explicit_dir_matches_remagraph_home(
    tmp_path, monkeypatch
):
    """關鍵正確性情境：明確傳入『與 REMAGRAPH_HOME 相同』的 state_dir 時，
    is_using_default_state_dir() 必須比較 REMAGRAPH_HOME 解析後的值，而不是
    早已過期、與 REMAGRAPH_HOME 無關的原始 DEFAULT_STATE_DIR 模組常數 ——
    否則即使呼叫端傳入的目錄正是 REMAGRAPH_HOME 指定的共用目錄，也會被誤判
    為『不是預設目錄』。
    """
    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)
    custom = tmp_path / "remagraph-home"
    monkeypatch.setenv("REMAGRAPH_HOME", str(custom))

    assert db_mod.is_using_default_state_dir(state_dir=custom) is True


def test_is_using_default_state_dir_false_when_explicit_dir_differs_from_remagraph_home(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)
    custom = tmp_path / "remagraph-home"
    other = tmp_path / "some-other-project-dir"
    monkeypatch.setenv("REMAGRAPH_HOME", str(custom))

    assert db_mod.is_using_default_state_dir(state_dir=other) is False


def test_is_using_default_state_dir_true_when_explicit_dir_is_unresolved_spelling_of_remagraph_home(
    tmp_path, monkeypatch
):
    """對抗式審查發現：上一版對這個情境的迴歸測試只用了兩次 tmp_path 呼叫，
    但 pytest 的 tmp_path 剛好本來就已經是完全展開（resolve 後不變）的路徑，
    兩邊字串其實從頭到尾都相同 —— 從未真正測試到『不同拼法、但實際是同一個
    真實目錄』這個情境本身，因而給了假的信心。

    這裡刻意建構『同一個真實目錄』的兩種不同拼法：REMAGRAPH_HOME 設成一個
    乾淨、已展開的路徑；呼叫端傳入的 state_dir 則刻意帶一段 Path 建構當下
    不會被自動正規化掉的 `..` 片段（只有呼叫 .resolve() 才會真正展開）。
    因此 resolve 前兩者字串必然不同（=> Path.__eq__ 為 False），resolve
    後則指向同一個真實目錄（=> 相等）—— 確保這個測試是否通過，真的取決於
    get_state_dir() 是否有對呼叫端傳入的 state_dir 呼叫 .resolve()，而不是
    像前一版那樣意外靠 tmp_path 本身已展開的巧合矇混過關。

    Root cause（修復前失敗）：_resolve_default_state_dir() 對 REMAGRAPH_HOME
    呼叫了 .resolve()，但 get_state_dir() 的
    `elif state_dir is not None: resolved = state_dir` 分支沒有對呼叫端
    傳入的路徑呼叫 .resolve()，導致兩個字面上拼法不同、但實際是同一個真實
    目錄的路徑物件比較為不相等，is_using_default_state_dir() 因而回傳錯誤
    的 False。
    """
    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)
    real_dir = tmp_path / "remagraph-home"
    real_dir.mkdir()
    monkeypatch.setenv("REMAGRAPH_HOME", str(real_dir))

    decoy_sibling = tmp_path / "decoy-sibling"
    unresolved_spelling = decoy_sibling / ".." / "remagraph-home"

    # 前置條件（修復前後皆須成立，確保這真的是「不同拼法、同一個真實目錄」）：
    assert unresolved_spelling != real_dir
    assert unresolved_spelling.resolve() == real_dir.resolve()

    assert db_mod.is_using_default_state_dir(state_dir=unresolved_spelling) is True
