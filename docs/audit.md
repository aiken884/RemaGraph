# Audit Contract

> 本文件內容逐字複製自 [`DESIGN.md`](../DESIGN.md) 「審計 (Audit)」章節的「Audit Contract」小節。此為 RemaGraph 對外公告的穩定合約，外部排程系統可獨立引用本節，無需閱讀完整設計文件。若合約有變動，SOT 仍是 `DESIGN.md`，本檔案會同步更新。

### Audit Contract（給外部排程系統）

RemaGraph 對外公告的合約（本節可獨立引用）：

- **路徑**：`~/.local/state/remagraph/audit.jsonl`
- **驗證方式**：以 `task_id` 為 key 查 audit，找 `action="remagraph_store"` 且 `status="stored"` 的記錄
- **未寫入的行為**：未找到記錄時，排程系統應自行決定處理策略（例如發 follow-up prompt 提醒 agent、記錄 `memory_write_failed`）
- **schema 變更**：RemaGraph 若修改 audit schema，會在 release note 中公告
