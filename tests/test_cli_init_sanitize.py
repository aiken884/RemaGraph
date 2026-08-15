# SPDX-License-Identifier: Apache-2.0
"""Regression tests: `remagraph init` 必須拒絕含特殊字元的 project 名。

修復前的行為（診斷實測確認）：目錄名有做字元白名單，但 env.sh 與
project.json 的內容用原始 project 字串手工拼接——
`remagraph init --project 'my"proj$(echo x)'` 會 exit 0 宣告成功，實際產出
無效 JSON 的 project.json 與含命令替換的損毀 env.sh。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from remagraph.cli import main as cli_main


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("REMAGRAPH_PROJECT", raising=False)
    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)


def test_init_rejects_project_name_with_quote(capsys):
    with pytest.raises(SystemExit) as exc:
        cli_main(["init", "--project", 'my"proj$(echo x)'])
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "project" in err.lower()


def test_init_rejects_project_name_with_shell_metacharacters(capsys):
    with pytest.raises(SystemExit) as exc:
        cli_main(["init", "--project", "a;rm -rf /"])
    assert exc.value.code != 0


def test_init_valid_name_writes_loadable_json_and_env(tmp_path):
    cli_main(["init", "--project", "good-name_1"])
    state_dir = Path(tmp_path) / ".local" / "state" / "remagraph-good-name_1"
    meta = json.loads((state_dir / "project.json").read_text(encoding="utf-8"))
    assert meta["project_id"] == "good-name_1"
    assert meta["state_dir"] == str(state_dir)
    env_text = (state_dir / "env.sh").read_text(encoding="utf-8")
    assert f'export REMAGRAPH_STATE_DIR="{state_dir}"' in env_text
    assert 'export REMAGRAPH_PROJECT="good-name_1"' in env_text
