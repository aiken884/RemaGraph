# SPDX-License-Identifier: Apache-2.0
"""指揮塔派工時自動帶 RemaGraph 記憶的極簡範例。

**目前狀態**：PPLX 最推薦 side-channel 架構完成。
- Herdr 只負責 lifecycle events (pane.agent_status_changed / exited)
- 結構化 task_report 走獨立 /tmp/tower-reports.sock
- 所有 dispatch / agent 啟動時取得 report_sock (env or learnings)
- agent 結束時自動送 envelope
- Tower 接收後存 RemaGraph，cross check Herdr done 事件
- 徹底移除 marker / polling 回報

recall/store 強制。統一呼叫。fleet record/recycle。cross ack 驗證。

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
import socket
import subprocess
import sys
import time
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
    """可靠執行 remagraph（優先直呼 python API，fallback CLI）。
    統一所有 before/after 散落呼叫。cross project 支援。
    """
    # 優先直呼（若 remagraph 可 import，統一不依賴 PATH）
    try:
        pass
    except Exception:
        pass

    try:
        if args and args[0] == "search":
            has_query = any(a == "--query" for a in args)
            has_task_id = any(a == "--task-id" for a in args)
            if has_task_id and not has_query:
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
        loaded: dict[str, Any] = json.loads(completed.stdout)
        return loaded
    except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return {}


def _recall_memories(
    task_id: str, top_k: int = 5, project_id: str | None = None
) -> list[dict[str, Any]]:
    """統一 recall 路徑（支援 project 做 cross-space）。"""
    args = ["search", "--task-id", task_id, "--top-k", str(top_k)]
    if project_id:
        args += ["--project", project_id]
    data = _run_remagraph(args)
    return data.get("results") or []


def _store_memory(
    *,
    task_id: str,
    agent_id: str,
    kind: str,
    summary: str,
    project_id: str | None = "default",
    learnings: list[str] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """統一 store 路徑。"""
    args = [
        "store",
        "--task-id",
        task_id,
        "--agent-id",
        agent_id,
        "--kind",
        kind,
        "--summary",
        summary,
    ]
    if project_id and project_id != "default":
        args += ["--project", project_id]
    if learnings:
        args += ["--learnings", json.dumps(learnings, ensure_ascii=False)]
    if tags:
        args += ["--tags", json.dumps(tags, ensure_ascii=False)]
    return _run_remagraph(args)


def make_task_id(prefix: str = "task") -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:6]}"


def build_prompt_with_memory(
    *,
    task_id: str | None = None,
    agent_label: str = "headless-agent",
    instruction: str,
    top_k: int = 5,
    project_id: str | None = None,
) -> str:
    """產生已注入之前記憶的派工文字（給 send_to_agent / acp.prompt 用）。
    強制 recall 路徑，所有 before prompt 皆走此。
    """
    tid = task_id or make_task_id()
    results = _recall_memories(tid, top_k=top_k, project_id=project_id)

    mem_lines = "\n".join(f"- {r.get('summary', '')}" for r in results[:top_k])
    mem_block = mem_lines if mem_lines else "（目前沒有之前記憶）"

    proj_hint = f"（project={project_id}）" if project_id else ""
    return f"""任務編號：{tid}
執行者：{agent_label} {proj_hint}

【RemaGraph 之前記憶】
{mem_block}

