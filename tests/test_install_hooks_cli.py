# SPDX-License-Identifier: Apache-2.0
"""`remagraph install-hooks` CLI 子命令本身的行為驗證（PPLX 架構審查核准的
設計規格逐點對應）：

- 既有非 remagraph 管理的 hook → 預設拒絕，--force 才備份後覆蓋
- 既有 remagraph-managed hook → 就地升級，冪等、不產生備份
- 既有 symlink → 預設拒絕，--force 才備份「符號連結本身」再覆蓋
- 已設定 core.hooksPath 指向另一個既有目錄（husky 風格）→ 裝在那裡，
  不覆蓋 core.hooksPath 這個設定值本身
- core.hooksPath 為相對路徑 → 相對 repo root 解析，而非 cwd
- core.hooksPath 指向不存在的目錄 → 乾淨錯誤，不靜默建立目錄
- 在 linked worktree 底下執行 → 安裝到主 repo 的 hooks 目錄
- 完全不在任何 git repo 內執行 → 乾淨、友善的錯誤，非零 exit，無原始 traceback
- --global（尚未設定 init.templateDir）→ 之後 `git init` 出來的 repo 自動
  帶有可執行、會真的觸發的 hook
- --global（已設定 init.templateDir）→ 既有設定與既有檔案不受影響，
  hook 只是新增進去

全部透過 subprocess 呼叫本 repo 自己 editable install 出來的真正 `remagraph`
執行檔（而非直接呼叫 Python 函式），因為這裡要驗證的正是「CLI 這個外殼本身」
的行為（含 argparse 解析、server.py 的 CLI/MCP 分派、cli.py 的錯誤訊息輸出）。

REMAGRAPH_HOME/REMAGRAPH_STATE_DIR 隔離的必要性：見
tests/test_post_commit_hook_integration.py 模組 docstring 的同一段說明 ——
subprocess 呼叫完全不受 tests/conftest.py 的 in-process monkeypatch 保護，
每個會呼叫真正 `remagraph` 執行檔的測試都必須在自己的 env dict 裡明確設定
這兩個環境變數。

--global 系列測試額外把 HOME 導向一個乾淨、隔離的 tmp_path 子目錄 ——
`git config --global` 與 Path.home() 都會遵循這個 subprocess 專屬的 env
dict，因此絕對不會動到執行這份測試的這台機器上真正的 ~/.gitconfig。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _find_remagraph_bin() -> str | None:
    candidate = Path(sys.executable).parent / "remagraph"
    if candidate.exists():
        return str(candidate)
    return shutil.which("remagraph")


REMAGRAPH_BIN = _find_remagraph_bin()

pytestmark = pytest.mark.skipif(
    REMAGRAPH_BIN is None, reason="需要已安裝的 remagraph CLI 才能驗證 install-hooks 本身的行為"
)


def _run(cmd: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=30
    )


def _install_hooks(
    repo_dir: Path, env: dict[str, str], extra_args: list[str] | None = None
) -> subprocess.CompletedProcess[str]:
    return _run([REMAGRAPH_BIN, "install-hooks", *(extra_args or [])], repo_dir, env)


def _init_repo(
    repo_dir: Path,
    env: dict[str, str],
    name: str = "Test Committer",
    email: str = "test@example.com",
) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "-q"], repo_dir, env)
    _run(["git", "config", "user.name", name], repo_dir, env)
    _run(["git", "config", "user.email", email], repo_dir, env)


@pytest.fixture()
def base_env(tmp_path) -> dict[str, str]:
    env = os.environ.copy()
    env["REMAGRAPH_STATE_DIR"] = str(tmp_path / "remagraph-state")
    env["REMAGRAPH_HOME"] = str(tmp_path / "remagraph-home")
    env.pop("REMAGRAPH_PROJECT", None)
    env.pop("AGENT_ID", None)
    return env


# ---------------------------------------------------------------------------
# 既有非 remagraph 管理的 hook
# ---------------------------------------------------------------------------


def test_existing_foreign_hook_refused_without_force(tmp_path, base_env):
    repo = tmp_path / "repo"
    _init_repo(repo, base_env)
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    foreign_hook = hooks_dir / "post-commit"
    original_content = "#!/bin/sh\necho custom-hook\n"
    foreign_hook.write_text(original_content)
    foreign_hook.chmod(0o755)

    result = _install_hooks(repo, base_env)

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert foreign_hook.read_text() == original_content, "拒絕時不應改動既有檔案"
    backup = hooks_dir / "post-commit.pre-remagraph-backup"
    assert not backup.exists()


def test_existing_foreign_hook_force_backs_up_then_overwrites(tmp_path, base_env):
    repo = tmp_path / "repo"
    _init_repo(repo, base_env)
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    foreign_hook = hooks_dir / "post-commit"
    original_content = "#!/bin/sh\necho custom-hook\n"
    foreign_hook.write_text(original_content)
    foreign_hook.chmod(0o755)

    result = _install_hooks(repo, base_env, ["--force"])

    assert result.returncode == 0, result.stderr
    backup = hooks_dir / "post-commit.pre-remagraph-backup"
    assert backup.exists()
    assert backup.read_text() == original_content
    assert "remagraph-managed-hook" in foreign_hook.read_text()


def test_existing_foreign_hook_force_backup_collision_refuses(tmp_path, base_env):
    """備份檔名已存在時（先前備份仍在）：拒絕覆蓋僅存的備份，即使有 --force。"""
    repo = tmp_path / "repo"
    _init_repo(repo, base_env)
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    foreign_hook = hooks_dir / "post-commit"
    foreign_hook.write_text("#!/bin/sh\necho foreign\n")
    backup = hooks_dir / "post-commit.pre-remagraph-backup"
    backup.write_text("#!/bin/sh\necho precious-old-backup\n")

    result = _install_hooks(repo, base_env, ["--force"])

    assert result.returncode != 0
    assert backup.read_text() == "#!/bin/sh\necho precious-old-backup\n"
    assert foreign_hook.read_text() == "#!/bin/sh\necho foreign\n"


# ---------------------------------------------------------------------------
# 既有 remagraph-managed 的 hook（就地升級，冪等）
# ---------------------------------------------------------------------------


def test_existing_managed_hook_with_older_fields_schema_version_reports_upgrade(
    tmp_path, base_env
):
    """managed-hook marker 與 fields-schema-version marker 是兩個獨立的版本號
    （設計點 5）：既有 hook 已是 remagraph-managed，但欄位推導規則版本比目前
    程式碼舊時，重新安裝應照常就地覆蓋（不需要 --force、不產生備份），且要
    在輸出中明確告知使用者「偵測到較舊版本、已升級」，而不是悄悄覆蓋、
    不發一語。
    """
    repo = tmp_path / "repo"
    _init_repo(repo, base_env)
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    stale_hook = hooks_dir / "post-commit"
    stale_hook.write_text(
        "#!/usr/bin/env bash\n"
        "# remagraph-managed-hook v1\n"
        "# remagraph-fields-schema-version: 0\n"
        "echo stale\n"
    )
    stale_hook.chmod(0o755)

    result = _install_hooks(repo, base_env)

    assert result.returncode == 0, result.stderr
    backup = hooks_dir / "post-commit.pre-remagraph-backup"
    assert not backup.exists(), "屬於我們自己管理的 hook，就地升級不需要備份"
    from remagraph.hooks_installer import CURRENT_FIELDS_SCHEMA_VERSION

    assert "fields-schema-version=0" in result.stdout
    assert f"fields-schema-version={CURRENT_FIELDS_SCHEMA_VERSION}" in result.stdout
    assert (
        f"# remagraph-fields-schema-version: {CURRENT_FIELDS_SCHEMA_VERSION}"
        in stale_hook.read_text()
    )


def test_existing_managed_hook_reinstall_is_idempotent_no_backup(tmp_path, base_env):
    repo = tmp_path / "repo"
    _init_repo(repo, base_env)
    first = _install_hooks(repo, base_env)
    assert first.returncode == 0, first.stderr

    hooks_dir = repo / ".git" / "hooks"
    hook_path = hooks_dir / "post-commit"
    assert hook_path.exists()

    second = _install_hooks(repo, base_env)

    assert second.returncode == 0, second.stderr
    backup = hooks_dir / "post-commit.pre-remagraph-backup"
    assert not backup.exists(), "覆蓋自己先前安裝的檔案不應該產生備份"
    assert "remagraph-managed-hook" in hook_path.read_text()


# ---------------------------------------------------------------------------
# 既有 symlink
# ---------------------------------------------------------------------------


def test_existing_symlink_refused_without_force(tmp_path, base_env):
    repo = tmp_path / "repo"
    _init_repo(repo, base_env)
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    shared_target = tmp_path / "shared-hook-target.sh"
    shared_target.write_text("#!/bin/sh\necho shared\n")
    shared_target.chmod(0o755)
    link_path = hooks_dir / "post-commit"
    link_path.symlink_to(shared_target)

    result = _install_hooks(repo, base_env)

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert link_path.is_symlink()
    assert link_path.resolve() == shared_target.resolve()
    backup = hooks_dir / "post-commit.pre-remagraph-backup"
    assert not backup.exists()


def test_existing_symlink_force_backs_up_link_itself_not_dereferenced_copy(tmp_path, base_env):
    repo = tmp_path / "repo"
    _init_repo(repo, base_env)
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    shared_target = tmp_path / "shared-hook-target.sh"
    shared_target.write_text("#!/bin/sh\necho shared\n")
    shared_target.chmod(0o755)
    link_path = hooks_dir / "post-commit"
    link_path.symlink_to(shared_target)

    result = _install_hooks(repo, base_env, ["--force"])

    assert result.returncode == 0, result.stderr
    backup = hooks_dir / "post-commit.pre-remagraph-backup"
    assert backup.is_symlink(), "備份必須是符號連結本身，而不是 follow 連結後複製到的內容"
    assert backup.resolve() == shared_target.resolve()
    assert not link_path.is_symlink(), "真正的目標檔案現在應是一般檔案"
    assert "remagraph-managed-hook" in link_path.read_text()


# ---------------------------------------------------------------------------
# core.hooksPath 各種設定情境
# ---------------------------------------------------------------------------


def test_existing_core_hooks_path_absolute_different_dir_is_respected(tmp_path, base_env):
    """模擬 husky 風格的設定：core.hooksPath 指向 repo 外的一個既有目錄。
    hook 應裝在那裡，且 core.hooksPath 這個設定值本身不被覆蓋。
    """
    repo = tmp_path / "repo"
    _init_repo(repo, base_env)
    husky_dir = tmp_path / ".husky"
    husky_dir.mkdir()
    _run(["git", "config", "core.hooksPath", str(husky_dir)], repo, base_env)

    result = _install_hooks(repo, base_env)

    assert result.returncode == 0, result.stderr
    hook_path = husky_dir / "post-commit"
    assert hook_path.exists()
    assert "remagraph-managed-hook" in hook_path.read_text()

    default_hook = repo / ".git" / "hooks" / "post-commit"
    assert not default_hook.exists()

    cfg = _run(["git", "config", "core.hooksPath"], repo, base_env)
    assert cfg.stdout.strip() == str(husky_dir), "core.hooksPath 設定值本身不應被改動"


def test_relative_core_hooks_path_resolved_against_repo_root_not_cwd(tmp_path, base_env):
    repo = tmp_path / "repo"
    _init_repo(repo, base_env)
    _run(["git", "config", "core.hooksPath", ".githooks"], repo, base_env)
    (repo / ".githooks").mkdir()
    subdir = repo / "sub" / "dir"
    subdir.mkdir(parents=True)

    result = _install_hooks(subdir, base_env)

    assert result.returncode == 0, result.stderr
    expected = repo / ".githooks" / "post-commit"
    assert expected.exists()
    wrong_dir = subdir / ".githooks"
    assert not wrong_dir.exists(), "相對路徑必須相對 repo root 解析，不是相對 cwd"


def test_core_hooks_path_pointing_at_nonexistent_dir_is_clean_error(tmp_path, base_env):
    repo = tmp_path / "repo"
    _init_repo(repo, base_env)
    missing_dir = repo / "does-not-exist-hooks"
    _run(["git", "config", "core.hooksPath", str(missing_dir)], repo, base_env)

    result = _install_hooks(repo, base_env)

    assert result.returncode != 0
    assert not missing_dir.exists(), "不應靜默建立目錄"
    assert "Traceback" not in result.stderr


# ---------------------------------------------------------------------------
# worktree 與非 git repo
# ---------------------------------------------------------------------------


def test_install_hooks_from_within_worktree_targets_main_repo_hooks_dir(tmp_path, base_env):
    main_repo = tmp_path / "main-repo"
    _init_repo(main_repo, base_env)
    init_commit = _run(
        ["git", "commit", "-q", "--allow-empty", "-m", "chore: init"], main_repo, base_env
    )
    assert init_commit.returncode == 0

    worktree_dir = tmp_path / "wt-dir"
    add_worktree = _run(
        ["git", "worktree", "add", "-q", "-b", "feat/y", str(worktree_dir)],
        main_repo,
        base_env,
    )
    assert add_worktree.returncode == 0, add_worktree.stderr

    result = _install_hooks(worktree_dir, base_env)

    assert result.returncode == 0, result.stderr
    main_hook = main_repo / ".git" / "hooks" / "post-commit"
    assert main_hook.exists()
    assert "remagraph-managed-hook" in main_hook.read_text()
    assert str(main_repo.resolve()) in result.stdout, "輸出應顯示實際解析出的安裝路徑（主 repo）"


def test_not_a_git_repo_gives_clean_friendly_error(tmp_path, base_env):
    plain_dir = tmp_path / "not-a-repo"
    plain_dir.mkdir()

    result = _install_hooks(plain_dir, base_env)

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "git repo" in result.stderr


# ---------------------------------------------------------------------------
# --global 模式
# ---------------------------------------------------------------------------


def _search_global(project: str, task_id: str, env: dict[str, str]) -> list[dict]:
    import json

    result = subprocess.run(
        [REMAGRAPH_BIN, "search", "--project", project, "--task-id", task_id],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"remagraph search 失敗: {result.stderr}"
    return json.loads(result.stdout)["results"]


def _isolated_global_env(tmp_path, base_env, home_name: str) -> dict[str, str]:
    fake_home = tmp_path / home_name
    fake_home.mkdir()
    env = dict(base_env)
    env["HOME"] = str(fake_home)
    # 防禦性移除任何可能讓 git 繞過 $HOME 的環境變數，確保這些測試絕對不會
    # 動到執行這份測試的機器上真正的全域 git 設定。
    for var in ("GIT_CONFIG_GLOBAL", "XDG_CONFIG_HOME", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM"):
        env.pop(var, None)
    # model2vec（dedup 語意去重，remagraph store 內部一定會用到）預設把模型
    # 快取在 $HOME/.cache/huggingface。上面把 $HOME 換成一個全新的隔離目錄
    # 後，若不特別處理，會導致 remagraph store 在這裡試圖重新從網路下載模型
    # ——沙盒/CI 環境通常無網路，會整個卡住直到 subprocess timeout（已實測
    # 重現：`git commit` 卡滿 30 秒逾時）。這裡明確把 HF_HOME 指回「執行這份
    # 測試的這台機器」真正的既有快取目錄（重複使用、唯讀，不寫入任何東西），
    # 讓 $HOME 隔離只影響 git 設定/我們自己的 templateDir 預設路徑，不影響
    # model2vec 模型載入。
    env["HF_HOME"] = str(Path.home() / ".cache" / "huggingface")
    return env


def test_global_install_no_prior_template_dir_new_repo_gets_working_hook(tmp_path, base_env):
    env = _isolated_global_env(tmp_path, base_env, "fake-home-fresh")

    result = _run([REMAGRAPH_BIN, "install-hooks", "--global"], tmp_path, env)
    assert result.returncode == 0, result.stderr

    template_cfg = _run(["git", "config", "--global", "init.templateDir"], tmp_path, env)
    assert template_cfg.returncode == 0
    template_dir = Path(template_cfg.stdout.strip())
    assert template_dir.exists()

    template_hook = template_dir / "hooks" / "post-commit"
    assert template_hook.exists()
    assert os.stat(template_hook).st_mode & 0o111, "template 內的 hook 必須是可執行的"

    new_repo = tmp_path / "new-repo-after-global"
    new_repo.mkdir()
    init = _run(["git", "init", "-q"], new_repo, env)
    assert init.returncode == 0, init.stderr

    hook_in_new_repo = new_repo / ".git" / "hooks" / "post-commit"
    assert hook_in_new_repo.exists(), "git init 之後應自動帶有 hook（來自 templateDir）"
    assert os.stat(hook_in_new_repo).st_mode & 0o111

    _run(["git", "config", "user.name", "Tester"], new_repo, env)
    _run(["git", "config", "user.email", "tester@example.com"], new_repo, env)
    (new_repo / "f.txt").write_text("hi\n")
    _run(["git", "add", "f.txt"], new_repo, env)

    commit_env = dict(env)
    commit_env["REMAGRAPH_STATE_DIR"] = str(tmp_path / "state-for-new-repo")
    commit = _run(
        ["git", "commit", "-q", "-m", "feat: first commit via global template"],
        new_repo,
        commit_env,
    )
    assert commit.returncode == 0, commit.stderr

    short_hash = _run(
        ["git", "rev-parse", "--short", "HEAD"], new_repo, commit_env
    ).stdout.strip()
    task_id = f"new-repo-after-global-commit-{short_hash}"
    results = _search_global("new-repo-after-global", task_id, commit_env)
    assert len(results) == 1, f"hook 應已真的觸發並寫回一筆記錄: {results}"


def test_global_install_with_existing_template_dir_preserves_other_content(tmp_path, base_env):
    env = _isolated_global_env(tmp_path, base_env, "fake-home-existing-template")

    existing_template = tmp_path / "my-own-template"
    existing_template.mkdir()
    (existing_template / ".gitignore").write_text("*.log\n")
    set_cfg = _run(
        ["git", "config", "--global", "init.templateDir", str(existing_template)], tmp_path, env
    )
    assert set_cfg.returncode == 0

    result = _run([REMAGRAPH_BIN, "install-hooks", "--global"], tmp_path, env)
    assert result.returncode == 0, result.stderr

    cfg_after = _run(["git", "config", "--global", "init.templateDir"], tmp_path, env)
    assert cfg_after.stdout.strip() == str(existing_template), (
        "已存在的 init.templateDir 設定值不應被覆蓋"
    )
    assert (existing_template / ".gitignore").read_text() == "*.log\n", (
        "既有 templateDir 內其他檔案不應被動到"
    )

    hook_path = existing_template / "hooks" / "post-commit"
    assert hook_path.exists()
    assert "remagraph-managed-hook" in hook_path.read_text()
