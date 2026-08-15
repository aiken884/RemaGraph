# SPDX-License-Identifier: Apache-2.0
"""`remagraph doctor` — 唯讀健檢（0.7.0 項目 A，PPLX 兩輪審查定案）。

設計要點（審查裁定的硬性條件全數落實）：
- 唯讀：絕不修改任何狀態；修復動作各有專責指令（install-hooks、
  migrate-project、init），本模組不提供 --fix。
- 檢查面 scope：預設只檢查當前 project；`--all-projects` 才做 registry
  全表污染掃描與跨專案 stray 掃描（輸出附「本機 registry 本地資料」
  disclaimer，污染條目顯示前 50 筆＋total_count/truncated）。
- PyPI 版本查詢：timeout=3 秒；逾時/離線/非 200 一律 skip（不算 fail）。
- execute bit 檢查在 Windows（POSIX 語意不存在）自動 skip。
- WAL/SHM 殘留：-wal/-shm 檔存在且 mtime 比主 db 檔舊超過 7 天才 warn
  （活躍使用中 -wal 的 mtime 必然新，不誤 warn——審查條件）。
- JSON schema（版本 1，append-only）：
  {"schema_version": 1, "overall": "ok|warn|fail",
   "checks": [{"name": str, "status": "ok|warn|fail|skip",
               "message": str, "data": object|null}]}
- overall 聚合規則：任一 fail → fail；否則任一 warn → warn；skip 視同
  ok。exit code：0=ok、1=fail、2=warn（文件註明勿假設跨工具通用語意）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from remagraph import __version__
from remagraph import db as _db
from remagraph.hooks_installer import (
    CURRENT_FIELDS_SCHEMA_VERSION,
    MANAGED_HOOK_MARKER,
    _parse_fields_schema_version,
)
from remagraph.prompt_hook import resolve_conventional_state_dir

SCHEMA_VERSION = 1

_PYPI_TIMEOUT_S = 3


def _readonly_conn(db_path: Path) -> sqlite3.Connection | None:
    """doctor 專用唯讀連線：mode=ro + immutable=1 + query_only。

    對抗式審查修復（F1/F2）：mode=ro 仍會建立 -wal/-shm side files（且
    重置 stale 偵測的 mtime 基準）；immutable=1 完全不碰 side files。
    代價：immutable 下 `PRAGMA journal_mode` 失真（回 delete）——
    journal mode 一律改讀 db 檔 header（見 _journal_mode_from_header）；
    對正被寫入的 db，immutable 讀取可能撞到不一致而拋錯——所有呼叫端
    都以 except → skip 容錯（診斷快照允許輕微 stale）。
    """
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro&immutable=1", uri=True, timeout=1
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=1")
        return conn
    except sqlite3.Error:
        return None


def _journal_mode_from_header(db_path: Path) -> str | None:
    """讀 SQLite 檔頭 bytes 18–19（file format write/read version）：
    0x02 = WAL，0x01 = rollback journal。immutable 連線的 PRAGMA
    journal_mode 失真，必須從檔案本體判定。"""
    try:
        with open(db_path, "rb") as f:
            header = f.read(20)
    except OSError:
        return None
    if len(header) < 20 or header[:16] != b"SQLite format 3\x00":
        return None
    return "wal" if header[18] == 2 and header[19] == 2 else "delete"


def _registry_entries_readonly() -> list[dict[str, str]] | None:
    """唯讀讀取共用 registry（對抗式審查修復 F1：先前走
    db.get_registered_state_dir/list_known_projects，其內部
    _connect_default_registry_db 會 mkdir + CREATE TABLE——在乾淨機器上
    憑空建庫，直接違反 doctor 的唯讀承諾）。registry db 不存在或無
    project_registry 表 → 回傳 None（視為空 registry）。"""
    reg_db = Path(_db._resolve_default_state_dir()) / _db.DB_FILENAME
    conn = _readonly_conn(reg_db)
    if conn is None:
        return None
    try:
        rows = conn.execute(
            "SELECT project_id, state_dir FROM project_registry"
        ).fetchall()
        return [
            {"project_id": r["project_id"], "state_dir": r["state_dir"]}
            for r in rows
        ]
    except sqlite3.Error:
        return None
    finally:
        conn.close()
_POISON_SHOWN_LIMIT = 50
_WAL_STALE_AGE_S = 7 * 24 * 3600


@dataclass
class CheckResult:
    name: str
    status: str  # ok | warn | fail | skip
    message: str
    data: dict[str, Any] | None = None


@dataclass
class DoctorReport:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def overall(self) -> str:
        statuses = {c.status for c in self.checks}
        if "fail" in statuses:
            return "fail"
        if "warn" in statuses:
            return "warn"
        return "ok"  # skip 視同 ok（聚合規則，審查定案）

    @property
    def exit_code(self) -> int:
        return {"ok": 0, "fail": 1, "warn": 2}[self.overall]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "overall": self.overall,
            "checks": [
                {"name": c.name, "status": c.status, "message": c.message,
                 "data": c.data}
                for c in self.checks
            ],
        }


# ---------------------------------------------------------------------------
# 檢查面 1：CLI 版本 vs PyPI
# ---------------------------------------------------------------------------


def check_cli_version() -> CheckResult:
    try:
        import urllib.request

        with urllib.request.urlopen(
            "https://pypi.org/pypi/remagraph/json", timeout=_PYPI_TIMEOUT_S
        ) as resp:
            if resp.status != 200:
                return CheckResult(
                    "cli_version", "skip",
                    f"PyPI returned HTTP {resp.status}; skipping version check",
                )
            latest = json.loads(resp.read()).get("info", {}).get("version")
    except Exception as e:
        return CheckResult(
            "cli_version", "skip",
            f"PyPI unreachable ({type(e).__name__}); skipping version check",
        )
    if not latest:
        return CheckResult("cli_version", "skip", "PyPI response had no version")
    if latest == __version__:
        return CheckResult(
            "cli_version", "ok", f"remagraph {__version__} is the latest",
            {"installed": __version__, "latest": latest},
        )
    return CheckResult(
        "cli_version", "warn",
        f"installed {__version__}, latest on PyPI is {latest} "
        "(run: uv tool upgrade remagraph)",
        {"installed": __version__, "latest": latest},
    )


# ---------------------------------------------------------------------------
# 檢查面 2：post-commit hook
# ---------------------------------------------------------------------------


def _resolve_hooks_dir(cwd: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    common = Path(result.stdout.strip())
    if not common.is_absolute():
        common = (cwd / common).resolve()
    try:
        hp = subprocess.run(
            ["git", "config", "core.hooksPath"],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        )
        if hp.returncode == 0 and hp.stdout.strip():
            hooks = Path(hp.stdout.strip())
            if not hooks.is_absolute():
                toplevel = subprocess.run(
                    ["git", "rev-parse", "--show-toplevel"],
                    cwd=cwd, capture_output=True, text=True, timeout=5,
                ).stdout.strip()
                hooks = Path(toplevel) / hooks
            return hooks
    except (OSError, subprocess.SubprocessError):
        pass
    return common / "hooks"


def check_post_commit_hook(cwd: Path) -> CheckResult:
    hooks_dir = _resolve_hooks_dir(cwd)
    if hooks_dir is None:
        return CheckResult(
            "post_commit_hook", "skip", "not inside a git repository"
        )
    hook = hooks_dir / "post-commit"
    if not hook.exists():
        return CheckResult(
            "post_commit_hook", "warn",
            f"no post-commit hook at {hook} (run: remagraph install-hooks)",
        )
    try:
        text = hook.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return CheckResult(
            "post_commit_hook", "fail", f"hook unreadable: {e}"
        )
    if MANAGED_HOOK_MARKER not in text:
        return CheckResult(
            "post_commit_hook", "warn",
            "post-commit hook exists but is not remagraph-managed",
        )
    version = _parse_fields_schema_version(text)
    data: dict[str, Any] = {
        "fields_schema_version": version,
        "current_version": CURRENT_FIELDS_SCHEMA_VERSION,
    }
    # execute bit：Windows 無 POSIX 語意，skip 此子檢查（審查條件 A.2）
    if sys.platform != "win32" and not os.access(hook, os.X_OK):
        return CheckResult(
            "post_commit_hook", "warn",
            f"hook at {hook} is not executable (chmod +x needed) — "
            "a present-but-unexecutable hook fails silently",
            data,
        )
    if version is None or version < CURRENT_FIELDS_SCHEMA_VERSION:
        return CheckResult(
            "post_commit_hook", "warn",
            f"hook fields-schema-version is {version}, current is "
            f"{CURRENT_FIELDS_SCHEMA_VERSION} (run: remagraph install-hooks)",
            data,
        )
    return CheckResult(
        "post_commit_hook", "ok",
        f"managed hook up to date (fields-schema-version {version})", data,
    )


# ---------------------------------------------------------------------------
# 檢查面 3：conventional state dir 與 project.json
# ---------------------------------------------------------------------------


def check_state_dir(project: str) -> CheckResult:
    resolved = resolve_conventional_state_dir(project)
    if resolved is None:
        return CheckResult(
            "state_dir", "warn",
            f"no conventional state dir for project {project!r} "
            f"(run: remagraph init --project {project})",
        )
    state_dir, authoritative = resolved
    meta_path = state_dir / "project.json"
    if not meta_path.exists():
        return CheckResult(
            "state_dir", "warn",
            f"{state_dir} exists but has no project.json "
            f"(run: remagraph init --project {project})",
            {"state_dir": str(state_dir)},
        )
    data = {"state_dir": str(state_dir), "authoritative_project_id": authoritative}
    if authoritative == project:
        return CheckResult(
            "state_dir", "ok", f"state dir healthy at {state_dir}", data
        )
    if authoritative.casefold() == project.casefold():
        return CheckResult(
            "state_dir", "ok",
            f"state dir healthy (authoritative id is {authoritative!r}; "
            f"queried as {project!r} — case differs, tools resolve via "
            "project.json)",
            data,
        )
    return CheckResult(
        "state_dir", "fail",
        f"project.json records {authoritative!r} which does not match "
        f"{project!r} — the safety valve will reject writes",
        data,
    )


# ---------------------------------------------------------------------------
# 檢查面 4：registry 健康度
# ---------------------------------------------------------------------------


def check_registry(project: str, *, all_projects: bool) -> CheckResult:
    default_dir = Path(_db.DEFAULT_STATE_DIR)

    def _is_shared_default(path_str: str) -> bool:
        try:
            p = Path(path_str)
            if p.resolve() == default_dir.resolve():
                return True
            return p.exists() and default_dir.exists() and p.samefile(default_dir)
        except OSError:
            return False

    entries = _registry_entries_readonly()

    if not all_projects:
        registered = None
        if entries is not None:
            for entry in entries:
                if entry["project_id"] == project:
                    registered = entry["state_dir"]
                    break
        if registered is None:
            return CheckResult(
                "registry", "ok",
                f"project {project!r} not in registry (normal for "
                "conventional-dir-only projects)",
            )
        if _is_shared_default(registered) and project != _db.DEFAULT_PROJECT_ID:
            return CheckResult(
                "registry", "warn",
                f"registry maps {project!r} to the shared default dir "
                f"({registered}) — poisoned entry from a pre-0.6.1 bare-env "
                "write; a successful migrate-project heals it",
                {"registered": registered},
            )
        return CheckResult(
            "registry", "ok", f"registry entry points to {registered}",
            {"registered": registered},
        )

    # --all-projects：全表污染掃描（本機 registry 本地資料，唯讀）
    if entries is None:
        return CheckResult(
            "registry", "ok", "no registry database on this machine yet"
        )
    known = entries
    poisoned: list[dict[str, str]] = []
    for entry in known:
        pid = entry["project_id"]
        reg = entry["state_dir"]
        if not pid or pid == _db.DEFAULT_PROJECT_ID:
            continue
        if reg and _is_shared_default(reg):
            poisoned.append({"project_id": pid, "registered": reg})
    data = {
        "disclaimer": "local registry data on this machine only",
        "shown": poisoned[:_POISON_SHOWN_LIMIT],
        "total_count": len(poisoned),
        "truncated": len(poisoned) > _POISON_SHOWN_LIMIT,
    }
    if poisoned:
        return CheckResult(
            "registry", "warn",
            f"{len(poisoned)} poisoned registry entrie(s) point non-default "
            "projects at the shared default dir",
            data,
        )
    return CheckResult(
        "registry", "ok", f"no poisoned entries among {len(known)} known projects",
        data,
    )


# ---------------------------------------------------------------------------
# 檢查面 5：共用 default db 的 stray 記錄
# ---------------------------------------------------------------------------


def check_stray_records(project: str, *, all_projects: bool) -> CheckResult:
    # F3（對抗式審查修復）：default 專案的記錄在共用 db 本來就是「家」，
    # 不是 stray——單專案模式下對 default 一律 ok，與 --all-projects
    # 分支的排除邏輯一致，避免自相矛盾的訊息與危險的 migrate 建議。
    if not all_projects and project == _db.DEFAULT_PROJECT_ID:
        return CheckResult(
            "stray_records", "ok",
            "default project's records live in the shared db by design",
        )
    shared_db = Path(_db.DEFAULT_STATE_DIR) / _db.DB_FILENAME
    conn = _readonly_conn(shared_db)
    if conn is None:
        return CheckResult(
            "stray_records", "ok", "no shared default db on this machine"
        )
    try:
        if True:
            if all_projects:
                rows = conn.execute(
                    "SELECT project_id, COUNT(*) AS n FROM memories "
                    "WHERE project_id != ? AND status = 'active' "
                    "GROUP BY project_id ORDER BY n DESC",
                    (_db.DEFAULT_PROJECT_ID,),
                ).fetchall()
                strays = {r["project_id"]: r["n"] for r in rows}
            else:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM memories "
                    "WHERE project_id = ? AND status = 'active'",
                    (project,),
                ).fetchone()
                strays = {project: row["n"]} if row["n"] else {}
    except sqlite3.Error as e:
        return CheckResult(
            "stray_records", "skip", f"shared db unreadable ({e})"
        )
    finally:
        conn.close()
    if not strays:
        return CheckResult(
            "stray_records", "ok", "no stray project records in the shared db"
        )
    total = sum(strays.values())
    return CheckResult(
        "stray_records", "warn",
        f"{total} active record(s) for non-default project(s) stranded in "
        "the shared db (recover with: REMAGRAPH_STATE_DIR="
        f"{_db.DEFAULT_STATE_DIR} remagraph migrate-project --from default "
        "--to <project> --dry-run)",
        {"per_project": strays},
    )


# ---------------------------------------------------------------------------
# 檢查面 6：資料庫健康度（可開啟性、schema 相容、WAL 狀態、殘留檔）
# ---------------------------------------------------------------------------


def check_database(project: str) -> CheckResult:
    resolved = resolve_conventional_state_dir(project)
    if resolved is None:
        return CheckResult(
            "database", "skip", f"no state dir for {project!r}; nothing to check"
        )
    state_dir, _authoritative = resolved
    db_path = state_dir / _db.DB_FILENAME
    if not db_path.exists():
        return CheckResult(
            "database", "warn", f"state dir exists but no database at {db_path}"
        )
    data: dict[str, Any] = {"db_path": str(db_path)}
    # journal mode 從檔案 header 判定（immutable 連線的 PRAGMA 失真，F2）
    data["journal_mode"] = _journal_mode_from_header(db_path)
    conn = _readonly_conn(db_path)
    if conn is None:
        return CheckResult(
            "database", "fail", f"database unopenable at {db_path}", data
        )
    legacy_no_meta = False
    try:
        try:
            conn.execute("SELECT 1").fetchone()
        except sqlite3.Error as e:
            return CheckResult("database", "fail", f"database unreadable: {e}", data)
        # _meta 缺失（pre-_meta 老 db）不是「開不起來」——分開容錯（F5）
        try:
            row = conn.execute(
                "SELECT value FROM _meta WHERE key='schema_version'"
            ).fetchone()
            data["schema_version"] = int(row[0]) if row else None
            mw = conn.execute(
                "SELECT value FROM _meta WHERE key='min_writer_version'"
            ).fetchone()
            data["min_writer_version"] = int(mw[0]) if mw else None
        except sqlite3.Error:
            legacy_no_meta = True
            data["schema_version"] = None
            data["min_writer_version"] = None
    finally:
        conn.close()

    issues: list[str] = []
    if legacy_no_meta:
        issues.append(
            "no _meta table (legacy pre-versioning database) — open any "
            "remagraph write command once to migrate it"
        )
    if str(data.get("journal_mode", "")).lower() != "wal":
        issues.append(
            f"journal_mode is {data.get('journal_mode')!r}, expected WAL"
        )
    sv = data.get("schema_version")
    if sv is not None and sv > _db.SCHEMA_VERSION:
        mwv = data.get("min_writer_version")
        if mwv is not None and _db.SCHEMA_VERSION < mwv:
            issues.append(
                f"db schema {sv} is newer than this CLI ({_db.SCHEMA_VERSION}); "
                "writes are read-only degraded — upgrade remagraph"
            )
    # WAL/SHM 殘留：mtime 比主 db 舊超過 7 天才視為殘留（活躍使用中 -wal
    # 的 mtime 必然新，不誤 warn——審查條件 A.5）
    try:
        db_mtime = db_path.stat().st_mtime
        for suffix in ("-wal", "-shm"):
            side = Path(str(db_path) + suffix)
            if side.exists() and db_mtime - side.stat().st_mtime > _WAL_STALE_AGE_S:
                issues.append(
                    f"stale {side.name} (mtime {int((db_mtime - side.stat().st_mtime) / 86400)}d "
                    "older than the db) — possible leftover from an abnormal exit"
                )
    except OSError:
        pass
    if issues:
        return CheckResult("database", "warn", "; ".join(issues), data)
    return CheckResult(
        "database", "ok",
        f"database healthy (schema v{data.get('schema_version')}, WAL)", data,
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def run_doctor(
    project: str | None,
    *,
    all_projects: bool = False,
    cwd: str | Path | None = None,
    skip_network: bool = False,
) -> DoctorReport:
    """執行全部檢查面，回傳報告。唯讀。

    project 未指定時從 cwd 的 git repo 推導（比照 prompt-hook 的候選規則）；
    推導不出時專案綁定類檢查標 skip。skip_network 供測試/離線環境跳過
    PyPI 查詢（標 skip）。
    """
    cwd_path = Path(cwd) if cwd else Path.cwd()
    report = DoctorReport()

    if skip_network:
        report.checks.append(
            CheckResult("cli_version", "skip", "network check disabled")
        )
    else:
        report.checks.append(check_cli_version())

    report.checks.append(check_post_commit_hook(cwd_path))

    effective_project = project or None  # 空字串視同未指定（F4 防護）
    if effective_project is None:
        from remagraph.prompt_hook import derive_project_candidates_from_cwd

        candidates = derive_project_candidates_from_cwd(str(cwd_path))
        for cand in candidates:
            if resolve_conventional_state_dir(cand) is not None:
                effective_project = cand
                break
        if effective_project is None and candidates:
            effective_project = candidates[0]

    if effective_project is None:
        report.checks.append(
            CheckResult("state_dir", "skip", "no project derivable from cwd")
        )
        report.checks.append(
            CheckResult("database", "skip", "no project derivable from cwd")
        )
        report.checks.append(
            check_registry("default", all_projects=all_projects)
            if all_projects
            else CheckResult("registry", "skip", "no project derivable from cwd")
        )
        report.checks.append(
            check_stray_records("default", all_projects=all_projects)
            if all_projects
            else CheckResult(
                "stray_records", "skip", "no project derivable from cwd"
            )
        )
        return report

    report.checks.append(check_state_dir(effective_project))
    report.checks.append(
        check_registry(effective_project, all_projects=all_projects)
    )
    report.checks.append(
        check_stray_records(effective_project, all_projects=all_projects)
    )
    report.checks.append(check_database(effective_project))
    return report


def format_text(report: DoctorReport) -> str:
    icon = {"ok": "✅", "warn": "⚠️ ", "fail": "❌", "skip": "⏭️ "}
    lines = [f"RemaGraph doctor — overall: {report.overall}"]
    for c in report.checks:
        lines.append(f"  {icon[c.status]} {c.name}: {c.message}")
    return "\n".join(lines)
