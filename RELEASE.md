# Release Process

RemaGraph follows [Semantic Versioning](https://semver.org/), but is currently
in a **pre-1.0** stage (alpha and, as of `0.4.0`, beta) — there is no frozen
public API yet (see [`BOUNDARIES.md`](BOUNDARIES.md)). The version number still communicates
intent using the rules below, but during pre-1.0, even a MINOR or PATCH bump
may include a reviewed, CHANGELOG-documented behavioral adjustment to MCP tool
parameters or CLI subcommands — something that would require a MAJOR bump once
the public surface is frozen at 1.0.

## Versioning rules

- **MAJOR** — once the public surface is frozen at 1.0, a backward-incompatible
  change to that surface. Pre-1.0, reserved for a deliberate, clearly
  communicated break.
- **MINOR** — additive, backward-compatible changes (new MCP tool parameters,
  new CLI subcommands, new `kind`s, etc.).
- **PATCH** — bug/security fixes and internal changes with no intended surface
  change.

## Current state

- Current version: `0.4.x` (see `pyproject.toml`), tagged as `-alpha` or
  `-beta` releases depending on maturity (e.g. `v0.3.1-alpha`, `v0.4.0-beta`).
- **Not yet published to PyPI.** Installation today is via a git-pinned tag:
  ```bash
  uv tool install git+https://github.com/aiken884/RemaGraph.git@vX.Y.Z-alpha
  # or, once tagged as beta:
  uv tool install git+https://github.com/aiken884/RemaGraph.git@vX.Y.Z-beta
  ```
  See the "Installation" section of [`README.md`](README.md) for the current
  recommended tag.

## Cutting a release

1. Ensure `main` is green: CI (`smoke` → `lint` [ruff + mypy] → `test` with
   coverage ≥80%, per `.github/workflows/test.yml`) passes.
2. Update `CHANGELOG.md` — move the `Unreleased` entries under a new
   `## [X.Y.Z-alpha]` or `## [X.Y.Z-beta] - DATE` heading, whichever
   maturity label applies (both the English and 繁體中文 sections).
3. Bump the version in `pyproject.toml` to match.
4. Commit, then tag: `git tag -a vX.Y.Z-alpha -m "..." && git push origin vX.Y.Z-alpha`
   (substitute `-beta` once the release has reached that maturity).
5. (Optional, not yet a fixed required step) Create a GitHub Release for the
   tag with notes drawn from `CHANGELOG.md`.

### About `.github/workflows/publish.yml`

A `publish.yml` workflow already exists, triggered on `v*` tag pushes, intended
to build and publish to PyPI via OIDC Trusted Publishing. **This path has not
yet been verified end-to-end** — earlier attempts were blocked by an
account-level GitHub Actions quota limit; the quota has since been raised, but
no tag push has yet produced a confirmed, successful PyPI publish. Treat this
as "configured but unproven," not as a working automated release pipeline.
Until it's verified, cutting a release means steps 1-5 above (git tag only);
publishing to PyPI is a separate, not-yet-exercised step.

## Security releases

Security fixes are prioritized and released as a PATCH as soon as verified —
see [`SECURITY.md`](SECURITY.md) for how to report a vulnerability.

## Cadence

No fixed calendar cadence; a release is cut when there is meaningful, verified
change to ship.

---

# 發布流程

RemaGraph 採用 [Semantic Versioning](https://semver.org/)，但目前處於
**pre-1.0** 階段（含 alpha，自 `0.4.0` 起也含 beta）——還沒有凍結的公開 API（詳見 [`BOUNDARIES.md`](BOUNDARIES.md)）。
版號依然依循下方規則傳達意圖，但在 pre-1.0 期間，即使是 MINOR 或 PATCH 版號，
也可能包含經過審查、且已在 CHANGELOG 中記錄過的 MCP tool 參數或 CLI 子指令行為
調整——這類調整一旦到了 1.0、公開介面凍結之後，就會需要走 MAJOR 版號。

## 版號規則

- **MAJOR**——等到公開介面在 1.0 凍結之後，代表對該介面的破壞性變更。pre-1.0
  期間保留給刻意且已清楚溝通過的重大break。
- **MINOR**——新增且向下相容的變更（新的 MCP tool 參數、新的 CLI 子指令、新的
  `kind` 等）。
- **PATCH**——bug／安全性修復，以及不打算改變介面的內部變更。

## 目前現況

- 目前版本：`0.4.x`（見 `pyproject.toml`），依成熟度以 `-alpha` 或 `-beta`
  標籤打 tag（例如 `v0.3.1-alpha`、`v0.4.0-beta`）。
- **尚未正式發布到 PyPI。** 目前的安裝方式是透過 git 釘住特定 tag：
  ```bash
  uv tool install git+https://github.com/aiken884/RemaGraph.git@vX.Y.Z-alpha
  # 若已打 beta tag：
  uv tool install git+https://github.com/aiken884/RemaGraph.git@vX.Y.Z-beta
  ```
  目前建議釘的 tag 版本見 [`README.md`](README.md)「安裝」章節。

## 切版本流程

1. 確認 `main` 綠燈：CI（`smoke` → `lint`〔ruff + mypy〕→ `test`，coverage ≥80%，
   對應 `.github/workflows/test.yml`）通過。
2. 更新 `CHANGELOG.md`——把 `Unreleased` 章節搬到新的
   `## [X.Y.Z-alpha]` 或 `## [X.Y.Z-beta] — DATE` 標題底下，依實際成熟度
   標籤擇一（英文、繁體中文兩個章節都要搬）。
3. 同步把 `pyproject.toml` 的版本號 bump 到位。
4. commit，然後打 tag：`git tag -a vX.Y.Z-alpha -m "..." && git push origin vX.Y.Z-alpha`
   （若已達 beta 成熟度則改用 `-beta`）。
5. （可選，目前尚未固定為必要步驟）為該 tag 建立對應的 GitHub Release，內容取
   自 `CHANGELOG.md`。

### 關於 `.github/workflows/publish.yml`

repo 中已經有一份 `publish.yml`，觸發條件是 push `v*` tag，設計上會 build 並透
過 OIDC Trusted Publishing 發布到 PyPI。**但這條路徑尚未被真正跑通驗證過**——先
前曾因帳號層級的 GitHub Actions 額度限制被卡住；額度目前已提升，但截至目前為止
還沒有任何一次 tag push 產出過確認成功的 PyPI 發布紀錄。請把它當作「已設定、但
尚未驗證有效」，不要當成已經在運作的自動發布流程。在驗證之前，切版本實務上就
是走上面 1-5 步（僅止於打 tag）；發布到 PyPI 是另一個尚未真正跑通過的獨立步驟。

## 安全性發布

安全性修復會被優先處理，一旦驗證完成即以 PATCH 版本發布——回報漏洞的方式見
[`SECURITY.md`](SECURITY.md)。

## 發布節奏

沒有固定的行事曆週期；只有在有實質且經過驗證的變更可以出貨時才切版本。
