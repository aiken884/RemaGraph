# SPDX-License-Identifier: Apache-2.0
"""`remagraph.hooks_installer` 模組本身的 in-process 單元測試。

與 `tests/test_install_hooks_cli.py` 互補、不是取代：那份測試刻意透過
`subprocess.run(...)` 呼叫真正 editable-install 出來的 `remagraph` CLI
執行檔，驗證的是「CLI 外殼本身」的行為（argparse 解析、cli.py 的錯誤輸出、
與真正安裝出來的執行檔互動），因此 coverage.py 天生看不到 subprocess 內部
實際跑過的 hooks_installer.py 程式碼行——這是已知且刻意的取捨（見該檔案
docstring）。

本檔案改用 `from remagraph.hooks_installer import ...` 直接呼叫模組內的
函式（`install_local()` / `install_global()` / `get_bundled_hook_text()`
等），讓 coverage 工具能真正歸因到這些程式碼路徑的執行，同時仍用
`tmp_path` + 真正的 `git init` 建立測試夾具本身（夾具建置透過 subprocess
沒關係，重點是「呼叫 hooks_installer 的函式本身」是 in-process）。

刻意不動、不刪除 hooks_installer.py 本身的任何行為——純粹補測試。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from remagraph import hooks_installer
from remagraph.hooks_installer import (
    CURRENT_FIELDS_SCHEMA_VERSION,
    FIELDS_SCHEMA_VERSION_PREFIX,
    MANAGED_HOOK_MARKER,
    HooksInstallerError,
    get_bundled_hook_text,
    install_global,
    install_local,
)


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=30
    )


def _init_repo(repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    result = _run_git(["init", "-q"], repo_dir)
    assert result.returncode == 0, result.stderr
    _run_git(["config", "user.name", "Test Committer"], repo_dir)
    _run_git(["config", "user.email", "test@example.com"], repo_dir)


# ---------------------------------------------------------------------------
# get_bundled_hook_text()
# ---------------------------------------------------------------------------


def test_get_bundled_hook_text_contains_managed_marker_and_schema_version():
    text = get_bundled_hook_text()

    assert MANAGED_HOOK_MARKER in text
    assert f"{FIELDS_SCHEMA_VERSION_PREFIX} {CURRENT_FIELDS_SCHEMA_VERSION}" in text
    assert text.startswith("#!/usr/bin/env bash\n") or text.startswith("#!")


# ---------------------------------------------------------------------------
# install_local(): 全新安裝
# ---------------------------------------------------------------------------


def test_install_local_fresh_install_creates_executable_marked_hook(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)

    outcome = install_local(cwd=repo)

    hook_path = repo / ".git" / "hooks" / "post-commit"
    assert outcome.action == "installed"
    assert outcome.path == hook_path
    assert hook_path.exists()
    assert MANAGED_HOOK_MARKER in hook_path.read_text(encoding="utf-8")
    mode = os.stat(hook_path).st_mode
    assert mode & 0o111, "安裝完成的 hook 必須是可執行的（明確 chmod 0o755）"
    assert outcome.messages == [], "全新安裝不應有任何額外訊息（無 hooksPath 設定、非升級）"


# ---------------------------------------------------------------------------
# install_local(): 既有非 remagraph 管理的 hook
# ---------------------------------------------------------------------------


def test_install_local_foreign_hook_without_force_raises_and_leaves_file_untouched(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    foreign_hook = hooks_dir / "post-commit"
    original_content = "#!/bin/sh\necho custom-hook\n"
    foreign_hook.write_text(original_content)
    foreign_hook.chmod(0o755)

    with pytest.raises(HooksInstallerError):
        install_local(cwd=repo)

    assert foreign_hook.read_text() == original_content
    backup = hooks_dir / "post-commit.pre-remagraph-backup"
    assert not backup.exists()


def test_install_local_foreign_hook_with_force_backs_up_then_overwrites(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    foreign_hook = hooks_dir / "post-commit"
    original_content = "#!/bin/sh\necho custom-hook\n"
    foreign_hook.write_text(original_content)
    foreign_hook.chmod(0o755)

    outcome = install_local(cwd=repo, force=True)

    assert outcome.action == "force-backed-up-and-installed"
    backup = hooks_dir / "post-commit.pre-remagraph-backup"
    assert backup.exists()
    assert backup.read_text() == original_content
    assert MANAGED_HOOK_MARKER in foreign_hook.read_text()
    mode = os.stat(foreign_hook).st_mode
    assert mode & 0o111


def test_install_local_foreign_hook_force_backup_collision_still_refuses(tmp_path):
    """備份檔名已存在時（先前備份仍在）：即使 --force 也拒絕覆蓋僅存的備份。"""
    repo = tmp_path / "repo"
    _init_repo(repo)
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    foreign_hook = hooks_dir / "post-commit"
    foreign_hook.write_text("#!/bin/sh\necho foreign\n")
    backup = hooks_dir / "post-commit.pre-remagraph-backup"
    backup.write_text("#!/bin/sh\necho precious-old-backup\n")

    with pytest.raises(HooksInstallerError):
        install_local(cwd=repo, force=True)

    assert backup.read_text() == "#!/bin/sh\necho precious-old-backup\n"
    assert foreign_hook.read_text() == "#!/bin/sh\necho foreign\n"


# ---------------------------------------------------------------------------
# install_local(): 既有 symlink
# ---------------------------------------------------------------------------


def test_install_local_symlink_without_force_raises_and_leaves_link_intact(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    shared_target = tmp_path / "shared-hook-target.sh"
    shared_target.write_text("#!/bin/sh\necho shared\n")
    shared_target.chmod(0o755)
    link_path = hooks_dir / "post-commit"
    link_path.symlink_to(shared_target)

    with pytest.raises(HooksInstallerError):
        install_local(cwd=repo)

    assert link_path.is_symlink()
    assert link_path.resolve() == shared_target.resolve()
    backup = hooks_dir / "post-commit.pre-remagraph-backup"
    assert not backup.exists()


def test_install_local_symlink_with_force_backs_up_link_itself_not_dereferenced_copy(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    shared_target = tmp_path / "shared-hook-target.sh"
    shared_target.write_text("#!/bin/sh\necho shared\n")
    shared_target.chmod(0o755)
    link_path = hooks_dir / "post-commit"
    link_path.symlink_to(shared_target)

    outcome = install_local(cwd=repo, force=True)

    assert outcome.action == "force-replaced-symlink"
    backup = hooks_dir / "post-commit.pre-remagraph-backup"
    assert backup.is_symlink(), "備份必須是符號連結本身，而不是 follow 連結後複製到的內容"
    assert backup.resolve() == shared_target.resolve()
    assert not link_path.is_symlink(), "真正的目標檔案現在應是一般檔案"
    assert MANAGED_HOOK_MARKER in link_path.read_text()


def test_install_local_symlink_force_backup_collision_still_refuses(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    shared_target = tmp_path / "shared-hook-target.sh"
    shared_target.write_text("#!/bin/sh\necho shared\n")
    link_path = hooks_dir / "post-commit"
    link_path.symlink_to(shared_target)
    backup = hooks_dir / "post-commit.pre-remagraph-backup"
    backup.write_text("precious-old-backup\n")

    with pytest.raises(HooksInstallerError):
        install_local(cwd=repo, force=True)

    assert link_path.is_symlink()
    assert backup.read_text() == "precious-old-backup\n"


# ---------------------------------------------------------------------------
# install_local(): 既有 remagraph-managed hook（就地升級）
# ---------------------------------------------------------------------------


def test_install_local_managed_hook_reinstall_is_idempotent_no_backup(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)

    first = install_local(cwd=repo)
    assert first.action == "installed"

    hooks_dir = repo / ".git" / "hooks"
    hook_path = hooks_dir / "post-commit"
    assert hook_path.exists()

    second = install_local(cwd=repo)

    assert second.action == "upgraded"
    backup = hooks_dir / "post-commit.pre-remagraph-backup"
    assert not backup.exists(), "覆蓋自己先前安裝的檔案不應該產生備份"
    assert MANAGED_HOOK_MARKER in hook_path.read_text()


def test_install_local_managed_hook_with_older_fields_schema_version_reports_upgrade(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    stale_hook = hooks_dir / "post-commit"
    stale_hook.write_text(
        "#!/usr/bin/env bash\n"
        f"{MANAGED_HOOK_MARKER}\n"
        f"{FIELDS_SCHEMA_VERSION_PREFIX} 0\n"
        "echo stale\n"
    )
    stale_hook.chmod(0o755)

    outcome = install_local(cwd=repo)

    assert outcome.action == "upgraded"
    backup = hooks_dir / "post-commit.pre-remagraph-backup"
    assert not backup.exists()
    joined = " ".join(outcome.messages)
    assert "fields-schema-version=0" in joined
    assert f"fields-schema-version={CURRENT_FIELDS_SCHEMA_VERSION}" in joined
    expected_marker = f"{FIELDS_SCHEMA_VERSION_PREFIX} {CURRENT_FIELDS_SCHEMA_VERSION}"
    assert expected_marker in stale_hook.read_text()


def test_install_local_managed_hook_without_schema_version_marker_reports_upgrade(tmp_path):
    """既有 remagraph-managed hook 完全沒有 fields-schema-version 標記
    （例如非常舊版安裝的）：old_version 解析為 None，訊息應明確說明「未帶
    版本標記，已升級為目前版本」，而不是靜默略過。
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    stale_hook = hooks_dir / "post-commit"
    stale_hook.write_text(f"#!/usr/bin/env bash\n{MANAGED_HOOK_MARKER}\necho stale-no-version\n")
    stale_hook.chmod(0o755)

    outcome = install_local(cwd=repo)

    assert outcome.action == "upgraded"
    joined = " ".join(outcome.messages)
    assert "未帶欄位 schema 版本標記" in joined
    assert f"fields-schema-version={CURRENT_FIELDS_SCHEMA_VERSION}" in joined


