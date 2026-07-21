# T-RG-DF3：修正 D03 + D04 + D05 + 建 00-index（PPLX 共識）

讀取 `docs/design/reviews/pplx-consensus-actions-2026-07-21.md` 與 PPLX 審查報告。

## 任務
修改：
- `docs/design/03-mcp-server-runtime.md`（B3 stdio 優先、has_more、sanitize、序列化、MCP SDK）
- `docs/design/04-audit-security.md`（fail-fast 模型、rotation DEFER、UTC、單 process）
- `docs/design/05-test-acceptance.md`（stdio 測試、trigram、TDD models、mutmut 範圍）
- **新建** `docs/design/00-index.md`：索引五份設計 + 裁決總表 + Blocking 已處理狀態 + 與 DESIGN.md 差異摘要

禁止改 src。各檔文末 `## PPLX-CONSENSUS-APPLIED`（00-index 可寫完成狀態表）。
