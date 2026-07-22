# SPDX-License-Identifier: Apache-2.0
"""指揮塔派工時自動帶 RemaGraph 記憶的極簡範例。

用法（在你的指揮塔專案中）：
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
import subprocess
import uuid
from datetime import datetime
from typing import Any


def _run_remagraph(args: list[str], timeout: int = 30) -> dict[str, Any]:
    try:
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
- 關鍵進度：remagraph store --task-id {tid} --agent-id {agent_label} --kind status_update --summary "..."
- 結束交接：remagraph store --task-id {tid} --agent-id {agent_label} --kind task_handoff --summary "..."
- 或最簡單：remagraph auto --task-id {tid} --agent-id {agent_label} -- <你的指令>

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
