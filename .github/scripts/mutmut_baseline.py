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


def survived_by_module() -> dict[str, int]:
    result = subprocess.run(
        ["uv", "run", "mutmut", "result-ids", "survived"],
        capture_output=True, text=True,
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
    current = survived_by_module()
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
