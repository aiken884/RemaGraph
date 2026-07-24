# SPDX-License-Identifier: Apache-2.0
"""`remagraph install-hooks` 的實際安裝邏輯（PPLX 架構審查核准的設計）。

背景：讓任何已安裝 remagraph 套件的專案，都能一行指令啟用「commit 自動把
摘要寫回 RemaGraph」，而不必像過去那樣手動從別的專案複製 hook 檔案。本模組
只負責「把 src/remagraph/hooks/post-commit 這份 package data 正確安裝到目標
git repo（或 git 原生 init.templateDir）」這件事本身，CLI 參數解析/輸出留給
cli.py 的 cmd_install_hooks() 這層薄薄的 wrapper（與本專案既有慣例一致，見
maintenance.py / store.py / search.py 皆是「非瑣碎邏輯獨立成模組，cli.py 只
做參數轉譯與輸出」）。

兩個 marker 的用途刻意分開（見 InstallOutcome / _read_installed_markers）：
- MANAGED_HOOK_MARKER：純粹用來判斷「這個檔案是不是我們安裝的」，決定
  重新安裝時能不能直接覆蓋（不需要備份）。
- FIELDS_SCHEMA_VERSION：獨立追蹤 hook 腳本內欄位推導規則（project_id/
  task_id/agent_id/summary/learnings 怎麼算出來）本身的版本，與「這個檔案
  是不是我們裝的」完全是两回事——未來若推導規則本身改變、需要新版本號，
  不需要連動改managed-hook marker 的版本。
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

# ---------------------------------------------------------------------------
# 常數
# ---------------------------------------------------------------------------

HOOK_FILENAME = "post-commit"
BACKUP_SUFFIX = ".pre-remagraph-backup"

MANAGED_HOOK_MARKER = "# remagraph-managed-hook v1"
FIELDS_SCHEMA_VERSION_PREFIX = "# remagraph-fields-schema-version:"
CURRENT_FIELDS_SCHEMA_VERSION = 1

_FIELDS_SCHEMA_VERSION_RE = re.compile(
    r"^#\s*remagraph-fields-schema-version:\s*(\d+)\s*$", re.MULTILINE
)

_DEFAULT_GLOBAL_TEMPLATE_DIR = Path.home() / ".local" / "share" / "remagraph" / "git-template"

_GLOBAL_MODE_LIMITATIONS = (
    "限制 1：--global 只會影響本指令執行「之後」才新建立（git init / git clone）的"
    "repo；已經存在的 repo 仍須各自在其目錄下執行一次非 --global 的 "
    "`remagraph install-hooks`。",
    "限制 2：不建議在 CI 環境中執行 --global —— CI runner 的 $HOME 可能是暫時性、"
    "或跨 job/repo 共用（尤其自架 self-hosted runner 有可能持續存在到下一個不相關"
    "的 job/repo），有實質的安全疑慮。CI pipeline 應改為對每個 repo、每個 job 各自"
    "明確執行一次非 --global 的 `remagraph install-hooks`。",
)


class HooksInstallerError(RuntimeError):
    """安裝過程中任何預期內、需要清楚告知使用者的錯誤（不是未預期的內部例外）。

    cli.py 的 cmd_install_hooks() 捕捉此例外並印出乾淨訊息、以非零 exit code
    結束——絕不讓使用者看到原始的 subprocess stderr 或 Python traceback。
    """


@dataclass(frozen=True)
class InstallOutcome:
    """一次 install-hooks 呼叫的結果，供 cli.py 組出使用者看到的輸出。"""

    path: Path
    action: str
    messages: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 內部：git 呼叫輔助（一律 capture stderr，絕不讓原始輸出外洩給使用者）
# ---------------------------------------------------------------------------


def _run_git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
    )


def _git_common_dir(cwd: Path) -> Path:
    """回傳目前 repo 的『共用 .git』目錄（所有 worktree 共用，見模組頂部說明）。

    刻意使用 --git-common-dir 而非 --git-dir：後者在 worktree 底下回傳的是
    worktree 自己的（假）.git 目錄，不是主 repo 真正的 hooks 存放位置。
    """
    result = _run_git(["rev-parse", "--git-common-dir"], cwd)
    if result.returncode != 0:
        raise HooksInstallerError(
            "目前目錄不在任何 git repo 內，請在 git repo 的根目錄下執行"
        )
    raw = result.stdout.strip()
    common_dir = Path(raw)
    if not common_dir.is_absolute():
        common_dir = cwd / common_dir
    try:
        return common_dir.resolve()
    except OSError as exc:  # pragma: no cover - 極端環境（權限/符號連結壞掉）
        raise HooksInstallerError(f"無法解析 git-common-dir：{exc}") from exc


def _show_toplevel(cwd: Path) -> Path:
    result = _run_git(["rev-parse", "--show-toplevel"], cwd)
    if result.returncode != 0:
        raise HooksInstallerError(
            "目前目錄不在任何 git repo 內，請在 git repo 的根目錄下執行"
        )
    return Path(result.stdout.strip()).resolve()


def _get_hooks_path_config(cwd: Path) -> str | None:
    result = _run_git(["config", "core.hooksPath"], cwd)
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


# ---------------------------------------------------------------------------
# 內部：bundled hook 內容讀取
# ---------------------------------------------------------------------------


def get_bundled_hook_text() -> str:
    """讀取封裝在 remagraph 套件內的 post-commit hook 腳本原始內容。

    透過 importlib.resources 讀取，不論 remagraph 是以 editable install、
    wheel、或其他任何安裝方式安裝，皆能正確取得（已於實作階段以
    `uv build`/`uv build --wheel` 實際打包並安裝進獨立 venv 驗證過）。
    """
    return resources.files("remagraph").joinpath("hooks", HOOK_FILENAME).read_text(
        encoding="utf-8"
    )


def _parse_fields_schema_version(text: str) -> int | None:
    match = _FIELDS_SCHEMA_VERSION_RE.search(text)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:  # pragma: no cover - regex 已限定為數字
        return None


# ---------------------------------------------------------------------------
# 核心：把 hook 檔案寫進某個目標目錄（3e 的衝突偵測邏輯，local/global 共用）
# ---------------------------------------------------------------------------


def _write_hook_into(target_dir: Path, *, force: bool) -> tuple[str, int | None]:
    """把 bundled hook 寫進 target_dir/post-commit，回傳 (action, 舊版本號)。

    action 取值："installed"（原本不存在）、"upgraded"（偵測到我們自己先前
    安裝的 marker，就地覆蓋、不備份）、"force-replaced-symlink"、
    "force-backed-up-and-installed"。

    永遠在寫入後明確 chmod 0o755（見模組/CLI 說明：package data 的權限位元
    不保證被保留，wheel/zipimport 皆不保證）。
    """
    target = target_dir / HOOK_FILENAME
    content = get_bundled_hook_text()

    if target.is_symlink():
        if not force:
            raise HooksInstallerError(
                f"偵測到 {target} 是符號連結（symlink），可能是其他工具刻意"
                "用來在多個 repo 間共用 hook 檔案。為避免破壞該工具的設定，"
                "預設不會處理，也不會 follow 這個連結去改動它指向的檔案。"
                "若確認要讓 remagraph 接管，請加上 --force"
                "（會先備份這個符號連結本身，而非它指向的內容）。"
            )
        backup = target_dir / (HOOK_FILENAME + BACKUP_SUFFIX)
        if backup.exists() or backup.is_symlink():
            raise HooksInstallerError(
                f"備份檔案 {backup} 已存在，為避免覆蓋僅存的先前備份，"
                "請手動確認/處理該備份後再重試。"
            )
        # os.replace 對符號連結做的是「搬移連結本身」，不會 follow 連結去
        # 複製其指向的內容——備份下來的仍然是一個符號連結。
        os.replace(target, backup)
        target.write_text(content, encoding="utf-8")
        os.chmod(target, 0o755)
        return "force-replaced-symlink", None

    if not target.exists():
        target.write_text(content, encoding="utf-8")
        os.chmod(target, 0o755)
        return "installed", None

    existing_text = target.read_text(encoding="utf-8", errors="replace")

    if MANAGED_HOOK_MARKER in existing_text:
        old_version = _parse_fields_schema_version(existing_text)
        target.write_text(content, encoding="utf-8")
        os.chmod(target, 0o755)
        return "upgraded", old_version

    if not force:
        raise HooksInstallerError(
            f"{target} 已存在，且不是由 remagraph 安裝（找不到管理 marker），"
            "可能是其他工具或使用者自行撰寫的 hook。為避免覆蓋，預設不會處理。"
            "請手動確認內容後自行合併/移除，或加上 --force 讓 remagraph 先"
            f"備份成 {target.name}{BACKUP_SUFFIX} 再覆蓋。"
        )

    backup = target_dir / (HOOK_FILENAME + BACKUP_SUFFIX)
    if backup.exists() or backup.is_symlink():
        raise HooksInstallerError(
            f"備份檔案 {backup} 已存在，為避免覆蓋僅存的先前備份，"
            "請手動確認/處理該備份後再重試。"
        )
    os.replace(target, backup)
    target.write_text(content, encoding="utf-8")
    os.chmod(target, 0o755)
    return "force-backed-up-and-installed", None


def _version_upgrade_message(action: str, old_version: int | None) -> str | None:
    if action != "upgraded":
        return None
    if old_version is None:
        return (
            "偵測到既有的 remagraph-managed hook 未帶欄位 schema 版本標記，"
            f"已升級為目前版本（fields-schema-version={CURRENT_FIELDS_SCHEMA_VERSION}）。"
        )
    if old_version < CURRENT_FIELDS_SCHEMA_VERSION:
        return (
            f"偵測到既有 hook 的欄位推導邏輯版本較舊"
            f"（fields-schema-version={old_version}），"
            f"已升級為目前版本（fields-schema-version={CURRENT_FIELDS_SCHEMA_VERSION}）。"
        )
    return None


# ---------------------------------------------------------------------------
# 公開 API：預設（非 --global）模式
# ---------------------------------------------------------------------------


def install_local(cwd: Path | None = None, *, force: bool = False) -> InstallOutcome:
    """安裝到目前 git repo（見設計點 3）。

    永遠解析到『主 repo』的 hooks 目錄（透過 --git-common-dir），即使目前
    cwd 位在某個 linked worktree 底下，也只會安裝在主 repo 唯一一份，
    對所有 worktree 都生效。
    """
    resolved_cwd = (cwd or Path.cwd()).resolve()
    common_dir = _git_common_dir(resolved_cwd)

    messages: list[str] = []
    hooks_path_raw = _get_hooks_path_config(resolved_cwd)

    if hooks_path_raw:
        configured = Path(hooks_path_raw).expanduser()
        if not configured.is_absolute():
            toplevel = _show_toplevel(resolved_cwd)
            configured = (toplevel / configured).resolve()
        else:
            configured = configured.resolve()
        if not configured.is_dir():
            raise HooksInstallerError(
                f"git config core.hooksPath 目前設定為 {configured}，"
                "但該目錄不存在。為避免掩蓋設定錯誤（例如移除某個 hook 管理"
                "工具後留下的孤兒設定），不會自動建立此目錄，請確認設定是否"
                "正確後再重試。"
            )
        target_dir = configured
        messages.append(f"偵測到 git config core.hooksPath 已設定為 {target_dir}，將安裝於此。")
    else:
        target_dir = common_dir / "hooks"
        target_dir.mkdir(parents=True, exist_ok=True)

    action, old_version = _write_hook_into(target_dir, force=force)
    upgrade_msg = _version_upgrade_message(action, old_version)
    if upgrade_msg:
        messages.append(upgrade_msg)

    return InstallOutcome(path=target_dir / HOOK_FILENAME, action=action, messages=messages)


# ---------------------------------------------------------------------------
# 公開 API：--global 模式
# ---------------------------------------------------------------------------


def install_global(*, force: bool = False) -> InstallOutcome:
    """設定/沿用 git 原生 init.templateDir，讓『之後』新建立的 repo 自動帶有
    此 hook（見設計點 4）。

    - 若尚未設定 init.templateDir：建立 remagraph 自己專屬的目錄並設定該
      config；不影響使用者原本沒有的任何設定。
    - 若已設定：完全不覆蓋這個 config 值本身，只在既有目錄底下的 hooks/
      子目錄新增/更新我們自己的 hook 檔案，保留使用者放在同一個 templateDir
      裡的其他自訂內容（例如 .gitignore/.editorconfig）。
    """
    messages: list[str] = []
    result = _run_git(["config", "--global", "init.templateDir"])
    existing = result.stdout.strip() if result.returncode == 0 else ""

    if existing:
        template_dir = Path(existing).expanduser()
        if not template_dir.is_absolute():
            template_dir = template_dir.resolve()
        messages.append(
            f"偵測到既有的 git config --global init.templateDir 設定：{template_dir}，"
            "將沿用、不覆蓋此設定，只在其 hooks/ 子目錄內加入 remagraph 的 hook。"
        )
    else:
        template_dir = _DEFAULT_GLOBAL_TEMPLATE_DIR
        template_dir.mkdir(parents=True, exist_ok=True)
        set_result = _run_git(
            ["config", "--global", "init.templateDir", str(template_dir)]
        )
        if set_result.returncode != 0:
            raise HooksInstallerError(
                f"設定 git config --global init.templateDir 失敗：{set_result.stderr.strip()}"
            )
        messages.append(f"未偵測到既有 init.templateDir，已設定為：{template_dir}")

    hooks_dir = template_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    action, old_version = _write_hook_into(hooks_dir, force=force)
    upgrade_msg = _version_upgrade_message(action, old_version)
    if upgrade_msg:
        messages.append(upgrade_msg)

    messages.extend(_GLOBAL_MODE_LIMITATIONS)

    return InstallOutcome(path=hooks_dir / HOOK_FILENAME, action=action, messages=messages)
