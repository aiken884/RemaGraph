# SPDX-License-Identifier: Apache-2.0
"""Regression tests: `remagraph --help` / `-h` / `serve --help` 必須顯示說明並
以 exit code 0 結束，絕不能落入 serve 的 fallback 分支（修復前的實際行為：
`server.main()` 只認得 cli_commands 與 "serve"，其餘 argv —— 包括 --help ——
一律被當成 serve 參數送進 _run_serve，撞上 project binding 檢查以 exit 1
報錯，或在 REMAGRAPH_PROJECT 已設定時直接啟動 MCP stdio 迴圈）。
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

import remagraph.server as server


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("REMAGRAPH_PROJECT", raising=False)
    monkeypatch.delenv("REMAGRAPH_STATE_DIR", raising=False)


def _run_main(monkeypatch, argv: list[str]):
    """跑 server.main() 並回傳 (exit_code, mcp_run_mock)。"""
    monkeypatch.setattr(sys, "argv", ["remagraph", *argv])
    mcp_run = MagicMock()
    monkeypatch.setattr(server.mcp, "run", mcp_run)
    with pytest.raises(SystemExit) as exc:
        server.main()
    return exc.value.code, mcp_run


def test_top_level_help_exits_zero_and_prints_usage(monkeypatch, capsys):
    code, mcp_run = _run_main(monkeypatch, ["--help"])
    out = capsys.readouterr().out
    assert code in (0, None)
    assert "usage:" in out
    assert "store" in out
    assert "search" in out
    mcp_run.assert_not_called()


def test_top_level_help_lists_serve_subcommand(monkeypatch, capsys):
    code, _ = _run_main(monkeypatch, ["--help"])
    out = capsys.readouterr().out
    assert code in (0, None)
    assert "serve" in out


def test_short_help_flag(monkeypatch, capsys):
    code, mcp_run = _run_main(monkeypatch, ["-h"])
    out = capsys.readouterr().out
    assert code in (0, None)
    assert "usage:" in out
    mcp_run.assert_not_called()


def test_serve_help_exits_zero_without_project(monkeypatch, capsys):
    """`remagraph serve --help` 不需要 --project 也要能印說明並正常結束。"""
    code, mcp_run = _run_main(monkeypatch, ["serve", "--help"])
    out = capsys.readouterr().out
    assert code in (0, None)
    assert "--project" in out
    mcp_run.assert_not_called()


def test_serve_help_with_project_env_does_not_start_server(monkeypatch, capsys):
    """即使 REMAGRAPH_PROJECT 已設定，serve --help 也只能印說明，
    不得綁定專案或啟動 MCP stdio 迴圈（修復前會直接啟動）。"""
    monkeypatch.setenv("REMAGRAPH_PROJECT", "some-project")
    bind = MagicMock()
    monkeypatch.setattr(server, "_bind_project", bind)
    code, mcp_run = _run_main(monkeypatch, ["serve", "--help"])
    out = capsys.readouterr().out
    assert code in (0, None)
    assert "--project" in out
    bind.assert_not_called()
    mcp_run.assert_not_called()