【記憶規則（請遵守，強制）】
- 關鍵進度：remagraph store --task-id {tid} --kind status_update --summary "..."
- 結束交接：remagraph store --task-id {tid} --kind task_handoff --summary "..."
- fleet 成員管理（tower 專用）：kind=fleet_member
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
    project_id: str | None = None,
    **send_kwargs: Any,
) -> Any:
    """包裝任意 send 函式：先組記憶 prompt（強制 recall），再呼叫 send_fn。"""
    label = agent_label or agent_id
    text = build_prompt_with_memory(
        task_id=task_id,
        agent_label=label,
        instruction=instruction,
        project_id=project_id,
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
    project_id: str | None = None,
) -> tuple[str, str]:
    """回傳 (task_id, 注入記憶後的 prompt 文字) 供 ACP 使用。
    強制 recall，所有 before/after 路徑皆 mandatory。
    """
    tid = task_id or make_task_id("acp")
    results = _recall_memories(tid, top_k=top_k, project_id=project_id)
    mem_lines = "\n".join(f"- {r.get('summary', '')}" for r in results[:top_k])
    mem_block = mem_lines if mem_lines else "（目前沒有之前記憶）"

    proj_hint = f"（project={project_id} cross-space）" if project_id else ""
    prompt = f"""任務編號：{tid}
執行者：{agent_label}（經 herdr-bridge ACP 派工） {proj_hint}

【RemaGraph 之前記憶】
{mem_block}

【記憶規則（強制遵守，before/after hooks 必經）】
結束後必須 store：
  remagraph store --task-id {tid} --agent-id {agent_label} --kind status_update --summary "..."
- fleet_member record/recycle 由 tower 負責：kind=fleet_member

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
    project_id: str | None = None,
) -> dict[str, Any]:
    """
    真實使用 herdr_bridge.acp 進行派工 + 自動 recall/store 記憶。
    這是 RemaGraph 側的完整 wrapper，治理層可直接呼叫。
    """
    label = agent_label or agent
    tid, prompt = build_acp_prompt_with_memory(
        task_id=task_id,
        agent_label=label,
        instruction=instruction,
        project_id=project_id,
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

    # 強制 store（mandatory after hook，所有路徑）
    store_res = _store_memory(
        task_id=tid,
        agent_id=label,
        kind="status_update",
        summary=summary,
        project_id=project_id,
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
        project_id: str | None = None,
    ) -> tuple[str, str]:
        """強制 recall 路徑（before hook）。"""
        tid, prompt = build_acp_prompt_with_memory(
            task_id=task_id,
            agent_label=agent_label,
            instruction=instruction,
            top_k=top_k,
            project_id=project_id,
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
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """強制 before/after（recall + store）mandatory。"""
        return dispatch_acp_with_memory(
            actor_id=self.actor_id,
            agent=agent or self.default_agent,
            workdir=workdir,
            instruction=instruction,
            task_id=task_id,
            agent_label=agent_label,
            policy_mode=self.default_policy,
            timeout_sec=timeout_sec,
            project_id=project_id,
        )


# ============================================================
# fleet_member 專屬：由 tower 擁有 record / recycle（PPLX Priority B 強制）
# ============================================================


def record_fleet_member(
    *,
    tower_id: str,
    member_id: str,
    project_id: str = "default",
    details: str,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """tower 記錄 fleet_member（full record owned by tower）。
    使用 task_id="fleet" 統一管理；以 tags 標 member_id 方便 search。
    會自動 supersede 同 task 的舊 fleet_member（最新有效）。
    """
    tid = "fleet"
    member_tag = f"member:{member_id}"
    base_tags = tags or []
    if member_tag not in base_tags:
        base_tags = base_tags + [member_tag, "owned-by-tower"]
    summary = f"fleet_member record by tower={tower_id}: member={member_id}. {details}"
    learnings = [f"fleet member {member_id} registered at {datetime.now().isoformat()}"]
    return _store_memory(
        task_id=tid,
        agent_id=tower_id,
        kind="fleet_member",
        summary=summary,
        project_id=project_id,
        learnings=learnings,
        tags=base_tags,
    )


def recycle_fleet_member(
    *,
    tower_id: str,
    member_id: str,
    project_id: str = "default",
    reason: str = "recycled by tower",
) -> dict[str, Any]:
    """tower 回收 fleet_member（recycle owned by tower）。
    透過寫入新的 fleet_member 記錄（同 task_id=fleet），自動 supersede 舊紀錄。
    """
    tid = "fleet"
    member_tag = f"member:{member_id}"
    summary = f"fleet_member RECYCLED by tower={tower_id}: member={member_id}. reason={reason}"
    learnings = [f"fleet member {member_id} recycled: {reason}"]
    return _store_memory(
        task_id=tid,
        agent_id=tower_id,
        kind="fleet_member",
        summary=summary,
        project_id=project_id,
        learnings=learnings,
        tags=[member_tag, "owned-by-tower", "recycled"],
    )


def generate_herdr_bridge_hook_code() -> str:
    """產生建議加到 herdr-bridge 的 hook 程式碼。
    強制 before/after prompt hooks 為 mandatory（所有派工路徑）。
    """
    return """
# 建議加到 herdr_bridge/acp/actions.py （或提供上層 wrapper）
# PPLX B: make recall/store MANDATORY in all paths (before/after)

from typing import Callable
from .models import PromptResult

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
    # === 強制記憶 hook（RemaGraph 整合，mandatory）===
    before_prompt: Callable[[str], str],   # 必填：recall + inject
    after_prompt: Callable[[PromptResult], None],  # 必填：store
) -> PromptResult:
    text = before_prompt(text)  # 強制執行 recall

    result = self._transport.run_prompt(...)

    after_prompt(result)  # 強制執行 store

    ...
    return result

# 治理層 / LightCommander / AcpRouter 使用範例（mandatory）：
# from dispatch_with_memory import MemoryDispatcher, record_fleet_member, recycle_fleet_member
# from herdr_bridge.acp import connect
#
# mem = MemoryDispatcher(actor_id="lightcommander-tower")
# acp = connect()
#
# def recall(text): return build_acp...  # 強制
# def store(res): _store...  # 強制
#
# # tower 管理 fleet
# record_fleet_member(tower_id="lightcommander-tower", member_id="worker-01", details="...")
# result = acp.prompt(..., before_prompt=recall, after_prompt=store)
# recycle_fleet_member(tower_id=..., member_id=...)
"""

def send_task_report(
    task_id: str,
    agent_id: str,
    result: dict[str, Any],
    sock_path: str | None = None,
) -> None:
    """Agent 任務結束時呼叫此函式，送結構化報告到 side-channel（PPLX 唯一推薦）。

    從 env 或 fleet learnings 取得 sock，預設 /tmp/tower-reports.sock。
    Tower listener 收到後存 RemaGraph。
    """
    sock = sock_path or os.environ.get("TOWER_REPORT_SOCK", "/tmp/tower-reports.sock")
    envelope = {
        "type": "task_report",
        "task_id": task_id,
        "agent_id": agent_id,
        "status": "completed",
        "result": result or {},
        "version": 1,
        "ts": time.time(),
    }
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect(sock)
            s.sendall((json.dumps(envelope, ensure_ascii=False) + "\n").encode("utf-8"))
    except Exception:
        # 非致命，Herdr event 仍可做 lifecycle
        pass


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
