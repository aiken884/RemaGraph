# SPDX-License-Identifier: Apache-2.0
"""全專案診斷（批次 4，hook 部分）的整合回歸測試。

涵蓋兩個診斷發現：
1. post-commit hook 從不解析專案對應的 REMAGRAPH_STATE_DIR：裸環境（未
   export 任何 REMAGRAPH_*）下每次 commit 都寫入失敗；修復後 hook 應自動
   採用 `remagraph init --project <slug>` 建立的 convention 目錄
   （~/.local/state/remagraph-<slug>）。
2. task_id = _slugify("<project>-commit-<hash>") 尾端 64 字元截斷：repo
   目錄名夠長時 `-commit-<hash>` 尾碼被整段截掉，所有 commit 產生相同
   task_id，kind=status_update 的 supersede 邏輯會讓每次 commit 靜默
   覆蓋前一個 commit 的記錄。

測試手法沿用 tests/test_post_commit_hook_integration.py：真的跑
`remagraph install-hooks` + `git commit`，用 `remagraph search` 讀回斷言。
"""

from __future__ import annotations

import json
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
    REMAGRAPH_BIN is None, reason="需要已安裝的 remagraph CLI"
)


def _run(cmd: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=30
    )


def _init_repo(repo_dir: Path, env: dict[str, str]) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "-q"], repo_dir, env)
    _run(["git", "config", "user.name", "Test Committer"], repo_dir, env)
    _run(["git", "config", "user.email", "test@example.com"], repo_dir, env)


def _commit(repo_dir: Path, env: dict[str, str], filename: str, message: str) -> str:
    (repo_dir / filename).write_text("content\n")
    _run(["git", "add", filename], repo_dir, env)
    result = _run(["git", "commit", "-q", "-m", message], repo_dir, env)
    assert result.returncode == 0, f"commit 失敗: {result.stderr}"
    return _run(["git", "rev-parse", "--short", "HEAD"], repo_dir, env).stdout.strip()


def _search_all(project: str, env: dict[str, str]) -> list[dict]:
    result = subprocess.run(
        [
            REMAGRAPH_BIN, "search", "--project", project,
            "--agent-id", "test-committer", "--top-k", "50",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"remagraph search 失敗: {result.stderr}"
    return json.loads(result.stdout)["results"]


@pytest.fixture()
def isolated_env(tmp_path) -> dict[str, str]:
    """完全隔離的 HOME + REMAGRAPH_HOME；REMAGRAPH_STATE_DIR 刻意不設定
    （裸環境），由各測試自行決定。"""
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    Path(env["HOME"]).mkdir()
    env["REMAGRAPH_HOME"] = str(tmp_path / "remagraph-home")
    env.pop("REMAGRAPH_STATE_DIR", None)
    env.pop("REMAGRAPH_PROJECT", None)
    env.pop("AGENT_ID", None)
    return env


def _install_hooks_with_state(repo_dir: Path, env: dict[str, str], tmp_path: Path) -> None:
    """install-hooks 需要一個 state dir 才能跑；只給安裝步驟用，不影響
    之後 commit 時的裸環境。"""
    install_env = dict(env)
    install_env["REMAGRAPH_STATE_DIR"] = str(tmp_path / "install-state")
    result = _run([REMAGRAPH_BIN, "install-hooks"], repo_dir, install_env)
    assert result.returncode == 0, f"install-hooks 失敗: {result.stderr}\n{result.stdout}"


def test_hook_uses_convention_state_dir_after_init(tmp_path, isolated_env):
    """`remagraph init --project <slug>` 過的專案，裸環境 commit 後記憶
    必須寫進該專案的 convention state dir（修復前：每次 commit 都以
    REMAGRAPH_STATE_DIR is not set 失敗，記憶永遠寫不進去）。"""
    repo_dir = tmp_path / "myproj"
    _init_repo(repo_dir, isolated_env)
    _install_hooks_with_state(repo_dir, isolated_env, tmp_path)

    init_result = _run(
        [REMAGRAPH_BIN, "init", "--project", "myproj"], repo_dir, isolated_env
    )
    assert init_result.returncode == 0, f"init 失敗: {init_result.stderr}"
    conv_dir = Path(isolated_env["HOME"]) / ".local" / "state" / "remagraph-myproj"
    assert conv_dir.is_dir()

    short_hash = _commit(repo_dir, isolated_env, "a.txt", "feat: first change")

    search_env = dict(isolated_env)
    search_env["REMAGRAPH_STATE_DIR"] = str(conv_dir)
    results = _search_all("myproj", search_env)
    task_ids = [r["task_id"] for r in results]
    assert any(t.endswith(f"-commit-{short_hash}") for t in task_ids), (
        f"裸環境 commit 的記憶沒有寫進 convention state dir；results={results}"
    )


def test_hook_bare_env_without_init_degrades_with_guidance(tmp_path, isolated_env):
    """完全裸環境（未 init 過）：commit 必須正常完成，且 stderr 給出
    remagraph init 的明確指引，而不是原始的安全閥錯誤訊息。"""
    repo_dir = tmp_path / "uninitproj"
    _init_repo(repo_dir, isolated_env)
    _install_hooks_with_state(repo_dir, isolated_env, tmp_path)

    (repo_dir / "a.txt").write_text("content\n")
    _run(["git", "add", "a.txt"], repo_dir, isolated_env)
    result = _run(["git", "commit", "-q", "-m", "feat: x"], repo_dir, isolated_env)
    assert result.returncode == 0
    assert "remagraph init" in result.stderr, (
        f"裸環境降級時未給出 init 指引: {result.stderr}"
    )


def test_long_repo_name_task_id_keeps_commit_hash_suffix(tmp_path, isolated_env):
    """repo 目錄名長達 60 字元時，task_id 仍必須以 -commit-<hash> 結尾——
    修復前整串 slugify 後截 64 字元，尾碼被截掉，所有 commit 共用同一個
    task_id，後一次 commit 會靜默 supersede 前一次的記錄。"""
    long_name = "a" * 60
    repo_dir = tmp_path / long_name
    _init_repo(repo_dir, isolated_env)
    _install_hooks_with_state(repo_dir, isolated_env, tmp_path)

    init_result = _run(
        [REMAGRAPH_BIN, "init", "--project", long_name], repo_dir, isolated_env
    )
    assert init_result.returncode == 0, f"init 失敗: {init_result.stderr}"
    conv_dir = (
        Path(isolated_env["HOME"]) / ".local" / "state" / f"remagraph-{long_name}"
    )

    hash1 = _commit(repo_dir, isolated_env, "a.txt", "feat: first")
    hash2 = _commit(repo_dir, isolated_env, "b.txt", "feat: second")

    search_env = dict(isolated_env)
    search_env["REMAGRAPH_STATE_DIR"] = str(conv_dir)
    results = _search_all(long_name, search_env)
    active_task_ids = {r["task_id"] for r in results if r["status"] == "active"}
    assert any(t.endswith(f"-commit-{hash1}") for t in active_task_ids), (
        f"第一次 commit 的記錄遺失（task_id 碰撞被 supersede）: {active_task_ids}"
    )
    assert any(t.endswith(f"-commit-{hash2}") for t in active_task_ids), (
        f"第二次 commit 的記錄遺失: {active_task_ids}"
    )
