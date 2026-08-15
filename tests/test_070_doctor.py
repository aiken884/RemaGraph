# SPDX-License-Identifier: Apache-2.0
"""0.7.0 項目 A：`remagraph doctor` 唯讀健檢（PPLX 兩輪審查定案設計）。

驗收要求（審查裁定）：每檢查面 ok/warn/fail/skip 態；污染 registry＋stray
合成環境整合測試；JSON snapshot；execute-bit（Windows skip）；WAL 殘留
「正常使用中不誤 warn」；5xx→skip；registry data 的 total_count/truncated。
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from remagraph import db as db_mod
from remagraph.doctor import (
    CheckResult,
    DoctorReport,
    check_database,
    check_post_commit_hook,
    check_registry,
    check_state_dir,
    check_stray_records,
    run_doctor,
)

SUMMARY = "一筆長度足夠通過仲裁下限的測試 summary，內容填充填充填充填充"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)
    monkeypatch.delenv("REMAGRAPH_PROJECT", raising=False)
    monkeypatch.setattr(
        db_mod, "DEFAULT_STATE_DIR", home / ".local" / "state" / "remagraph"
    )
    return home


def _init_project(home: Path, project: str) -> Path:
    state_dir = home / ".local" / "state" / f"remagraph-{project}"
    state_dir.mkdir(parents=True)
    (state_dir / "project.json").write_text(
        json.dumps({"project_id": project, "state_dir": str(state_dir)}),
        encoding="utf-8",
    )
    db_mod.connect_at_state_dir(state_dir).close()
    return state_dir


def _insert_memory(db_path: Path, project_id: str, mem_id: str = "mem-20260815-001"):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    now = "2026-08-15T00:00:00Z"
    conn.execute(
        "INSERT INTO memories (id, project_id, kind, task_id, agent_id, timestamp, "
        "summary, learnings, handoff_note, tags, status, created_at, updated_at) "
        "VALUES (?, ?, 'status_update', 't', 'a', ?, ?, '[]', '', '[]', 'active', ?, ?)",
        (mem_id, project_id, now, SUMMARY, now, now),
    )
    conn.commit()
    conn.close()


def _make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "--template="], cwd=path, check=True)
    return path


# ---------------------------------------------------------------------------
# overall 聚合與 exit code
# ---------------------------------------------------------------------------


class TestAggregation:
    def _report(self, *statuses: str) -> DoctorReport:
        r = DoctorReport()
        for i, s in enumerate(statuses):
            r.checks.append(CheckResult(f"c{i}", s, "m"))
        return r

    def test_fail_wins(self):
        assert self._report("ok", "warn", "fail").overall == "fail"
        assert self._report("fail").exit_code == 1

    def test_warn_beats_ok(self):
        assert self._report("ok", "warn").overall == "warn"
        assert self._report("warn").exit_code == 2

    def test_skip_counts_as_ok(self):
        assert self._report("ok", "skip").overall == "ok"
        assert self._report("skip").exit_code == 0


# ---------------------------------------------------------------------------
# 檢查面 2：post-commit hook
# ---------------------------------------------------------------------------


class TestHookCheck:
    def test_outside_git_is_skip(self, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        assert check_post_commit_hook(plain).status == "skip"

    def test_missing_hook_is_warn(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        result = check_post_commit_hook(repo)
        assert result.status == "warn"
        assert "install-hooks" in result.message

    def test_unmanaged_hook_is_warn(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        hooks = repo / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        (hooks / "post-commit").write_text("#!/bin/sh\necho custom\n")
        (hooks / "post-commit").chmod(0o755)
        assert check_post_commit_hook(repo).status == "warn"

    def test_current_managed_hook_is_ok(self, tmp_path):
        from remagraph.hooks_installer import get_bundled_hook_text

        repo = _make_repo(tmp_path / "repo")
        hooks = repo / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        hook = hooks / "post-commit"
        hook.write_text(get_bundled_hook_text())
        hook.chmod(0o755)
        result = check_post_commit_hook(repo)
        assert result.status == "ok"

    def test_old_version_hook_is_warn(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        hooks = repo / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        hook = hooks / "post-commit"
        hook.write_text(
            "#!/bin/sh\n# remagraph-managed-hook v1\n"
            "# remagraph-fields-schema-version: 1\n"
        )
        hook.chmod(0o755)
        result = check_post_commit_hook(repo)
        assert result.status == "warn"
        assert "fields-schema-version" in result.message

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX execute bit")
    def test_non_executable_hook_is_warn(self, tmp_path):
        from remagraph.hooks_installer import get_bundled_hook_text

        repo = _make_repo(tmp_path / "repo")
        hooks = repo / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        hook = hooks / "post-commit"
        hook.write_text(get_bundled_hook_text())
        hook.chmod(0o644)  # 存在但不可執行——靜默失敗的高頻原因
        result = check_post_commit_hook(repo)
        assert result.status == "warn"
        assert "executable" in result.message


# ---------------------------------------------------------------------------
# 檢查面 3：state dir
# ---------------------------------------------------------------------------


class TestStateDirCheck:
    def test_missing_is_warn(self, isolated):
        result = check_state_dir("neverinit")
        assert result.status == "warn"
        assert "init" in result.message

    def test_healthy_is_ok(self, isolated):
        _init_project(isolated, "goodproj")
        assert check_state_dir("goodproj").status == "ok"

    def test_mismatched_metadata_is_fail(self, isolated):
        state_dir = isolated / ".local" / "state" / "remagraph-badproj"
        state_dir.mkdir(parents=True)
        (state_dir / "project.json").write_text(
            json.dumps({"project_id": "completely-different"}), encoding="utf-8"
        )
        assert check_state_dir("badproj").status == "fail"


# ---------------------------------------------------------------------------
# 檢查面 4：registry（含污染掃描與 total_count/truncated）
# ---------------------------------------------------------------------------


class TestRegistryCheck:
    def test_unregistered_is_ok(self, isolated):
        assert check_registry("nowhere", all_projects=False).status == "ok"

    def test_healthy_entry_is_ok(self, isolated, tmp_path):
        d = tmp_path / "custom-state"
        d.mkdir()
        db_mod.register_known_project("healthy", d)
        assert check_registry("healthy", all_projects=False).status == "ok"

    def test_poisoned_entry_is_warn(self, isolated):
        shared = Path(db_mod.DEFAULT_STATE_DIR)
        shared.mkdir(parents=True)
        db_mod.register_known_project("victim", shared)
        result = check_registry("victim", all_projects=False)
        assert result.status == "warn"
        assert "poisoned" in result.message

    def test_all_projects_scan_reports_counts(self, isolated, tmp_path):
        shared = Path(db_mod.DEFAULT_STATE_DIR)
        shared.mkdir(parents=True)
        db_mod.register_known_project("victim-a", shared)
        db_mod.register_known_project("victim-b", shared)
        ok_dir = tmp_path / "ok-state"
        ok_dir.mkdir()
        db_mod.register_known_project("finethanks", ok_dir)
        result = check_registry("victim-a", all_projects=True)
        assert result.status == "warn"
        assert result.data is not None
        assert result.data["total_count"] == 2
        assert result.data["truncated"] is False
        assert "disclaimer" in result.data


# ---------------------------------------------------------------------------
# 檢查面 5：stray 記錄
# ---------------------------------------------------------------------------


class TestStrayCheck:
    def test_no_shared_db_is_ok(self, isolated):
        assert check_stray_records("any", all_projects=False).status == "ok"

    def test_own_stray_is_warn_with_recovery_hint(self, isolated):
        shared = Path(db_mod.DEFAULT_STATE_DIR)
        db_mod.connect_at_state_dir(shared).close()
        _insert_memory(shared / db_mod.DB_FILENAME, "strayproj")
        result = check_stray_records("strayproj", all_projects=False)
        assert result.status == "warn"
        assert "migrate-project" in result.message

    def test_other_projects_stray_invisible_in_single_mode(self, isolated):
        shared = Path(db_mod.DEFAULT_STATE_DIR)
        db_mod.connect_at_state_dir(shared).close()
        _insert_memory(shared / db_mod.DB_FILENAME, "someoneelse")
        assert check_stray_records("mine", all_projects=False).status == "ok"

    def test_all_projects_mode_sees_everything(self, isolated):
        shared = Path(db_mod.DEFAULT_STATE_DIR)
        db_mod.connect_at_state_dir(shared).close()
        _insert_memory(shared / db_mod.DB_FILENAME, "stray-x", "mem-20260815-001")
        _insert_memory(shared / db_mod.DB_FILENAME, "stray-y", "mem-20260815-002")
        result = check_stray_records("mine", all_projects=True)
        assert result.status == "warn"
        assert result.data is not None
        assert set(result.data["per_project"]) == {"stray-x", "stray-y"}


# ---------------------------------------------------------------------------
# 檢查面 6：database（WAL、殘留不誤 warn）
# ---------------------------------------------------------------------------


class TestDatabaseCheck:
    def test_healthy_db_is_ok(self, isolated):
        _init_project(isolated, "dbproj")
        result = check_database("dbproj")
        assert result.status == "ok"
        assert result.data["journal_mode"].lower() == "wal"

    def test_active_wal_file_does_not_warn(self, isolated):
        """正常使用中 -wal 檔存在（mtime 新）不得誤 warn（審查條件 A.5）。"""
        state_dir = _init_project(isolated, "walproj")
        conn = db_mod.connect_at_state_dir(state_dir)
        conn.execute("CREATE TABLE IF NOT EXISTS _touch (x INTEGER)")
        # 保持 -wal 存在（不 checkpoint）
        wal = Path(str(state_dir / db_mod.DB_FILENAME) + "-wal")
        result = check_database("walproj")
        conn.close()
        assert result.status == "ok", f"{result.message} (wal exists: {wal.exists()})"

    def test_stale_wal_file_warns(self, isolated):
        state_dir = _init_project(isolated, "staleproj")
        db_path = state_dir / db_mod.DB_FILENAME
        wal = Path(str(db_path) + "-wal")
        wal.write_bytes(b"")
        old = time.time() - 30 * 24 * 3600
        os.utime(wal, (old, old))
        result = check_database("staleproj")
        assert result.status == "warn"
        assert "stale" in result.message

    def test_no_db_is_warn(self, isolated):
        state_dir = isolated / ".local" / "state" / "remagraph-emptyproj"
        state_dir.mkdir(parents=True)
        (state_dir / "project.json").write_text(
            json.dumps({"project_id": "emptyproj"}), encoding="utf-8"
        )
        assert check_database("emptyproj").status == "warn"


# ---------------------------------------------------------------------------
# 整合：run_doctor 與 JSON snapshot
# ---------------------------------------------------------------------------


class TestRunDoctor:
    def test_full_run_on_healthy_project(self, isolated, tmp_path):
        _init_project(isolated, "myproj")
        repo = _make_repo(tmp_path / "myproj")
        report = run_doctor("myproj", cwd=repo, skip_network=True)
        names = [c.name for c in report.checks]
        assert names == [
            "cli_version", "post_commit_hook", "state_dir", "registry",
            "stray_records", "database",
        ]

    def test_json_schema_snapshot(self, isolated, tmp_path):
        """JSON schema 凍結（審查條件 A.6）：頂層鍵與 check 欄位固定。"""
        _init_project(isolated, "snapproj")
        report = run_doctor("snapproj", cwd=tmp_path, skip_network=True)
        payload = report.to_json()
        assert set(payload.keys()) == {"schema_version", "overall", "checks"}
        assert payload["schema_version"] == 1
        assert payload["overall"] in ("ok", "warn", "fail")
        for check in payload["checks"]:
            assert set(check.keys()) == {"name", "status", "message", "data"}
            assert check["status"] in ("ok", "warn", "fail", "skip")

    def test_underivable_project_all_checks_skip_or_run(self, isolated, tmp_path):
        plain = tmp_path / "not-a-repo"
        plain.mkdir()
        report = run_doctor(None, cwd=plain, skip_network=True)
        by_name = {c.name: c for c in report.checks}
        assert by_name["state_dir"].status == "skip"
        assert by_name["database"].status == "skip"

    def test_cli_doctor_subcommand(self, isolated, tmp_path, monkeypatch, capsys):
        from remagraph.cli import main as cli_main

        _init_project(isolated, "cliproj")
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as exc:
            cli_main(["doctor", "--project", "cliproj", "--json", "--offline"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["schema_version"] == 1
        assert exc.value.code in (0, 2)  # 健康專案：ok 或（hook缺）warn


# ---------------------------------------------------------------------------
# 檢查面 1：PyPI 5xx → skip（審查條件 A.1）
# ---------------------------------------------------------------------------


def test_pypi_5xx_is_skip(monkeypatch):
    import urllib.request

    from remagraph.doctor import check_cli_version

    class _Resp:
        status = 503

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    assert check_cli_version().status == "skip"