# ---------------------------------------------------------------------------
# core.hooksPath 各種設定情境
# ---------------------------------------------------------------------------


def test_install_local_core_hooks_path_absolute_dir_is_respected(tmp_path):
    """husky 風格設定：core.hooksPath 指向 repo 外的一個既有目錄。hook 應
    裝在那裡，且 core.hooksPath 這個設定值本身不被覆蓋，預設 .git/hooks 內
    不應出現該檔案。
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    husky_dir = tmp_path / ".husky"
    husky_dir.mkdir()
    _run_git(["config", "core.hooksPath", str(husky_dir)], repo)

    outcome = install_local(cwd=repo)

    hook_path = husky_dir / "post-commit"
    assert outcome.path == hook_path
    assert hook_path.exists()
    assert MANAGED_HOOK_MARKER in hook_path.read_text()
    assert any(str(husky_dir) in msg for msg in outcome.messages)

    default_hook = repo / ".git" / "hooks" / "post-commit"
    assert not default_hook.exists()

    cfg = _run_git(["config", "core.hooksPath"], repo)
    assert cfg.stdout.strip() == str(husky_dir)


def test_install_local_relative_core_hooks_path_resolved_against_repo_root_not_cwd(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _run_git(["config", "core.hooksPath", ".githooks"], repo)
    (repo / ".githooks").mkdir()
    subdir = repo / "sub" / "dir"
    subdir.mkdir(parents=True)

    outcome = install_local(cwd=subdir)

    expected = repo / ".githooks" / "post-commit"
    assert outcome.path == expected
    assert expected.exists()
    wrong_dir = subdir / ".githooks"
    assert not wrong_dir.exists(), "相對路徑必須相對 repo root 解析，不是相對 cwd"


def test_install_local_core_hooks_path_pointing_at_nonexistent_dir_is_clean_error(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    missing_dir = repo / "does-not-exist-hooks"
    _run_git(["config", "core.hooksPath", str(missing_dir)], repo)

    with pytest.raises(HooksInstallerError):
        install_local(cwd=repo)

    assert not missing_dir.exists(), "不應靜默建立目錄"


# ---------------------------------------------------------------------------
# worktree 與非 git repo
# ---------------------------------------------------------------------------


def test_install_local_from_within_worktree_targets_main_repo_hooks_dir(tmp_path):
    main_repo = tmp_path / "main-repo"
    _init_repo(main_repo)
    init_commit = _run_git(["commit", "-q", "--allow-empty", "-m", "chore: init"], main_repo)
    assert init_commit.returncode == 0, init_commit.stderr

    worktree_dir = tmp_path / "wt-dir"
    add_worktree = _run_git(
        ["worktree", "add", "-q", "-b", "feat/y", str(worktree_dir)], main_repo
    )
    assert add_worktree.returncode == 0, add_worktree.stderr

    outcome = install_local(cwd=worktree_dir)

    main_hook = main_repo / ".git" / "hooks" / "post-commit"
    assert outcome.path == main_hook
    assert main_hook.exists()
    assert MANAGED_HOOK_MARKER in main_hook.read_text()
    worktree_hook = worktree_dir / ".git" / "hooks" / "post-commit"
    assert not worktree_hook.exists()


def test_install_local_not_a_git_repo_raises_friendly_error(tmp_path):
    plain_dir = tmp_path / "not-a-repo"
    plain_dir.mkdir()

    with pytest.raises(HooksInstallerError, match="git repo"):
        install_local(cwd=plain_dir)


# ---------------------------------------------------------------------------
# install_global()
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    """把 install_global() 用到的 `git config --global` / 預設 templateDir
    導向一個乾淨、隔離的 tmp_path 子目錄，絕對不會動到執行這份測試的這台
    機器上真正的 ~/.gitconfig 或 ~/.local/share/remagraph/git-template。

    與 test_install_hooks_cli.py 的 `_isolated_global_env()` 同一套防禦
    邏輯，這裡改為 monkeypatch 進程本身的環境變數（in-process 呼叫，不經
    subprocess env dict）。

    關鍵補充（血淚教訓，見本測試檔案開發過程）：
    `hooks_installer._DEFAULT_GLOBAL_TEMPLATE_DIR` 是模組載入當下就用真正
    `Path.home()` 算好、寫死的模組級常數（見 hooks_installer.py 頂部），
    載入之後才 monkeypatch HOME 環境變數並不會讓這個常數改變——只
    monkeypatch HOME 會導致「尚未設定 init.templateDir」分支仍然把 hook
    真的寫進這台機器上真正的 `~/.local/share/remagraph/git-template`。
    因此這裡必須額外直接 monkeypatch 模組屬性本身，比照 conftest.py 對
    `db_mod.DEFAULT_STATE_DIR` 的同一套處理方式。
    """
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    for var in ("GIT_CONFIG_GLOBAL", "XDG_CONFIG_HOME", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM"):
        monkeypatch.delenv(var, raising=False)
    fake_default_template_dir = fake_home / ".local" / "share" / "remagraph" / "git-template"
    monkeypatch.setattr(
        hooks_installer, "_DEFAULT_GLOBAL_TEMPLATE_DIR", fake_default_template_dir
    )
    return fake_home


def test_install_global_no_prior_template_dir_sets_config_and_installs(isolated_home):
    outcome = install_global()

    assert outcome.action == "installed"

    cfg = subprocess.run(
        ["git", "config", "--global", "init.templateDir"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert cfg.returncode == 0
    template_dir = Path(cfg.stdout.strip())
    assert template_dir.exists()
    assert str(isolated_home) in str(template_dir), "預設 templateDir 應落在（隔離的）$HOME 底下"

    hook_path = template_dir / "hooks" / "post-commit"
    assert outcome.path == hook_path
    assert hook_path.exists()
    assert MANAGED_HOOK_MARKER in hook_path.read_text()
    assert os.stat(hook_path).st_mode & 0o111

    # 兩條 --global 模式限制說明訊息必須都出現，讓使用者清楚知道範圍與限制。
    joined = " ".join(outcome.messages)
    assert "限制 1" in joined
    assert "限制 2" in joined


def test_install_global_with_existing_template_dir_preserves_other_content(isolated_home):
    existing_template = isolated_home.parent / "my-own-template"
    existing_template.mkdir()
    (existing_template / ".gitignore").write_text("*.log\n")
    set_cfg = subprocess.run(
        ["git", "config", "--global", "init.templateDir", str(existing_template)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert set_cfg.returncode == 0, set_cfg.stderr

    outcome = install_global()

    assert outcome.action == "installed"
    cfg_after = subprocess.run(
        ["git", "config", "--global", "init.templateDir"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert cfg_after.stdout.strip() == str(existing_template), (
        "已存在的 init.templateDir 設定值不應被覆蓋"
    )
    assert (existing_template / ".gitignore").read_text() == "*.log\n", (
        "既有 templateDir 內其他檔案不應被動到"
    )

    hook_path = existing_template / "hooks" / "post-commit"
    assert outcome.path == hook_path
    assert hook_path.exists()
    assert MANAGED_HOOK_MARKER in hook_path.read_text()
    joined = " ".join(outcome.messages)
    assert "偵測到既有的 git config --global init.templateDir 設定" in joined


def test_install_global_reinstall_is_idempotent_upgrade(isolated_home):
    first = install_global()
    assert first.action == "installed"

    second = install_global()

    assert second.action == "upgraded"
    hooks_dir = second.path.parent
    backup = hooks_dir / "post-commit.pre-remagraph-backup"
    assert not backup.exists()
