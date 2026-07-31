# Governance

RemaGraph is an open-source project under the Apache-2.0 license. This
document describes how decisions are made. It is deliberately lightweight to
match the project's size and pre-1.0 stage, and is expected to grow as the
project and its adopter base grow.

## Roles

- **Users** — anyone using RemaGraph, including the internal orchestration
  systems listed in [`ADOPTERS.md`](ADOPTERS.md). Feedback via issues and
  discussions is the primary input to the roadmap.
- **Contributors** — anyone who submits a pull request, issue, or review.
- **Maintainers** — listed in [`MAINTAINERS.md`](MAINTAINERS.md). They review
  and merge changes, cut releases, and steward the project's direction.

## Decision Making

- **Lazy consensus.** Most decisions happen in issues and pull requests. A
  change with maintainer approval and no sustained objection is accepted.
- **Non-trivial decisions** (breaking changes, new public surface, dependency
  additions, schema migrations, governance changes) are raised as an issue for
  discussion before implementation, so users can weigh in.
- **Pre-1.0, no frozen public API yet.** Unlike a project with a frozen
  surface, RemaGraph is currently pre-1.0 alpha and does not yet have a frozen
  public API — see [`BOUNDARIES.md`](BOUNDARIES.md) for what is and isn't
  considered stable today. This means the MCP tool signatures (`remagraph_store`
  / `remagraph_search` / `remagraph_status`) and CLI subcommands can still
  change. In practice this has meant real behavioral changes going through
  independent review before shipping (e.g. the `project_id`/`state_dir`
  binding fix and the fan-out cap semantics change in 0.3.1-alpha both went
  through an external architecture review before merging). Any change that
  alters the meaning of an existing MCP tool parameter or CLI subcommand's
  behavior must be recorded clearly in `CHANGELOG.md` and go through that same
  kind of independent review before it ships — it is not something a single
  contributor can change unreviewed, even pre-1.0.
- If consensus cannot be reached, the maintainers decide. As the project grows
  to multiple maintainers, contentious decisions are resolved by a simple
  majority of maintainers.

## Becoming a Maintainer

A contributor may be invited to become a maintainer after a sustained track
record of quality contributions and reviews (roughly: several months of
meaningful activity) and agreement of the existing maintainers. New maintainers
are added via a pull request to `MAINTAINERS.md`.

## Vendor Neutrality

RemaGraph is a vendor-neutral MCP (Model Context Protocol) memory server. It
does not privilege any particular AI model vendor or coding-agent tool — any
client that speaks the standard MCP stdio protocol (Claude Code, Codex,
opencode, Cursor, and others) can connect the same way, through `remagraph
serve`. Governance changes will preserve this neutrality: the project will not
be steered to lock users into any single vendor, model, or downstream product.

## Changes to Governance

This document is changed by pull request under the same lazy-consensus
process, with explicit maintainer approval.

---

# 治理方式

RemaGraph 是一個採用 Apache-2.0 授權的開源專案。本文件說明決策如何產生，故意寫
得很輕量，配合專案目前的規模與 pre-1.0 階段；隨著專案與採用者增加，內容也會跟
著擴充。

## 角色

- **使用者（Users）**——任何使用 RemaGraph 的人，包含 [`ADOPTERS.md`](ADOPTERS.md)
  列出的各內部 orchestration 系統。透過 issue 與討論串提供的回饋是 roadmap 最主要的輸入來源。
- **貢獻者（Contributors）**——任何提交 pull request、issue 或 code review 的人。
- **維護者（Maintainers）**——名單見 [`MAINTAINERS.md`](MAINTAINERS.md)。負責審查
  與合併變更、切版本發布，並掌舵專案方向。

## 決策方式

- **懶惰共識（Lazy consensus）。** 多數決策發生在 issue 與 pull request 中。只要
  有維護者核准、且沒有持續性的反對意見，變更就會被接受。
- **非瑣碎變更**（破壞性變更、新增公開介面、新增相依套件、schema 遷移、治理文
  件變更）在動手實作前先開 issue 討論，讓使用者有機會表達意見。
- **Pre-1.0，尚未有凍結的公開 API。** 與已經凍結公開介面的專案不同，RemaGraph
  目前處於 pre-1.0 alpha 階段，還沒有凍結的公開 API——目前哪些部分算穩定、哪些
  還會變動，詳見 [`BOUNDARIES.md`](BOUNDARIES.md)。這代表 MCP tool 簽章
  （`remagraph_store` / `remagraph_search` / `remagraph_status`）與 CLI 子指
  令目前仍可能改動。實務上的作法是：真正的行為變更要先經過獨立審查才能上線
  （例如 0.3.1-alpha 的 `project_id`/`state_dir` 綁定修復，以及 fan-out cap
  語意調整，兩者都是先經過外部架構審查才合併的）。任何會改變既有 MCP tool 參
  數語意、或 CLI 子指令行為的變更，都必須先在 `CHANGELOG.md` 中清楚記錄，並經
  過同類型的獨立審查才能上線——即使是 pre-1.0 階段，也不是單一貢獻者可以未經
  審查就自行變動的事。
- 若無法達成共識，由維護者決定。隨著專案成長到多位維護者，有爭議的決策改由維
  護者簡單多數決解決。

## 如何成為維護者

貢獻者若持續一段時間（大致上：數個月有意義的活動）提交高品質的貢獻與審查，並
獲得現有維護者同意，可獲邀成為維護者。新任維護者透過 PR 加進 `MAINTAINERS.md`。

## 廠商中立性

RemaGraph 是一套廠商中立的 MCP（Model Context Protocol）記憶伺服器。它不偏袒任
何特定 AI 模型廠商或 coding agent 工具——任何講標準 MCP stdio 協定的客戶端
（Claude Code、Codex、opencode、Cursor 等）都能用同樣方式接入 `remagraph
serve`。治理上的任何變更都會維持這份中立性：本專案不會被導向把使用者綁死在任
何單一廠商、模型或下游產品上。

## 治理文件的變更

本文件的變更同樣走懶惰共識流程，並需要維護者明確核准。
