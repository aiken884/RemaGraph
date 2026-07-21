## 類型 Type

<!-- 請選擇一個（刪除其他） -->

- feat: 新功能
- fix: 錯誤修正
- docs: 文件異動
- chore: 雜項（依賴更新、設定變更等）
- refactor: 重構（無功能變動、無錯誤修正）
- test: 測試

## 是否為 Breaking Change

- [ ] 是
- [ ] 否

---

## 描述 Description

**為什麼這個 PR 是必要的？**

<!-- 說明背景、動機或關聯 issue -->

**做了什麼改動？**

<!-- 條列或摘要說明實作方式 -->

**測試結果**

<!-- 附上相關測試輸出或手動驗證結果 -->

---

## Checklist

- [ ] `ruff check .` 通過
- [ ] `pytest -m "not slow"` 通過
- [ ] `mypy src/` 通過
- [ ] 無新的 secret / credential 引入
- [ ] CHANGELOG 已更新（若適用）
