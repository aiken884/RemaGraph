# SPDX-License-Identifier: Apache-2.0
"""Regression tests: server.main() 的 argv 分派邊界（--help 修復的同族缺口）。

修復前的行為（診斷確認）：
- `remagraph --version` 落入 serve fallback，印誤導的 project binding 錯誤；
  REMAGRAPH_PROJECT 已設定時甚至直接啟動 MCP stdio server。
- `remagraph --allow-default-state-dir <子命令> ...` 是 build_parser() 文法上
  合法的頂層旗標前置寫法，卻因 argv[1] 不在 cli_commands 而整串被當成
  serve 參數。
- typo 子命令（如 `stroe`）靜默落入 serve fallback，而不是 argparse 的
  invalid choice 錯誤。
- `remagraph serve --project`（末端無值）與 `--project=`（空值）被
  _determine_serve_project_id 靜默忽略，fallback 到 REMAGRAPH_PROJECT，
  使用者以為綁定 A 實際綁到 env 裡的 B。
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

import remagraph
import remagraph.server as server


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("REMAGRAPH_PROJECT", raising=False)
    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)


def _run_main(monkeypatch, argv: list[str]):
    monkeypatch.setattr(sys, "argv", ["remagraph", *argv])
    mcp_run = MagicMock()
    monkeypatch.setattr(server.mcp, "run", mcp_run)
    with pytest.raises(SystemExit) as exc:
        server.main()
    return exc.value.code, mcp_run


def test_version_flag_prints_version_and_exits_zero(monkeypatch, capsys):
    code, mcp_run = _run_main(monkeypatch, ["--version"])
    out = capsys.readouterr().out
    assert code in (0, None)
    assert remagraph.__version__ in out
    mcp_run.assert_not_called()


def test_version_flag_does_not_start_server_even_with_project_env(monkeypatch, capsys):
    monkeypatch.setenv("REMAGRAPH_PROJECT", "some-project")
    bind = MagicMock()
    monkeypatch.setattr(server, "_bind_project", bind)
    code, mcp_run = _run_main(monkeypatch, ["--version"])
    assert code in (0, None)
    bind.assert_not_called()
    mcp_run.assert_not_called()


def test_typo_subcommand_reports_invalid_choice(monkeypatch, capsys):
    """`remagraph stroe ...` 必須得到 argparse 的 invalid choice 錯誤
    （exit 2），而不是靜默落入 serve fallback。"""
    code, mcp_run = _run_main(monkeypatch, ["stroe", "--task-id", "x"])
    err = capsys.readouterr().err
    assert code == 2
    assert "invalid choice" in err
    mcp_run.assert_not_called()


def test_leading_global_flag_is_dispatched_to_cli(monkeypatch, capsys):
    """`remagraph --allow-default-state-dir --help` 是合法的頂層旗標前置
    寫法，必須交給 cli parser 處理而不是 serve fallback。"""
    code, mcp_run = _run_main(monkeypatch, ["--allow-default-state-dir", "--help"])
    out = capsys.readouterr().out
    assert code in (0, None)
    assert "usage:" in out
    mcp_run.assert_not_called()


def test_leading_project_flag_still_reaches_serve_fallback(monkeypatch):
    """向後相容：MCP host 可能設定 `remagraph --project X`（無 serve
    子命令）——這種寫法必須維持歷史的 serve fallback 行為。"""
    run_serve = MagicMock()
    monkeypatch.setattr(server, "_run_serve", run_serve)
    monkeypatch.setattr(sys, "argv", ["remagraph", "--project", "proj-x"])
    server.main()
    run_serve.assert_called_once_with(["--project", "proj-x"])


def test_bare_invocation_still_reaches_serve_fallback(monkeypatch):
    """向後相容：bare `remagraph` 歷史上等同 `remagraph serve`。"""
    run_serve = MagicMock()
    monkeypatch.setattr(server, "_run_serve", run_serve)
    monkeypatch.setattr(sys, "argv", ["remagraph"])
    server.main()
    run_serve.assert_called_once_with([])


def test_serve_project_flag_without_value_fails_loudly(monkeypatch, capsys):
    """`remagraph serve --project`（末端無值）必須明確報錯，不得靜默
    fallback 到 REMAGRAPH_PROJECT。"""
    monkeypatch.setenv("REMAGRAPH_PROJECT", "env-project")
    bind = MagicMock()
    monkeypatch.setattr(server, "_bind_project", bind)
    code, mcp_run = _run_main(monkeypatch, ["serve", "--project"])
    err = capsys.readouterr().err
    assert code not in (0, None)
    assert "--project" in err
    bind.assert_not_called()
    mcp_run.assert_not_called()


def test_serve_project_flag_with_empty_value_fails_loudly(monkeypatch, capsys):
    """`remagraph serve --project=` 同上，必須明確報錯。"""
    monkeypatch.setenv("REMAGRAPH_PROJECT", "env-project")
    bind = MagicMock()
    monkeypatch.setattr(server, "_bind_project", bind)
    code, mcp_run = _run_main(monkeypatch, ["serve", "--project="])
    err = capsys.readouterr().err
    assert code not in (0, None)
    assert "--project" in err
    bind.assert_not_called()
    mcp_run.assert_not_called()
