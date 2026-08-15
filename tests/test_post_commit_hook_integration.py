# SPDX-License-Identifier: Apache-2.0
"""驗證由 `remagraph install-hooks` 安裝的 post-commit hook：commit 完成後
應自動把摘要寫回 RemaGraph。

移植自另一個專案既有、已驗證過的 5 個整合測試情境，改為
透過新的 `remagraph install-hooks` 子命令安裝 hook（而不是像原本測試那樣手動
複製 hook 檔案），驗證安裝出來的 hook 行為與原本手動複製版本完全一致。

這裡直接透過已安裝好的 `remagraph` CLI（本 repo 自己 editable install 出來
的那份）在獨立的臨時 git repo + 獨立的 REMAGRAPH_STATE_DIR/REMAGRAPH_HOME 下
真的跑一次 `remagraph install-hooks` + `git commit`，然後用 `remagraph search`
讀回資料庫，斷言「commit 後 RemaGraph 裡真的多了一筆對應的 status_update
記錄」，而不是只驗 exit code。

REMAGRAPH_HOME 隔離的必要性：這些測試全部透過 subprocess 呼叫真正安裝好的
`remagraph` CLI，是完全獨立的 OS process，tests/conftest.py 的 autouse
monkeypatch fixture（只對「同一個 process 內」的 pytest 程式碼有效）完全碰
不到它。若不在傳給 subprocess 的 env 裡明確設定 REMAGRAPH_HOME，`remagraph
install-hooks` 本身呼叫路徑上的 `_db.is_using_default_state_dir()` 前置檢查
會嘗試建立/操作這台機器上真正的 ~/.local/state/remagraph/ —— 因此每一個
helper 一律同時設定 REMAGRAPH_STATE_DIR（該次 commit 自己的記憶資料）與
REMAGRAPH_HOME（共用 registry 的落地位置）。
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
    """優先找目前 Python 直譯器同一個 venv 底下的 remagraph 執行檔（editable
    install 出來的那份，保證是本 repo 目前這份原始碼），找不到才退回 PATH。
    """
    candidate = Path(sys.executable).parent / "remagraph"
    if candidate.exists():
        return str(candidate)
    return shutil.which("remagraph")


REMAGRAPH_BIN = _find_remagraph_bin()

pytestmark = pytest.mark.skipif(
    REMAGRAPH_BIN is None, reason="需要已安裝的 remagraph CLI 才能驗證 install-hooks 產生的行為"
)


def _run(cmd: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=30
    )


def _search(project: str, task_id: str, env: dict[str, str]) -> list[dict]:
    result = subprocess.run(
        [REMAGRAPH_BIN, "search", "--project", project, "--task-id", task_id],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"remagraph search 失敗: {result.stderr}"
    payload = json.loads(result.stdout)
    return payload["results"]


def _init_repo(
    repo_dir: Path,
    env: dict[str, str],
    name: str = "Test Committer",
    email: str = "test@example.com",
) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "-q", "--template="], repo_dir, env)
    _run(["git", "config", "user.name", name], repo_dir, env)
    _run(["git", "config", "user.email", email], repo_dir, env)


def _install_hooks(repo_dir: Path, env: dict[str, str]) -> None:
    result = _run([REMAGRAPH_BIN, "install-hooks"], repo_dir, env)
    assert result.returncode == 0, f"install-hooks 失敗: {result.stderr}\n{result.stdout}"


@pytest.fixture()
def base_env(tmp_path) -> dict[str, str]:
    """獨立的 REMAGRAPH_STATE_DIR + REMAGRAPH_HOME，避免測試污染真實資料。"""
    env = os.environ.copy()
    env["REMAGRAPH_STATE_DIR"] = str(tmp_path / "remagraph-state")
    env["REMAGRAPH_HOME"] = str(tmp_path / "remagraph-home")
    env.pop("REMAGRAPH_PROJECT", None)
    env.pop("AGENT_ID", None)
    return env


def test_post_commit_hook_stores_status_update_memory(tmp_path, base_env):
    """一般情境：透過 `remagraph install-hooks` 安裝後 commit，應能在
    RemaGraph 查到對應的 status_update 記錄，project_id 從 repo 目錄名推導，
    agent_id 從 git user.name 推導。
    """
    repo_dir = tmp_path / "sample-project"
    _init_repo(repo_dir, base_env)
    _install_hooks(repo_dir, base_env)

    (repo_dir / "file1.txt").write_text("hello\n")
    _run(["git", "add", "file1.txt"], repo_dir, base_env)
    commit = _run(["git", "commit", "-q", "-m", "fix: short subject line"], repo_dir, base_env)
    assert commit.returncode == 0, f"commit 失敗: {commit.stderr}"

    short_hash = _run(
        ["git", "rev-parse", "--short", "HEAD"], repo_dir, base_env
    ).stdout.strip()
    task_id = f"sample-project-commit-{short_hash}"

    results = _search("sample-project", task_id, base_env)
    assert len(results) == 1, f"預期恰好一筆記錄，實際: {results}"
    record = results[0]
    assert record["kind"] == "status_update"
    assert record["task_id"] == task_id
    assert record["project_id"] == "sample-project"
    assert record["agent_id"] == "test-committer"
    assert "fix: short subject line" in record["summary"]


def test_post_commit_hook_project_id_is_worktree_safe(tmp_path, base_env):
    """在 git worktree 底下 commit 時，project_id 必須是主 repo 的目錄名，
    不能是 worktree 自己的（無關）目錄名 —— 這是章程要求中明確點名的陷阱。

    hook 只需要在主 repo 安裝一次（`remagraph install-hooks` 設計點 3a 的
    承諾：worktree 共用主 repo 的 hooks 目錄，git 原生機制本身即會如此）；
    這裡刻意不在 worktree 目錄下重複執行 install-hooks，驗證這個共用承諾
    本身成立。
    """
    main_repo = tmp_path / "main-repo"
    _init_repo(main_repo, base_env, name="Tower", email="tower@example.com")
    _install_hooks(main_repo, base_env)
    init_commit = _run(
        ["git", "commit", "-q", "--allow-empty", "-m", "chore: init"], main_repo, base_env
    )
    assert init_commit.returncode == 0

    worktree_dir = tmp_path / "some-unrelated-worktree-dirname"
    add_worktree = _run(
        ["git", "worktree", "add", "-q", "-b", "feat/x", str(worktree_dir)],
        main_repo,
        base_env,
    )
    assert add_worktree.returncode == 0, add_worktree.stderr

    (worktree_dir / "f.txt").write_text("content\n")
    _run(["git", "add", "f.txt"], worktree_dir, base_env)
    commit = _run(["git", "commit", "-q", "-m", "feat: add f"], worktree_dir, base_env)
    assert commit.returncode == 0, f"commit 失敗: {commit.stderr}"

    short_hash = _run(
        ["git", "rev-parse", "--short", "HEAD"], worktree_dir, base_env
    ).stdout.strip()
    expected_task_id = f"main-repo-commit-{short_hash}"

    results = _search("main-repo", expected_task_id, base_env)
    assert len(results) == 1, f"預期在主 repo project_id 'main-repo' 下找到記錄: {results}"
    assert results[0]["project_id"] == "main-repo"

    wrong_project_results = _search(
        "some-unrelated-worktree-dirname", expected_task_id, base_env
    )
    assert wrong_project_results == []


def test_post_commit_hook_respects_agent_id_env_override(tmp_path, base_env):
    """AGENT_ID 環境變數應優先於 git config user.name（沿用既有 AGENT_ID 慣例）。"""
    repo_dir = tmp_path / "env-override-project"
    _init_repo(repo_dir, base_env, name="Should Not Be Used")
    _install_hooks(repo_dir, base_env)

    env = dict(base_env)
    env["AGENT_ID"] = "claude-headless-03"

    (repo_dir / "g.txt").write_text("g\n")
    _run(["git", "add", "g.txt"], repo_dir, env)
    commit = _run(["git", "commit", "-q", "-m", "feat: env agent id"], repo_dir, env)
    assert commit.returncode == 0

    short_hash = _run(["git", "rev-parse", "--short", "HEAD"], repo_dir, env).stdout.strip()
    task_id = f"env-override-project-commit-{short_hash}"

    results = _search("env-override-project", task_id, env)
    assert len(results) == 1
    assert results[0]["agent_id"] == "claude-headless-03"


def test_post_commit_hook_pads_short_summary_past_arbitration_minimum(tmp_path, base_env):
    """commit subject 極短時（如單一字元），RemaGraph 的仲裁規則要求
    summary 至少 30 字元；hook 必須確保這種情況下記錄仍然被實際存入，
    而不是被仲裁規則悄悄拒絕。
    """
    repo_dir = tmp_path / "shortsub"
    _init_repo(repo_dir, base_env)
    _install_hooks(repo_dir, base_env)

    (repo_dir / "a.txt").write_text("a\n")
    _run(["git", "add", "a.txt"], repo_dir, base_env)
    commit = _run(["git", "commit", "-q", "-m", "x"], repo_dir, base_env)
    assert commit.returncode == 0

    short_hash = _run(
        ["git", "rev-parse", "--short", "HEAD"], repo_dir, base_env
    ).stdout.strip()
    task_id = f"shortsub-commit-{short_hash}"

    results = _search("shortsub", task_id, base_env)
    assert len(results) == 1, (
        "極短 commit subject 應仍成功寫入（summary 應已補足到仲裁門檻以上），"
        f"實際查無記錄: {results}"
    )
    assert len(results[0]["summary"]) >= 30


def test_post_commit_hook_root_commit_reports_real_changed_files(tmp_path, base_env):
    """repo 的第一個 commit（root commit，沒有 parent）也必須拿到真正的變更
    檔案清單，而不是 fallback 佔位字串。

    `git diff-tree --no-commit-id --name-only -r HEAD` 對 root commit 預設
    印不出任何東西（沒有 parent 可比較），若 hook 沒有明確加上 --root，
    就會誤觸發「無檔案異動」的 fallback，讓每個新 repo 的第一個 commit都
    拿到誤導性的 learnings。
    """
    repo_dir = tmp_path / "root-commit-project"
    _init_repo(repo_dir, base_env)
    _install_hooks(repo_dir, base_env)

    (repo_dir / "f.txt").write_text("first commit content\n")
    _run(["git", "add", "f.txt"], repo_dir, base_env)
    commit = _run(
        ["git", "commit", "-q", "-m", "feat: root commit with real file"], repo_dir, base_env
    )
    assert commit.returncode == 0, f"commit 失敗: {commit.stderr}"

    short_hash = _run(
        ["git", "rev-parse", "--short", "HEAD"], repo_dir, base_env
    ).stdout.strip()
    task_id = f"root-commit-project-commit-{short_hash}"

    results = _search("root-commit-project", task_id, base_env)
    assert len(results) == 1, f"預期恰好一筆記錄，實際: {results}"
    learnings = results[0]["learnings"]
    assert learnings == ["f.txt"], (
        "root commit 應回報真正變更的檔案清單，而不是 fallback 佔位字串: "
        f"{learnings}"
    )


def test_post_commit_hook_merge_commit_reports_real_changed_files(tmp_path, base_env):
    """真正的 merge commit（multiple parents）也必須拿到有意義、非空的變更
    檔案清單，而不是 fallback 佔位字串。

    `git diff-tree --no-commit-id --name-only -r HEAD` 對有多個 parent 的
    commit，在沒有明確指定 diff 表示法（-m/-c/--cc）時預設印不出任何東西，
    若 hook 沒有處理這個情況，就會誤觸發「無檔案異動」的 fallback。

    刻意透過「有衝突、手動解決、再明確 `git commit`」建構這個 merge commit，
    而不是單純 `git merge --no-ff`：實測證實（見 githooks(5) 對 post-commit
    的定義：「invoked by git-commit(1)」，不含 git-merge(1)）一個乾淨、無
    衝突、由 `git merge` 自動完成的合併並不會觸發 post-commit hook —— 唯有
    當合併發生衝突、需要使用者手動解決後再明確執行一次 `git commit` 來完成
    合併時，那次 `git commit` 才會真正觸發 post-commit hook，而其 HEAD
    正是一個 multiple-parent 的 merge commit。這也是實務上最常見、post-commit
    hook 真的會看到 merge commit 的情境。
    """
    repo_dir = tmp_path / "merge-commit-project"
    _init_repo(repo_dir, base_env)
    _install_hooks(repo_dir, base_env)

    (repo_dir / "shared.txt").write_text("line1\n")
    _run(["git", "add", "shared.txt"], repo_dir, base_env)
    init_commit = _run(["git", "commit", "-q", "-m", "chore: base commit"], repo_dir, base_env)
    assert init_commit.returncode == 0, f"commit 失敗: {init_commit.stderr}"

    main_branch = _run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], repo_dir, base_env
    ).stdout.strip()

    checkout_feature = _run(["git", "checkout", "-q", "-b", "feature/x"], repo_dir, base_env)
    assert checkout_feature.returncode == 0, checkout_feature.stderr

    (repo_dir / "shared.txt").write_text("line1\nfeature-change\n")
    _run(["git", "add", "shared.txt"], repo_dir, base_env)
    feature_commit = _run(
        ["git", "commit", "-q", "-m", "feat: feature edits shared"], repo_dir, base_env
    )
    assert feature_commit.returncode == 0, f"commit 失敗: {feature_commit.stderr}"

    checkout_main = _run(["git", "checkout", "-q", main_branch], repo_dir, base_env)
    assert checkout_main.returncode == 0, checkout_main.stderr

    (repo_dir / "shared.txt").write_text("line1\nmain-change\n")
    _run(["git", "add", "shared.txt"], repo_dir, base_env)
    main_commit = _run(
        ["git", "commit", "-q", "-m", "fix: main edits shared"], repo_dir, base_env
    )
    assert main_commit.returncode == 0, f"commit 失敗: {main_commit.stderr}"

    # 這一步預期因衝突而失敗（returncode != 0），故意不 assert returncode。
    _run(
        ["git", "merge", "--no-ff", "-m", "merge: bring in feature/x", "feature/x"],
        repo_dir,
        base_env,
    )

    (repo_dir / "shared.txt").write_text("line1\nresolved-change\n")
    _run(["git", "add", "shared.txt"], repo_dir, base_env)
    resolve_commit = _run(
        ["git", "commit", "-q", "-m", "merge: bring in feature/x (resolved)"], repo_dir, base_env
    )
    assert resolve_commit.returncode == 0, f"解衝突後 commit 失敗: {resolve_commit.stderr}"

    parents = _run(
        ["git", "log", "-1", "--format=%P", "HEAD"], repo_dir, base_env
    ).stdout.strip()
    assert len(parents.split()) == 2, f"預期 HEAD 是恰有兩個 parent 的 merge commit: {parents!r}"

    short_hash = _run(
        ["git", "rev-parse", "--short", "HEAD"], repo_dir, base_env
    ).stdout.strip()
    task_id = f"merge-commit-project-commit-{short_hash}"

    results = _search("merge-commit-project", task_id, base_env)
    assert len(results) == 1, f"預期恰好一筆記錄，實際: {results}"
    learnings = results[0]["learnings"]
    assert learnings == ["shared.txt"], (
        "merge commit 應回報真正變更的檔案清單，而不是 fallback 佔位字串: "
        f"{learnings}"
    )


def test_post_commit_hook_noop_when_remagraph_not_installed(tmp_path, base_env):
    """未安裝 remagraph 時 hook 必須靜默略過：commit 正常成功，
    只在 stderr 印一行提示，不冒出未捕捉的錯誤或阻擋 commit。

    注意：`remagraph install-hooks` 這個安裝步驟本身仍然要用真正的
    REMAGRAPH_BIN 執行（否則根本裝不了 hook）；PATH 限制只套用在後面
    「觸發 hook 執行」的 `git commit` 這一步，模擬「commit 當下環境裡沒有
    remagraph 可執行檔」這個情境。
    """
    repo_dir = tmp_path / "norema"
    _init_repo(repo_dir, base_env)
    _install_hooks(repo_dir, base_env)

    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    for tool in ("git", "bash", "sh", "sed", "tr", "dirname", "basename", "cat", "env"):
        real = shutil.which(tool)
        if real:
            (fake_bin / tool).symlink_to(real)

    env = dict(base_env)
    env["PATH"] = str(fake_bin)

    (repo_dir / "b.txt").write_text("b\n")
    add = _run(["git", "add", "b.txt"], repo_dir, env)
    assert add.returncode == 0
    commit = _run(["git", "commit", "-q", "-m", "chore: no remagraph installed"], repo_dir, env)

    assert commit.returncode == 0, f"remagraph 未安裝時 commit 不應失敗: {commit.stderr}"
    assert "未安裝 remagraph" in commit.stderr
    assert "Traceback" not in commit.stderr
