# ADR 0001: v2 治理強化與公開準備

**日期**：2026-07-22

**狀態**：Completed

## Context

RemaGraph v1 核心功能已完成（stdio MCP 三 tool、SQLite + FTS5、五條仲裁規則）。決定公開釋出（Apache-2.0 open source）前，需補足治理、安全、可靠度面向的 gap。

## Decision

執行 v2 計畫（`docs/plans/roadmap-v2-blueprint.md`），包含：

- **安全防線**：路徑穿越防禦、Rate limiting、task_id 格式驗證
- **可靠度**：Audit rotation、max_db_size、superseded 清理、Migration 框架
- **治理**：CONTRIBUTING.md、PR template、mypy CI gate、ADR 機制、PyPI publish pipeline

Phase 3 項目（語意搜尋、Unix socket 等）延至 V3。

## Consequences

- 公開前需完成 Phase 1 + Phase 2 共 13 項
- Phase 1 治理文件已完成、程式碼變更進行中
- CI 因 Actions 額度不足暫停，待額度恢復後啟用
