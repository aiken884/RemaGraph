# SPDX-License-Identifier: Apache-2.0
"""從 CHANGELOG.md 抽取指定版本段落作為 GitHub Release notes
（0.7.0 項目 E.3，PPLX 審查定案）。

邊界處理（審查條件）：段落不存在或內容為空（只有標題）時，以非零 exit
讓 workflow 走 fallback（generate_release_notes），並印出可觀測的
::warning:: annotation——絕不靜默 fallback，也絕不讓 publish 失敗。

用法：python extract_changelog.py v0.7.0-beta > release-notes.md
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("::warning::extract_changelog: no tag argument", file=sys.stderr)
        return 1
    tag = sys.argv[1].lstrip("v")  # v0.7.0-beta → 0.7.0-beta
    text = Path("CHANGELOG.md").read_text(encoding="utf-8")

    # 終止邊界（對抗式審查 #2）：除了下一個版本標題（### [x.y.z]），也要
    # 認得任何二級標題（## 繁體中文等雙語區塊）——否則英文區最後一個版本
    # 會夾帶中文區與 link-reference 區。
    pattern = re.compile(
        r"^### \[" + re.escape(tag) + r"\][^\n]*\n(.*?)(?=^### \[|^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if match is None:
        print(
            f"::warning::extract_changelog: no CHANGELOG section for {tag}; "
            "falling back to auto-generated notes",
            file=sys.stderr,
        )
        return 1
    body = match.group(1).strip()
    if not body:
        print(
            f"::warning::extract_changelog: CHANGELOG section for {tag} is "
            "empty; falling back to auto-generated notes",
            file=sys.stderr,
        )
        return 1
    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
