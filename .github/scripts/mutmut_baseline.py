# SPDX-License-Identifier: Apache-2.0
"""mutmut delta 防護（0.7.0 項目 B，PPLX 審查條件 B.3）。

讀 mutmut 2.x 的結果快取，統計 per-module 存活 mutant 數，寫成
baseline JSON（含 commit SHA），並與上一輪 baseline 比對：
- 上輪不存在的模組 → 「初始建立」，不告警（審查條件：新增模組邊界）。
- 存活數增加的模組 → GitHub Actions warning annotation ＋ job summary
  （不 fail build——delta gate 是可見性機制，非阻塞）。
"""

from __future__ import annotations

import json
import os
import subprocess
from collections import Counter
from pathlib import Path

BASELINE = Path("mutmut-baseline.json")


def _paths_to_mutate() -> list[str]:
    """讀 pyproject 的 paths_to_mutate，作為 baseline 的完整模組宇集——
    0 survived 的模組必須以 0 留在 baseline（審查 #1 同構問題：否則
    之後 0→N 的退化被當『初始建立』放行）。"""
    try:
        import tomllib

        with open("pyproject.toml", "rb") as f:
            cfg = tomllib.load(f)
        return list(cfg.get("tool", {}).get("mutmut", {}).get("paths_to_mutate", []))
    except Exception:
        return []


def survived_by_module() -> dict[str, int]:
    result = subprocess.run(
        ["uv", "run", "mutmut", "result-ids", "survived"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        # 查詢失敗（mutmut run 崩潰、.mutmut-cache 缺失）不得被當成
        # 「0 survived」——否則會覆寫掉上輪 baseline（審查 #1）
        raise RuntimeError(
            f"mutmut result-ids failed (rc={result.returncode}): "
            f"{result.stderr.strip()[:200]}"
        )
    ids = result.stdout.split()
    counts: Counter[str] = Counter()
    for mid in ids:
        show = subprocess.run(
            ["uv", "run", "mutmut", "show", mid], capture_output=True, text=True
        )
        for line in show.stdout.splitlines():
            if line.startswith("--- "):
                counts[line[4:].split()[0]] += 1
                break
    return dict(counts)


def main() -> None:
    try:
        current = survived_by_module()
    except Exception as e:
        # 保護既有 baseline：查詢失敗時不覆寫、以非零退出讓 workflow 的
        # continue-on-error 記錄失敗（審查 #1——delta 防護不得被單次
        # 壞輪靜默重置）。
        print(f"::warning::mutmut baseline skipped — {e}")
        raise SystemExit(1)
    # 0-survived 模組補 0（完整宇集來自 pyproject）
    for module in _paths_to_mutate():
        current.setdefault(module, 0)
    sha = os.environ.get("GITHUB_SHA", "unknown")

    previous: dict[str, int] = {}
    if BASELINE.exists():
        try:
            previous = json.loads(BASELINE.read_text()).get("survived", {})
        except (OSError, ValueError):
            previous = {}

    lines = [f"## mutmut delta report (commit {sha[:12]})", ""]
    for module, count in sorted(current.items()):
        if module not in previous:
            lines.append(f"- `{module}`: {count} survived (baseline established)")
        elif count > previous[module]:
            delta = count - previous[module]
            print(
                f"::warning::mutmut: surviving mutants in {module} increased "
                f"by {delta} ({previous[module]} → {count})"
            )
            lines.append(
                f"- ⚠️ `{module}`: {previous[module]} → {count} (+{delta})"
            )
        else:
            lines.append(f"- `{module}`: {count} survived (was {previous[module]})")

    BASELINE.write_text(
        json.dumps({"commit": sha, "survived": current}, indent=1)
    )

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))


if __name__ == "__main__":
    main()
