# 修 ruff 錯誤
repo RemaGraph。只修 lint，不改業務語意。

1. 跑 `uv run ruff check .` 看全部錯誤
2. 可 auto-fix 的用 `uv run ruff check . --fix`
3. E501 適當折行或略調 ruff line-length 若計畫允許（優先折行測試字串）
4. 再跑 `uv run ruff check .` 必須 0 error
5. `uv run pytest tests/ -m "not slow" -q` 仍全綠

結尾 ## RUFF DONE
