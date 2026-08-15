# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 跨專案聯絡指揮塔 / 其他專案 agent

需要聯絡其他專案的指揮塔或 agent（例如回報進度、任務交接、詢問問題）時，先查閱 herdr-bridge 指揮塔維護的三層通訊架構使用指南：

```bash
remagraph search --project herdr-bridge --task-id herdr-bridge-three-channel-usage-guide
# 或用跨專案標籤查：
remagraph search --cross-project-label topic:how-to-contact-tower
```

指南涵蓋以下管道的使用時機與範例：

- **Primary**：`remagraph store` / `search`（非同步、持久，任何裝有 remagraph CLI 的 agent 都能用）
- **Secondary**：ACP `dispatch_with_memory_confirm`（herdr-bridge 內部 API，一般用不到）
- **Tertiary**：side-channel `/tmp/tower-reports.sock`（僅限被指揮塔明確派工、拿到 report_sock 路徑者使用）
- **即時互動**：`herdr pane send-text`（送出後務必驗證真的送達，不能假設成功）
