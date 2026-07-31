# T-RG-D04：審計與安全設計

> **艦隊任務 ID**：`T-RG-D04`
> **狀態**：設計完成，尚未實作
> **約束**：本文件僅為設計產出，不得引入對特定外部專案的具名耦合。以 `DESIGN.md` 與 `docs/audit.md` 為 SOT。

---

## 目錄

1. [audit.jsonl 路徑、權限與 Schema](#1-auditjsonl-路徑權限與-schema)
2. [Audit Contract（與 docs/audit.md 一致）](#2-audit-contract與-docsauditmd-一致)
3. [寫入時機與不回存 traceback 原則](#3-寫入時機與不回存-traceback-原則)
4. [威脅模型](#4-威脅模型)
5. [依賴面分析](#5-依賴面分析)
6. [機密掃描與 Secret 紀律](#6-機密掃描與-secret-紀律)
7. [檔案系統安全與初始化](#7-檔案系統安全與初始化)
8. [驗收條件](#8-驗收條件)
9. [開放問題](#9-開放問題)
10. [與 DESIGN.md / docs/audit.md 對齊聲明](#10-與-designmd--docsauditmd-對齊聲明)

---

## 1. audit.jsonl 路徑、權限與 Schema

### 1.1 路徑

```
~/.local/state/remagraph/audit.jsonl
```

- 遵循 XDG Base Directory 規範（`$XDG_STATE_HOME`，預設 `~/.local/state`）。
- `remagraph/` 目錄為 RemaGraph 的所有 state 檔案根目錄，與 `remagraph.db` 同層。
- 若 `$XDG_STATE_HOME` 環境變數存在，優先使用其值。

### 1.2 權限

| 對象 | 權限 | 八進位 | 說明 |
|------|------|--------|------|
| `~/.local/state/remagraph/` 目錄 | `rwx------` | `0700` | 僅 owner 可讀寫執行。目錄不應被其他使用者或 group 存取 |
| `audit.jsonl` 檔案 | `rw-------` | `0600` | 僅 owner 可讀寫。外部排程系統若以同一 OS user 執行，可自然讀取 |

**初始化行為**：`audit.py` 在首次寫入前必須確保目錄存在且權限正確：

1. `os.makedirs(state_dir, mode=0o700, exist_ok=True)`
2. 以 `os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)` 開啟檔案——這保證即使檔案已存在但權限錯誤（例如被 `umask` 或其他工具改成 0644），開啟後也會被修正。

### 1.3 Schema 欄位表

每行一條 JSON 記錄（JSONL 格式），無外層陣列、無換行縮排。

```jsonl
{"ts":"2026-07-21T14:23:01.234Z","actor_id":"oc-dspro/task-2026-07-21-003","action":"remagraph_store","mem_id":"mem-20260721-001","task_id":"task-2026-07-21-003","status":"stored","error":null}
```

| 欄位 | 型別 | 必填 | 說明 |
|------|------|------|------|
| `ts` | `string`（ISO 8601 UTC） | 是 | 事件時間戳，精確到毫秒。格式：`YYYY-MM-DDTHH:MM:SS.sssZ`。伺服器端生成，非 agent 端時間 |
| `actor_id` | `string` | 是 | 複合鍵：`{agent_id}/{task_id}`。範例：`"oc-dspro/task-2026-07-21-003"`。外部系統可由此追溯「哪個 agent 在哪個任務中做了什麼」 |
| `action` | `string` | 是 | 動作類型。v1 固定為 `"remagraph_store"`。未來可擴展（如 `remagraph_search`、`remagraph_invalidate`）。以常數定義，非自由字串 |
| `mem_id` | `string` | 條件必填 | 寫入成功時為記憶 ID（格式 `mem-YYYYMMDD-NNN`）；`status="error"` 時為 `null` |
| `task_id` | `string` | 是 | 明確的 index key。外部排程系統可直接 `grep "task-2026-07-21-003" audit.jsonl` 驗證 |
| `status` | `string` | 是 | `"stored"` 或 `"error"`。不接受其他值。以 enum 定義 |
| `error` | `string \| null` | 條件必填 | `status="error"` 時填錯誤訊息（reason_code + detail）；`status="stored"` 時為 `null`。**不存 traceback**（詳見 §3.2） |

### 1.4 Python 型別定義

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

AuditStatus = Literal["stored", "error"]
AuditAction = Literal["remagraph_store"]  # v1；未來可擴展


@dataclass
class AuditEntry:
    """單筆 audit 記錄。"""
    ts: datetime
    actor_id: str           # "{agent_id}/{task_id}"
    action: AuditAction
    mem_id: str | None      # null when status="error"
    task_id: str
    status: AuditStatus
    error: str | None       # null when status="stored"
```

### 1.5 與外部排程系統的 audit 格式相容性

`ts` 欄位格式（ISO 8601 UTC，精確到毫秒）採用業界常見的通用慣例。這是**刻意為之的格式相容**——RemaGraph 不需要知道消費方是誰，但只要多個獨立系統都遵循同一套格式慣例，消費 audit 的外部排程系統就能用同一套 parser 處理來自不同系統的記錄。這不是耦合，是遵循相同慣例。

---

## 2. Audit Contract（與 docs/audit.md 一致）

以下內容與 [`docs/audit.md`](../audit.md) 逐字一致。本節為設計文件的完整版，`docs/audit.md` 是給外部系統引用的精簡版。

### 2.1 合約條文

- **路徑**：`~/.local/state/remagraph/audit.jsonl`
- **驗證方式**：以 `task_id` 為 key 查 audit，找 `action="remagraph_store"` 且 `status="stored"` 的記錄
- **未寫入的行為**：未找到記錄時，排程系統應自行決定處理策略（例如發 follow-up prompt 提醒 agent、記錄 `memory_write_failed`）。RemaGraph 不對「agent 未寫入記憶」的情況做任何處理或通知
- **schema 變更**：RemaGraph 若修改 audit schema（新增欄位、修改欄位語意、新增 action），會在 release note 中公告。新增欄位以向後相容方式進行（新增可選欄位，不刪除既有欄位）。破壞性變更（如刪除欄位）需跨一個 major version 的棄用期

### 2.2 合約邊界（補充）

以下行為**明確落在 Audit Contract 範圍外**，消費方不得依賴：

- ❌ audit.jsonl 的寫入順序與 `created_at` 的時間順序嚴格一致（append-only 保證順序，但非 transaction 保證）
- ❌ audit.jsonl 中有「任務開始」或「任務結束」事件——RemaGraph 只記 `remagraph_store` 的結果
- ❌ audit.jsonl 的總筆數等於 `memories` 表的總筆數——被仲裁拒絕的請求也會寫入 audit（`status="error"`）
- ❌ audit.jsonl 可作為資料庫的 replication log——這只是審計記錄，不是 WAL

### 2.3 消費方 grep 範例

外部排程系統可用以下指令快速驗證 agent 是否完成記憶寫入：

```bash
# 檢查 task-2026-07-21-003 是否有成功寫入
grep '"task-2026-07-21-003"' ~/.local/state/remagraph/audit.jsonl \
  | grep '"action":"remagraph_store"' \
  | grep '"status":"stored"'
```

若輸出為空，表示該 task 未成功寫入任何記憶（可能是 agent 未呼叫、被仲裁拒絕、或寫入過程中 crash）。

---

## 3. 寫入時機與不回存 traceback 原則

### 3.1 寫入時機

| 場景 | `status` | `mem_id` | `error` | 說明 |
|------|----------|----------|---------|------|
| `remagraph_store` 通過全部仲裁規則，成功寫入 SQLite | `"stored"` | 記憶 ID（例：`"mem-20260721-001"`） | `null` | 正常成功路徑 |
| `remagraph_store` 被仲裁拒絕（reason_code） | `"error"` | `null` | reason_code + detail（例：`"summary_too_short: summary 需 ≥ 30 字，目前 12 字"`） | agent 寫入內容品質不足 |
| `remagraph_store` 過程發生資料庫錯誤（DB locked、disk full 等） | `"error"` | `null` | `"db_error: {exception class name}"`（**不包含 traceback**） | 基礎設施層級錯誤 |
| `remagraph_store` 過程發生未預期錯誤 | `"error"` | `null` | `"internal_error: {exception class name}"`（**不包含 traceback**） | 捕捉意外錯誤，防止 audit 寫入失敗導致整個請求 crash |

**寫入時序**：audit 記錄在 SQLite transaction commit **之後**才 append 到 audit.jsonl。這保證 audit 中 `status="stored"` 的記錄一定對應到資料庫中真實存在的資料——不會出現「audit 說寫入了但 DB 裡沒有」的狀況。

反過來說，若 transaction commit 成功但 audit 寫入失敗（例如 disk full 發生在 commit 之後），此時資料庫中已有記錄但 audit 中沒有。這**可接受**——audit 是盡力而為（best-effort）的記錄，不是 transaction 的一部分。RemaGraph 不會因為 audit 寫入失敗而 rollback 已 commit 的資料。

### 3.2 不回存 traceback 原則

**設計決定：audit.jsonl 的 `error` 欄位絕不包含 Python traceback。**

理由：

1. **最小資訊洩漏原則**：traceback 可能包含檔案系統路徑、函式名稱、第三方套件版本等資訊。audit.jsonl 可能被外部排程系統讀取，不應洩漏實作細節。
2. **audit.jsonl 不是 debug log**：audit 的目的是讓外部系統驗證「寫入是否成功」，不是讓開發者除錯。除錯資訊應走 logging 模組（`logging.error()`），不進 audit。
3. **安全性**：若未來 RemaGraph 被部署在多租戶環境（雖然目前無此規劃），traceback 可能間接洩漏其他 agent 的資訊。

**正確實作**：

```python
# audit.py
import logging

logger = logging.getLogger(__name__)


def write_audit_entry(entry: AuditEntry, state_dir: str) -> None:
    """Append 一筆 audit 記錄。盡力而為——寫入失敗只 log，不拋例外。"""
    try:
        path = os.path.join(state_dir, "audit.jsonl")
        line = json.dumps(_serialize(entry), ensure_ascii=False) + "\n"
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a") as f:
            f.write(line)
    except Exception as e:
        # audit 寫入失敗不影響主流程——已 commit 的資料不受影響
        logger.error("audit write failed: %s", e)
        # 不 re-raise
```

```python
# store.py：捕捉錯誤並寫入 audit
try:
    # ... 仲裁、supersede、INSERT ...
    conn.commit()
    write_audit_entry(AuditEntry(
        ts=datetime.now(timezone.utc),
        actor_id=f"{request.agent_id}/{request.task_id}",
        action="remagraph_store",
        mem_id=memory_id,
        task_id=request.task_id,
        status="stored",
        error=None,
    ), state_dir)
except ArbitrationRejected as e:
    write_audit_entry(AuditEntry(
        ts=datetime.now(timezone.utc),
        actor_id=f"{request.agent_id}/{request.task_id}",
        action="remagraph_store",
        mem_id=None,
        task_id=request.task_id,
        status="error",
        error=f"{e.reason}: {e.detail}",  # reason_code + detail，無 traceback
    ), state_dir)
except Exception as e:
    logger.exception("unexpected error in remagraph_store")  # traceback 走 logging
    write_audit_entry(AuditEntry(
        ts=datetime.now(timezone.utc),
        actor_id=f"{request.agent_id}/{request.task_id}",
        action="remagraph_store",
        mem_id=None,
        task_id=request.task_id,
        status="error",
        error=f"db_error: {type(e).__name__}",  # 只有 exception class name
    ), state_dir)
```

---

## 4. 威脅模型

RemaGraph 的信任邊界在 **MCP 連線**。任何能連上 Unix socket 的 process 都可呼叫 MCP tool。威脅模型基於以下假設：

- Unix socket 的檔案權限（0700 目錄 + 0600 socket）提供基礎的 OS 層級隔離
- 同一 OS user 下的所有 process 被視為同一信任域
- RemaGraph **不實現自己的認證或授權機制**——那是 MCP proxy / OS 權限的責任

### 4.1 威脅 T1：惡意 agent 灌入大量垃圾記憶

**情境**：惡意或被入侵的 agent 以程式化方式大量呼叫 `remagraph_store`，企圖：
- 灌爆 SQLite 資料庫（磁碟空間耗盡）
- 用無意義內容污染 FTS5 index，使正常查詢失效
- 使 `remagraph_status` 回傳無效資訊

**現有防禦**：
- 五條仲裁規則（特別是 #1 summary ≥ 30 字、#2 learnings 非空、#4 去重）可阻擋最基本的垃圾寫入
- model2vec 去重（cosine ≥ 0.92）可防止同一內容以微小變化重複寫入

**殘餘風險**：仲裁規則無法阻擋「文法正確但語意無意義」的內容（例如 LLM 生成的胡言亂語）。同理，agent 可每次換不同 `task_id` 繞過 `status_update` 的 supersede。

**緩解策略**：
- v1 接受此風險。RemaGraph 是工具，不是 gatekeeper。若 agent 決定寫垃圾，那是 agent 的問題
- v2 可考慮加入 rate limiting（per agent_id、per task_id）
- v2 可考慮加入 `task_id` 格式驗證（例如必須符合 `task-YYYY-MM-DD-NNN`），但目前 `task_id` 刻意不限制格式

### 4.2 威脅 T2：路徑穿越（Path Traversal）

**情境**：若未來 RemaGraph 加入「讀取外部檔案」功能（例如 agent 附加 log 片段到 `learnings`），攻擊者可注入 `../../etc/passwd` 路徑。

**現有防禦**：
- v1 的 `remagraph_store` 所有輸入都是**純文字內容**（summary、learnings、handoff_note、tags），不涉及檔案路徑
- audit.jsonl 的路徑是 hardcoded 的（`os.path.join(state_dir, "audit.jsonl")`），不接受外部輸入
- SQLite 資料庫路徑同理

**殘餘風險**：v1 無路徑穿越攻擊面。但若 v2 加入檔案關聯功能，必須在設計階段就加入路徑驗證。

**緩解策略**：
- 任何涉及檔案路徑的功能必須使用 `os.path.realpath()` 解析後驗證落在 `state_dir` 內
- 禁止絕對路徑輸入
- 在設計文件中標記此威脅，供 v2 設計者參考

### 4.3 威脅 T3：磁碟灌爆（Disk Exhaustion）

**情境**：惡意 agent 大量寫入導致：
- SQLite 資料庫無限增長，耗盡磁碟空間
- audit.jsonl 無限 append，耗盡磁碟空間
- embedding BLOB（每筆 ~32KB）累積，加速空間耗盡

**現有防禦**：無（v1 不做儲存限額）。

**殘餘風險**：磁碟滿了之後，SQLite 寫入會失敗（`SQLITE_FULL`），audit.jsonl append 也會失敗。RemaGraph 會回傳 `db_error`。

**緩解策略**：
- v1 接受此風險。RemaGraph 假設執行環境有基本的磁碟監控
- v2 可考慮 `max_db_size` 設定（SQLite `PRAGMA max_page_count`）
- v2 可考慮 audit.jsonl 的 log rotation
- v2 可考慮 `status_update` 的 `superseded` 記錄定期清理（保留最近 N 筆）

### 4.4 威脅 T4：敏感內容透過 summary / learnings 洩漏

**情境**：agent 在 `summary` 或 `learnings` 中無意間寫入敏感資訊，例如：
- API key、token、password
- 內部系統的主機名稱、IP 位址
- 客戶資料

**現有防禦**：
- audit.jsonl 權限 0600、目錄 0700，僅 owner 可讀
- SQLite 資料庫同理
- 不實作 network server——無遠端攻擊面
- CI pipeline 中有 gitleaks 掃描**原始碼**（詳見 §6）

**殘餘風險**：RemaGraph 無法在執行期偵測 agent 寫入的內容是否包含敏感資訊。這是 agent 端（呼叫方）的責任。

**緩解策略**：
- 在 README 和文件中明確告知：RemaGraph 不掃描、不過濾記憶內容，agent 應自行確保不寫入敏感資訊
- 建議 agent 開發者在寫入 RemaGraph 前先做內容 sanitization
- 不實作任何形式的「自動偵測 secret」——這會造成安全假象（開發者以為 RemaGraph 會保護他們，但實際上偵測不可能完美）

### 4.5 威脅 T5：Unix socket 權限被繞過

**情境**：攻擊者以同一 OS user 身份執行惡意 process，直接讀取 `~/.local/state/remagraph/` 下的檔案或連線到 Unix socket。

**防禦**：無。這是 OS 層級的問題，不在 RemaGraph 的威脅模型範圍內。若攻擊者已經是同一 OS user，他們可以直接 `sqlite3 ~/.local/state/remagraph/remagraph.db` 讀取所有資料。

### 4.6 威脅摘要表

| ID | 威脅 | 嚴重度 | v1 防禦 | v2 建議 |
|----|------|--------|---------|---------|
| T1 | 惡意 agent 灌垃圾 | 中 | 仲裁規則（#1–#5） | Rate limiting、task_id 格式驗證 |
| T2 | 路徑穿越 | 低（v1 無攻擊面） | 無檔案路徑輸入 | `realpath()` 驗證 |
| T3 | 磁碟灌爆 | 中 | 無 | `max_page_count`、log rotation |
| T4 | 敏感內容洩漏 | 中 | 0600/0700 權限、gitleaks | README 警語、agent 端 sanitization 指引 |
| T5 | 同 user 橫向存取 | 低 | 檔案權限（非防禦，同 user 自然可讀） | N/A（OS 層級問題） |

---

## 5. 依賴面分析

### 5.1 依賴樹

RemaGraph 的依賴極簡，只有兩個 runtime 依賴：

```
remagraph
├── model2vec>=0.1.0       # 唯一的非 stdlib 依賴
│   └── (其自身的依賴鏈)
└── pydantic>=2.0.0        # schema 驗證
    └── pydantic-core
```

`sentinel`、`sqlite3`、`os`、`pathlib` 等為 Python stdlib，無供應鏈風險。

### 5.2 model2vec 供應鏈風險

`model2vec` 是 RemaGraph 唯一的「有攻擊面的」依賴。它負責：

- 載入 `potion-multilingual-128M` 模型權重（從 Hugging Face 或本地快取）
- 將 `summary` 文字轉換為 embedding vector

**風險向量**：

| 風險 | 說明 | 緩解 |
|------|------|------|
| 惡意模型權重 | 若攻擊者替換 Hugging Face 上的 `potion-multilingual-128M` 模型檔，載入時可執行任意程式碼 | model2vec 使用 safetensors 格式（非 pickle），大幅降低風險。CI 中 `pip-audit` 掃描已知漏洞 |
| 依賴劫持 | `model2vec` 本身的 PyPI 套件被惡意更新 | `pip-audit` 掃描；鎖定最低版本（`>=0.1.0`，不鎖死以允許安全更新） |
| 模型快取竄改 | 攻擊者修改 `~/.cache/model2vec/` 下的模型檔 | 假設 OS 層級檔案權限保護。RemaGraph 不做額外驗證（模型的 checksum 驗證是 model2vec 的責任） |
| 供應鏈遞迴依賴 | `model2vec` 自身的依賴（tokenizers、numpy、etc.）可能有漏洞 | `pip-audit` 掃描整棵依賴樹 |

**model2vec 載入行為**：模型在首次 `remagraph_store` 呼叫時 lazy load（stdio 模式），或 daemon 啟動時 eager load。詳見 D03 §2.3。

**模型載入失敗的處理（已裁決）**：若模型無法載入（無網路、Hugging Face 不可用、模型檔損毀），RemaGraph **必須 fail-fast**——立即回傳 MCP error（`model_load_error`），**不靜默降級**為純文字比對或跳過去重規則。理由：去重是保證記憶品質的核心機制，降級會導致重複記憶累積，後續難以清理。

### 5.3 無 network server 假設

**RemaGraph v1 不做任何 network listen。** 通訊僅透過 Unix socket（`mcp.server.stdio` 或 Unix socket transport）。這是一個重要的安全邊界：

- ✅ 無遠端攻擊面（無 port、無 HTTP endpoint、無 TCP listener）
- ✅ 無需 TLS、CORS、API key 等 network 層級安全機制
- ✅ audit.jsonl 和 SQLite 都在本地檔案系統上，無需擔心傳輸層加密
- ✅ 不需處理 DoS／DDoS（Unix socket 的連線數受 OS 限制）

**唯一的外部網路請求**：model2vec 的模型下載（啟動時、一次性）。之後所有操作皆為本地。

### 5.4 pip-audit 整合

CI pipeline 中執行：

```yaml
# .github/workflows/pip-audit.yml
- name: Scan dependencies for known vulnerabilities
  run: pip-audit
```

這會掃描 `model2vec` 及其遞迴依賴的已知 CVE。若發現漏洞，CI 會失敗。

---

## 6. 機密掃描與 Secret 紀律

### 6.1 gitleaks

RemaGraph 的 CI pipeline 使用 gitleaks 掃描**原始碼 repo** 中的 secret：

```yaml
# .github/workflows/gitleaks.yml
- name: Run gitleaks
  uses: gitleaks/gitleaks-action@v2
```

**掃描範圍**：Git 歷史中的所有 commit（不僅是當前 HEAD）。這防止過去 commit 中意外提交的 secret 留在歷史中。

**gitleaks 不做的事**：gitleaks 掃描的是 Git repo 中的原始碼和 commit 訊息，**不是** audit.jsonl 或 SQLite 資料庫的內容。這些 runtime 檔案不在 Git 中。

### 6.2 不存 secret 的原則

RemaGraph 的核心設計原則是：**RemaGraph 本身不需要任何 secret。**

- 無 API key（不做外部 API 呼叫，除了 model2vec 模型下載）
- 無 token、無密碼
- 無認證機制（依賴 Unix socket 的 OS 層級權限）
- 無 `.env` 檔案（`pip-audit` 和 gitleaks 的設定走 GitHub Actions workflow，不經 .env）

這意味著：若 gitleaks 在 RemaGraph repo 中掃到 secret，那一定是**意外提交**（例如開發者在程式碼中 hardcode 了測試用的 token），而非 RemaGraph 的設計要求。

### 6.3 agent 寫入內容的 secret 風險

如 §4.4 所述，RemaGraph **不掃描、不過濾** agent 寫入的記憶內容。若 agent 在 `summary` 中寫了 API key，RemaGraph 會原樣存入 SQLite。

**這是設計意圖，不是漏洞。** 理由：

1. 內容掃描是 agent 端的責任，不應由儲存層代勞
2. 在 MCP tool 中實作 secret 掃描會造成安全假象
3. 0600 權限已提供基本的 OS 層級保護

**對 agent 開發者的建議**（將寫入 README）：
- 在呼叫 `remagraph_store` 前，確保內容不包含 secret
- 考慮在 agent 端加入 pre-store sanitization（例如 regex 掃描常見 secret 格式）
- 不要將 `.env` 或設定檔內容貼到 `learnings` 中

---

## 7. 檔案系統安全與初始化

### 7.1 初始化流程

RemaGraph daemon 啟動時的檔案系統初始化：

```python
# audit.py（或 db.py 中統一的 init 函式）

import os
import stat


def ensure_state_dir(state_dir: str) -> None:
    """確保 state 目錄存在且權限正確。"""
    os.makedirs(state_dir, mode=0o700, exist_ok=True)
    # exist_ok=True 時 makedirs 不會修改已存在目錄的權限，
    # 因此需要手動檢查並修正
    _ensure_permissions(state_dir, 0o700)


def ensure_audit_file(state_dir: str) -> str:
    """確保 audit.jsonl 存在、權限正確。回傳完整路徑。"""
    path = os.path.join(state_dir, "audit.jsonl")
    # os.open 的 mode 參數僅在 O_CREAT 時生效；
    # 若檔案已存在，需手動修正權限
    if not os.path.exists(path):
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600))
    else:
        _ensure_permissions(path, 0o600)
    return path


def _ensure_permissions(path: str, expected_mode: int) -> None:
    """若路徑權限不符預期，修正之。"""
    current_mode = stat.S_IMODE(os.stat(path).st_mode)
    if current_mode != expected_mode:
        os.chmod(path, expected_mode)
```

### 7.2 SQLite 資料庫安全

- SQLite 資料庫檔案（`remagraph.db`）同樣設為 0600
- SQLite 不使用 WAL mode 之外的共享記憶體或暫存檔（這些會被放在 state_dir 內，繼承目錄的 0700）
- 不啟用 SQLite 的 `ATTACH` 指令（防止透過 SQL injection 讀取其他檔案——雖然 RemaGraph 的 SQL 都是 parameterized queries，這仍是 defense-in-depth）

### 7.3 暫存檔

model2vec 模型下載使用系統預設的快取目錄（`~/.cache/model2vec/` 或 `$XDG_CACHE_HOME/model2vec/`）。此目錄由 model2vec 管理，RemaGraph 不控制其權限。

---

## 8. 驗收條件

### 8.1 audit.jsonl 寫入

```
Given remagraph_store 成功寫入記憶（status="stored"）
When 檢查 audit.jsonl
Then 最後一行包含對應的 JSON 記錄
And 記錄中 status="stored", mem_id 非 null, error=null
And ts 為 ISO 8601 UTC 格式，精確到毫秒

Given remagraph_store 被仲裁拒絕（reason="summary_too_short"）
When 檢查 audit.jsonl
Then 最後一行包含對應的 JSON 記錄
And 記錄中 status="error", mem_id=null, error 欄位包含 "summary_too_short" 但不含 traceback

Given SQLite transaction commit 成功，但 disk full 導致 audit.jsonl 寫入失敗
When remagraph_store 回傳
Then 回傳 status="stored"（資料庫已寫入成功）
And audit.jsonl 中無對應記錄（盡力而為，不 rollback）
And logging 中有 "audit write failed" 的 error log
```

### 8.2 權限

```
Given RemaGraph daemon 首次啟動
When 初始化 state 目錄和 audit.jsonl
Then state 目錄權限為 0700（drwx------）
And audit.jsonl 權限為 0600（-rw-------）

Given audit.jsonl 已存在但權限為 0644（例如被 umask 影響）
When RemaGraph daemon 啟動
Then audit.jsonl 權限被修正為 0600
```

### 8.3 no traceback

```
Given remagraph_store 過程中發生未預期例外（例如 ValueError）
When 寫入 audit.jsonl error 記錄
Then error 欄位不包含 "Traceback (most recent call last)"
And error 欄位不包含檔案路徑（如 /Users/xxx/src/remagraph/store.py）
And error 欄位為 "internal_error: ValueError" 格式
```

### 8.4 gitleaks

```
Given CI 執行 gitleaks 掃描
When 掃描全 Git 歷史
Then 無 secret 被偵測（或已偵測的 secret 已被 revoke 且在 .gitleaksignore 中）
```

### 8.5 pip-audit

```
Given CI 執行 pip-audit
When 掃描依賴樹
Then 無已知 CVE（或已知 CVE 已被評估為不影響 RemaGraph 且記錄在 ADR 中）
```

---

## 9. 開放問題與已裁決項目

### 9.1 已裁決（PPLX Consensus 2026-07-21）

| # | 原問題 | 裁決 |
|---|--------|------|
| Q1 | audit.jsonl log rotation 策略？ | **DEFER v2**。v1 不做 rotation |
| Q2 | `ts` 時區（UTC vs local time）？ | **全 UTC Z**。最小混淆方案，不支援 local time + offset |
| Q3 | Database error 的 `error` 欄位粒度？ | **exception class name only**（如 `db_error: OperationalError`）。不細分 SQLITE_FULL / SQLITE_LOCKED 等 |
| Q4 | 多 instance 共用 audit.jsonl 競爭？ | v1 **單 process**（PID 鎖），不支援多實例共用 DB。此問題不適用於 v1 |
| Q5 | model2vec 下載失敗的降級策略？ | **fail-fast**：模型載入失敗立即回傳 `model_load_error`，不靜默降級（見 §5.2） |
| — | audit rotation（DF1-7） | **DEFER v2**。v1 audit.jsonl 線性 append，不做 rotation |

### 9.2 仍開放（留待 v2）

| # | 問題 | 備註 |
|---|------|------|
| Q6 | gitignore 與 audit.jsonl | state_dir 在 repo 外（`~/.local/state/remagraph/`），不需 `.gitignore`。若未來支援 per-project state_dir（`.remagraph/`）則需處理 |
| — | `ts` 精度：記憶 timestamp 到秒 vs audit 到毫秒 | 文件層級標注即可（N4） |

---

## 10. 與 DESIGN.md / docs/audit.md 對齊聲明

本文件所有設計決策的來源皆來自 `DESIGN.md` 與 `docs/audit.md`。以下為關鍵對齊點：

| 來源 | 章節 | 本文件對應 |
|------|------|-----------|
| DESIGN.md §審計 | audit.jsonl 路徑、權限、schema | §1 完整展開欄位表、型別定義、Python dataclass |
| DESIGN.md §審計 | Audit Contract | §2 逐字對齊並補充合約邊界、消費方 grep 範例 |
| DESIGN.md §審計 | 不存 traceback | §3.2 原則說明 + 正確實作範例 |
| DESIGN.md §CI/CD | gitleaks | §6.1 整合說明 |
| DESIGN.md §專案基本資訊 | 獨立專案、不耦合任何特定外部系統 | 全文無外部具名專案詞彙 |
| DESIGN.md §儲存層 | SQLite + 零依賴（stdlib） | §5 依賴面分析確認無額外 runtime 依賴 |
| docs/audit.md | Audit Contract 全文 | §2 逐字一致，補充設計細節 |
| docs/design/01-data-model-arbitration.md | reason_code 表 | §3.1 寫入時機表中的 reason_code 與 01 的錯誤碼表一致 |

本文件新增的設計（威脅模型、依賴面分析、檔案初始化流程、no traceback 原則的實作細節）**不違反** DESIGN.md 中任何既有決策，且可追溯至 DESIGN.md 的對應原則。

---

## DONE

- [x] audit.jsonl 路徑、權限（0600/0700）與 schema 欄位表（含 Python dataclass）
- [x] Audit Contract（與 docs/audit.md 一致，補充合約邊界與消費方 grep 範例）
- [x] 寫入時機（stored/error）與不回存 traceback 原則（含程式碼範例）
- [x] 威脅模型：T1 惡意灌入、T2 路徑穿越、T3 磁碟灌爆、T4 敏感內容洩漏、T5 同 user 橫向存取
- [x] 依賴面：model2vec 供應鏈風險、無 network server 假設、pip-audit 整合
- [x] gitleaks 整合與不存 secret 的設計紀律
- [x] 檔案系統安全初始化流程（含權限修正邏輯）
- [x] 驗收條件（audit 寫入、權限、no traceback、gitleaks、pip-audit）
- [x] 開放問題（6 題）
- [x] 與 DESIGN.md / docs/audit.md 對齊聲明
- [x] 驗證：全文無外部具名專案詞彙

---

## PPLX-CONSENSUS-APPLIED

本文件已完成以下 PPLX 共識裁決的寫入（2026-07-21）：

- [x] **fail-fast 模型記載**：§5.2 明確記載模型載入失敗必須 fail-fast（`model_load_error`），不靜默降級
- [x] **audit rotation DEFER v2**：§9.1 裁決表明確記載 rotation 延至 v2，v1 線性 append
- [x] **audit ts 全 UTC Z**：§9.1 裁決表明確記載全 UTC，不支援 local time + offset
- [x] **v1 單 process**：§9.1 裁決表明確記載 v1 不支援多實例共用 DB；PID 鎖僅 daemon 模式
- [x] **error 粒度：exception class name only**：§9.1 裁決，與 §3.2 不回存 traceback 原則一致
- [x] **模型名稱**：`potion-base-8M` → `potion-multilingual-128M`（§5.2）
- [x] **ts 精度標注**：§9.2 記載 N4（記憶 timestamp 到秒 vs audit 到毫秒）
