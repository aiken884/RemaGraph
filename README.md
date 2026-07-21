# RemaGraph

> **凡走過必留下痕跡。** RemaGraph 是一把輕量的 MCP 工具，任何 AI coding agent 走過後自然留下的殘跡，後人可循跡。與 CodeGraph 互補：CodeGraph 記「這段程式碼有什麼已知問題」，RemaGraph 記「處理時留下了什麼痕跡」。

## 安裝

```bash
pip install remagraph
```

## MCP 工具

RemaGraph 透過 MCP（stdio transport）暴露三個 tool，相容 Claude Desktop、Cursor 等主流 MCP 客戶端（Unix socket daemon 為進階路線圖）：

- **`remagraph_store`** — agent 寫入記憶（task handoff / status update / discovered constraint），通過五條仲裁規則後寫入 SQLite + FTS5。
- **`remagraph_search`** — agent 查詢記憶，FTS5 BM25 全文檢索 + tag/kind 過濾 + 時間排序。
- **`remagraph_status`** — 查詢專案最新現況，回傳所有 active 的 `status_update` 記憶（以 task_id 去重）。

詳細規格見 [`DESIGN.md`](./DESIGN.md)；對外穩定合約見 [`docs/audit.md`](./docs/audit.md)。

## 授權

Apache-2.0
