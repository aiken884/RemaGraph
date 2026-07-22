# SPDX-License-Identifier: Apache-2.0
"""指揮塔派工時自動帶 RemaGraph 記憶的極簡範例。

**目前狀態**：工具層 + 治理層已就緒（herdr-bridge 已實作 ACP before/after_prompt + on_event hooks；
RemaGraph MemoryDispatcher 完整）。
組織層（herdr-org 正式指揮塔接入）僅設計階段，開發稍後。跨專案溝通使用 ACP。

用法（在您的指揮塔專案中）：
    from dispatch_with_memory import build_prompt_with_memory, dispatch_with_memory

    text = build_prompt_with_memory(
        task_id="fix-login-001",
        agent_label="headless-worker-03",
        instruction="請修復登入失敗的 bug",
    )
    actions.send_to_agent("rule:tower", agent_id, text)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


# 為了直接相容 herdr-bridge 環境，允許動態加入路徑
def _ensure_herdr_bridge_path() -> None:
    candidates = [
        Path("/Users/aikenlin/Projects/herdr-bridge/src"),
        Path(os.environ.get("HERDR_BRIDGE_SRC", "")),
    ]
    for p in candidates:
        if p and p.exists() and str(p) not in sys.path:
            sys.path.insert(0, str(p))
            break


_ensure_herdr_bridge_path()


def _run_remagraph(args: list[str], timeout: int = 30) -> dict[str, Any]:
    """可靠執行 remagraph CLI。
    特別處理 search 必須帶 --query 的情況（task-id 模式仍需提供 query 作為 fallback）。
    """
    try:
        # 自動為 search 補 --query（若只有 task-id）
        if args and args[0] == "search":
            has_query = any(a == "--query" for a in args)
            has_task_id = any(a == "--task-id" for a in args)
            if has_task_id and not has_query:
                # 找 task-id 的值當 query
                try:
                    tid_idx = args.index("--task-id") + 1
                    tid = args[tid_idx]
                    args = args + ["--query", tid]
                except (IndexError, ValueError):
                    args = args + ["--query", "memory-recall"]

        completed = subprocess.run(
            ["remagraph", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if not completed.stdout.strip():
            return {}
        return json.loads(completed.stdout)
    except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return {}


def make_task_id(prefix: str = "task") -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:6]}"


def build_prompt_with_memory(
    *,
    task_id: str | None = None,
    agent_label: str = "headless-agent",
    instruction: str,
    top_k: int = 5,
) -> str:
    """產生已注入之前記憶的派工文字（給 send_to_agent / acp.prompt 用）。"""
    tid = task_id or make_task_id()
    data = _run_remagraph(["search", "--task-id", tid, "--top-k", str(top_k)])
    results = data.get("results") or []

    mem_lines = "\n".join(f"- {r.get('summary', '')}" for r in results[:top_k])
    mem_block = mem_lines if mem_lines else "（目前沒有之前記憶）"

    return f"""任務編號：{tid}
執行者：{agent_label}

【RemaGraph 之前記憶】
{mem_block}

【記憶規則（請遵守）】
- 關鍵進度：remagraph store --task-id {tid} ... --kind status_update --summary "..."
- 結束交接：remagraph store --task-id {tid} ... --kind task_handoff --summary "..."
- 或最簡單：remagraph auto --task-id {tid} --agent-id {agent_label} -- <指令>

現在的任務：
{instruction}
"""


def dispatch_with_memory(
    send_fn: Any,
    *,
    actor_id: str,
    agent_id: str,
    instruction: str,
    task_id: str | None = None,
    agent_label: str | None = None,
    **send_kwargs: Any,
) -> Any:
    """包裝任意 send 函式：先組記憶 prompt，再呼叫 send_fn(actor_id, agent_id, text, ...)。"""
    label = agent_label or agent_id
    text = build_prompt_with_memory(
        task_id=task_id,
        agent_label=label,
        instruction=instruction,
    )
    return send_fn(actor_id, agent_id, text, **send_kwargs)


if __name__ == "__main__":
    # 示範：只印出會送給 agent 的文字
    print(
        build_prompt_with_memory(
            task_id="demo-task-001",
            agent_label="demo-agent",
            instruction="請跑測試並回報結果",
        )
    )

# ============================================================
# ACP 專用派工包裝（新增，2026-07-22）
# 利用 herdr_bridge.acp 直接與 agent 溝通 + RemaGraph 記憶
# ============================================================


def build_acp_prompt_with_memory(
    *,
    task_id: str | None = None,
    agent_label: str = "acp-agent",
    instruction: str,
    top_k: int = 5,
) -> tuple[str, str]:
    """回傳 (task_id, 注入記憶後的 prompt 文字) 供 ACP 使用。"""
    tid = task_id or make_task_id("acp")
    # 注意：目前 remagraph search 仍需 --query，傳 task-id 相關關鍵字即可
    data = _run_remagraph(
        [
            "search",
            "--task-id",
            tid,
            "--query",
            tid,  # 最小可行 query
            "--top-k",
            str(top_k),
        ]
    )
    results = data.get("results") or []
    mem_lines = "\n".join(f"- {r.get('summary', '')}" for r in results[:top_k])
    mem_block = mem_lines if mem_lines else "（目前沒有之前記憶）"

    prompt = f"""任務編號：{tid}
執行者：{agent_label}（經 herdr-bridge ACP 派工）

【RemaGraph 之前記憶】
{mem_block}

【記憶規則（請遵守）】
結束後請用以下指令儲存：
  remagraph store --task-id {tid} --agent-id {agent_label} --kind status_update --summary "..."

