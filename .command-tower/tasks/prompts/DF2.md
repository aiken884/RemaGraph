# T-RG-DF2：修正 D01 + D02（PPLX 共識）

讀取 `docs/design/reviews/pplx-consensus-actions-2026-07-21.md` 與 PPLX 審查報告。

## 任務
修改：
- `docs/design/01-data-model-arbitration.md`
- `docs/design/02-storage-search-dedup.md`

重點：
- B1 模型與 fail-fast、門檻 0.90 待校準、2000 筆上限、`<f4` endian
- B2 trigram、修正 unicode61 錯誤
- C1 handoff_note 範圍、strip 計長、agent_id 長度
- C5/C6 DDL 修正納入正文（非僅開放問題）
- 開放問題改「已裁決」狀態表
- 去掉與共識衝突的舊建議

禁止改 src。文末 `## PPLX-CONSENSUS-APPLIED`。
