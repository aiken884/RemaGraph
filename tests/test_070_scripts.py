# SPDX-License-Identifier: Apache-2.0
"""CI 腳本測試（對抗式審查修復 #1/#2/#6：兩支腳本先前零測試，
baseline 清空與雙語夾帶正是直接後果）。"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
EXTRACT = REPO / ".github" / "scripts" / "extract_changelog.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# extract_changelog.py
# ---------------------------------------------------------------------------


class TestExtractChangelog:
    def _run(self, tag: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(EXTRACT), tag],
            cwd=cwd, capture_output=True, text=True,
        )

    @pytest.fixture()
    def changelog(self, tmp_path: Path) -> Path:
        (tmp_path / "CHANGELOG.md").write_text(
            "# Changelog\n\n## English\n\n"
            "### [Unreleased]\n\n"
            "### [0.2.0] - 2026-01-02\n\n#### Added\n- feature B\n\n"
            "### [0.1.0] - 2026-01-01\n\n#### Added\n- feature A\n\n"
            "## 繁體中文\n\n中文區內容不得被夾帶\n\n"
            "[Unreleased]: https://example.com/compare\n",
            encoding="utf-8",
        )
        return tmp_path

    def test_extracts_middle_section(self, changelog):
        result = self._run("v0.2.0", changelog)
        assert result.returncode == 0
        assert "feature B" in result.stdout
        assert "feature A" not in result.stdout

    def test_last_english_section_does_not_leak_chinese_or_links(self, changelog):
        """審查 #2：英文區最後一個版本的終止邊界必須認得 `## ` 標題，
        不得夾帶中文區與 link-reference 區。"""
        result = self._run("v0.1.0", changelog)
        assert result.returncode == 0
        assert "feature A" in result.stdout
        assert "繁體中文" not in result.stdout
        assert "中文區內容" not in result.stdout
        assert "https://example.com" not in result.stdout

    def test_missing_section_warns_and_fails(self, changelog):
        result = self._run("v9.9.9", changelog)
        assert result.returncode == 1
        assert "::warning::" in result.stderr

    def test_empty_section_warns_and_fails(self, changelog):
        result = self._run("vUnreleased", changelog)
        assert result.returncode == 1
        assert "::warning::" in result.stderr


# ---------------------------------------------------------------------------
# mutmut_baseline.py（單元測 survived 統計與 baseline 保護）
# ---------------------------------------------------------------------------


class TestMutmutBaseline:
    @pytest.fixture()
    def mod(self):
        return _load(
            REPO / ".github" / "scripts" / "mutmut_baseline.py", "mutmut_baseline"
        )

    def test_failed_mutmut_query_does_not_clobber_baseline(
        self, mod, tmp_path, monkeypatch
    ):
        """審查 #1：mutmut 查詢失敗（returncode 非 0 / cache 缺失）時
        絕不覆寫既有 baseline——delta 防護不得被單次壞輪重置。"""
        baseline = tmp_path / "mutmut-baseline.json"
        baseline.write_text(
            json.dumps({"commit": "old", "survived": {"src/a.py": 5}})
        )
        monkeypatch.setattr(mod, "BASELINE", baseline)
        monkeypatch.setattr(
            mod, "survived_by_module", lambda: (_ for _ in ()).throw(
                RuntimeError("mutmut query failed")
            )
        )
        with pytest.raises(SystemExit):
            mod.main()
        preserved = json.loads(baseline.read_text())
        assert preserved["survived"] == {"src/a.py": 5}, "baseline 被清空"

    def test_zero_survived_module_stays_in_baseline(self, mod, tmp_path, monkeypatch):
        """審查 #1 同構問題：本輪 0 survived 的模組必須以 0 留在 baseline，
        之後 0→N 的退化才會被比對到，不得被當『初始建立』放行。"""
        baseline = tmp_path / "mutmut-baseline.json"
        monkeypatch.setattr(mod, "BASELINE", baseline)
        monkeypatch.setattr(mod, "survived_by_module", lambda: {})
        monkeypatch.setenv("GITHUB_SHA", "abc123")
        monkeypatch.chdir(REPO)  # 讀 pyproject 的 paths_to_mutate
        mod.main()
        saved = json.loads(baseline.read_text())["survived"]
        assert "src/remagraph/store.py" in saved
        assert saved["src/remagraph/store.py"] == 0

    def test_increase_emits_warning(self, mod, tmp_path, monkeypatch, capsys):
        baseline = tmp_path / "mutmut-baseline.json"
        baseline.write_text(
            json.dumps({"commit": "old", "survived": {"src/remagraph/store.py": 2}})
        )
        monkeypatch.setattr(mod, "BASELINE", baseline)
        monkeypatch.setattr(
            mod, "survived_by_module", lambda: {"src/remagraph/store.py": 7}
        )
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        monkeypatch.chdir(REPO)
        mod.main()
        out = capsys.readouterr().out
        assert "::warning::" in out
        assert "+5" in out

    def test_new_module_is_baseline_established_not_warning(
        self, mod, tmp_path, monkeypatch, capsys
    ):
        baseline = tmp_path / "mutmut-baseline.json"
        baseline.write_text(json.dumps({"commit": "old", "survived": {}}))
        monkeypatch.setattr(mod, "BASELINE", baseline)
        monkeypatch.setattr(
            mod, "survived_by_module", lambda: {"src/remagraph/brandnew.py": 3}
        )
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        monkeypatch.chdir(REPO)
        mod.main()
        out = capsys.readouterr().out
        assert "::warning::" not in out
        assert "baseline established" in out