現在的任務：
{instruction}
"""
    return tid, prompt


def dispatch_acp_with_memory(
    *,
    actor_id: str = "remagraph-agent",
    agent: str = "opencode",
    workdir: str,
    instruction: str,
    task_id: str | None = None,
    agent_label: str | None = None,
    policy_mode: str = "approve-all",
    timeout_sec: float = 120,
) -> dict:
    """
    真實使用 herdr_bridge.acp 進行派工 + 自動 recall/store 記憶。
    這是 RemaGraph 側的完整 wrapper，治理層可直接呼叫。
    """
    label = agent_label or agent
    tid, prompt = build_acp_prompt_with_memory(
        task_id=task_id,
        agent_label=label,
        instruction=instruction,
    )

    from herdr_bridge.acp import AcpPolicy, connect

    acp = connect()

    try:
        session = acp.ensure_session(
            actor_id=actor_id,
            agent=agent,
            workdir=workdir,
            session_name=f"mem-{tid}",
            policy=AcpPolicy(mode=policy_mode),
        )
    except Exception as e:
        return {
            "task_id": tid,
            "error": f"ensure_session failed: {e}",
            "prompt_preview": prompt[:200],
        }

    result = acp.prompt(
        actor_id=actor_id,
        session_name=session.session_name,
        text=prompt,
        timeout_sec=timeout_sec,
    )

    summary = f"ACP dispatch 完成。stop_reason={result.stop_reason}, reason={result.reason}"
    if result.text:
        summary += f" | 輸出片段: {result.text[:400]}"

    store_res = _run_remagraph(
        [
            "store",
            "--task-id",
            tid,
            "--agent-id",
            label,
            "--kind",
            "status_update",
            "--summary",
            summary,
        ]
    )

    acp.close_session(actor_id, session.session_name)

    return {
        "task_id": tid,
        "session_name": session.session_name,
        "result": {
            "reason": result.reason,
            "stop_reason": result.stop_reason,
            "text_len": len(result.text or ""),
        },
        "memory_stored": store_res,
        "full_text": result.text,
    }


# ============================================================
# 更高階協調 API（給治理層 / 指揮塔使用）
# 目前：工具層 + 治理層已完成；組織層（herdr-org）設計階段，稍後開發
# ============================================================


class MemoryDispatcher:
    """RemaGraph 記憶派工器。
    可包裝 herdr_bridge.acp 或其他 send 函式，自動 recall + store。
    """

    def __init__(
        self,
        *,
        actor_id: str = "remagraph-agent",
        default_agent: str = "opencode",
        default_policy: str = "approve-all",
    ):
        self.actor_id = actor_id
        self.default_agent = default_agent
        self.default_policy = default_policy

    def recall_and_build_prompt(
        self,
        task_id: str | None,
        agent_label: str,
        instruction: str,
        top_k: int = 5,
    ) -> tuple[str, str]:
        tid, prompt = build_acp_prompt_with_memory(
            task_id=task_id, agent_label=agent_label, instruction=instruction, top_k=top_k
        )
        return tid, prompt

    def dispatch_via_acp(
        self,
        workdir: str,
        instruction: str,
        *,
        task_id: str | None = None,
        agent: str | None = None,
        agent_label: str | None = None,
        timeout_sec: float = 120,
    ) -> dict:
        return dispatch_acp_with_memory(
            actor_id=self.actor_id,
            agent=agent or self.default_agent,
            workdir=workdir,
            instruction=instruction,
            task_id=task_id,
            agent_label=agent_label,
            policy_mode=self.default_policy,
            timeout_sec=timeout_sec,
        )


def generate_herdr_bridge_hook_code() -> str:
    """產生建議加到 herdr-bridge 的 hook 程式碼。
    這是用來「告訴 Herdr Bridge 要如何加上 hook」的內容。
    符合 policy-neutral 設計：只加可選 callback，不強迫依賴 RemaGraph。
    """
    return """
# 建議加到 herdr_bridge/acp/actions.py （或提供上層 wrapper）

from typing import Callable, Optional
from .models import PromptResult

# 在 AcpActions class 裡的 prompt() 和 exec_prompt() 加入兩個可選參數：

def prompt(
    self,
    actor_id: str,
    session_name: str,
    text: str,
    *,
    priority: int = 0,
    policy: AcpPolicy | None = None,
    timeout_sec: float = 600,
    on_event: Callable[[AcpEvent], None] | None = None,
    # === 新增：記憶 hook（上層治理層注入）===
    before_prompt: Optional[Callable[[str], str]] = None,   # recall + inject
    after_prompt: Optional[Callable[[PromptResult], None]] = None,  # store
) -> PromptResult:
    if before_prompt:
        text = before_prompt(text)

    result = self._transport.run_prompt(...)

    if after_prompt:
        after_prompt(result)

    ...
    return result

# 同樣加到 exec_prompt()

# 治理層使用範例（在 herdr-org 或指揮塔）：
# from dispatch_with_memory import MemoryDispatcher
# from herdr_bridge.acp import connect
#
# mem = MemoryDispatcher(actor_id="tower-01")
# acp = connect()
#
# def recall(text): ...
# def store(res): ...
#
# result = acp.prompt(..., before_prompt=recall, after_prompt=store)
"""


if __name__ == "__main__":
    # 額外示範：印出 ACP 版本的 prompt（不真正執行）
    tid, p = build_acp_prompt_with_memory(
        task_id="demo-acp-001",
        agent_label="opencode-worker",
        instruction="分析目前目錄結構並列出關鍵檔案",
    )
    print("=== ACP 注入記憶後的 prompt ===")
    print(p)
    print("\n（如要真實執行 dispatch_acp_with_memory，需提供合法隔離 workdir）")
