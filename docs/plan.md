## 目標

在 `/home/paul_chen/prj-arc/dts-build` 規劃一個**通用 DTS 產生工具**：

- 輸入：由使用者提供的 `diff-folder`
- 輸出：**可編輯的 DTS 草稿**
- 第一階段：先支援 **BCM68575**
- 配套：先提供一份**給 HW 填寫的差異表模板**

## 問題背景

- 新案大多是從某個公版/reference board 改出來。
- 使用者手上的資料不保證固定格式，可能是 GPIO/LED Excel、差異表、schematic PDF，或其他混合檔案。
- 因此工具不能假設只有單一格式，而要先把資料夾內容**正規化**後再生成 DTS。

## 核心設計

1. **reference-first**
   - 工具先找出對應的公版 board/profile。
   - 使用者主要提供「相對 reference 的硬體差異」。

2. **mixed-input**
   - `diff-folder` 可混放 Excel/CSV/PDF/附件。
   - 但第一版仍要求最小 `manifest` 來標示關鍵檔案位置與 reference 資訊。

3. **normalize-then-generate**
   - 先把資料整理成統一的 board-delta spec。
   - 再由模板或 patch 規則產生 DTS 草稿。

4. **phase 1 保守範圍**
   - 優先解析結構化表格。
   - PDF 先視為輔助證據，不把完整 PDF 自動理解列為第一版必備能力。

## 第一階段範圍

- 定義 `diff-folder` 的最小契約
- 定義 HW 差異表模板
- 定義 manifest 格式
- 建立 68575 的 reference/profile 解析邏輯
- 建立 board-delta spec
- 產出可編輯 DTS 草稿
- 產出缺件/不確定欄位報告

## 目前已收斂的決策

- 第一版要**直接產出可編輯的 DTS 草稿**
- 在生成前，需要一份**標準差異表**方便向 HW 要資料
- storage/flash 若與 reference board 不同，也必須納入差異模型

## 開發前請先準備的資料

### 最優先

1. **一份真實專案的 sample diff-folder**
   - 不求完整，但最好是你現在手邊最接近真實流程的資料包。
   - 目的是讓我用真實樣本設計 ingest / normalize 流程。
   - 建議短版資料夾名稱格式：`dtsin_<project>`
   - 例：`dtsin_MyBoard`
   - `profile` 與 `refboard` 都不放在資料夾名裡，改放進 `manifest.yaml`

2. **對應的 reference board / profile**
   - 例如該專案是從哪個公版 board/profile 改出來。
   - 這會直接影響模板選擇與差異模型。

3. **任何結構化差異表**
   - 例如 GPIO / LED Excel、port mapping 表、DDR 表、BOM 摘要。
   - 這類資料對第一版工具最重要，因為 phase 1 會優先吃結構化表格。

### 次優先

4. **schematic PDF 或其他 HW 文件**
   - 第一版不會完全自動讀懂 PDF。
   - 但它很適合當欄位補件與人工驗證的依據。

5. **如果有的話，現成人工修改過的 DTS / diff / note**
   - 這能幫我反推工具的預期輸出與欄位對應。

6. **命名規則**
   - 新板 board name、model name、輸出 DTS 檔名、是否有固定 profile 命名方式。

### 加分但非必要

7. **你目前向 HW 要資料的既有表單或流程**
   - 若你已有習慣用的差異表，我可以優先相容那個格式，而不是重新發明一份。

## 建議的 sample folder 結構

```text
dtsin_<project>/
  manifest.yaml
  tables/
    gpio_led.xlsx
    network.xlsx
    ddr.xlsx
  hw/
    schematic.pdf
  notes/
    readme.txt
```

- 若 `profile` 還不確定，可以先在 `manifest.yaml` 先填 `unknownprofile`
- 若只有一份 Excel，也可以先只放 `tables/`
- `manifest.yaml` 第一版只要先寫：project、reference board、profile、你放了哪些表格/附件
- `profile` 可能本身含底線，所以不應該靠資料夾名稱去拆這些欄位
- `profile` 與 `reference board` 不一定相等，所以這兩個欄位都要保留

## 主要假設

- `diff-folder` 允許加入最小 manifest
- profile 先用來輔助 reference/template 選擇
- 68575 是第一個落地 family，後續再擴展到其他家族

## 下一步

- 先把高階規劃拆成可落地的 `todo.md`
- 將 `plan.md` 與 `todo.md` 一起放到 `docs/`
- 依你先提供的樣本，優先落地 sample diff-folder + 差異表模板 + manifest 規格

## Phase 4 追蹤

- Phase 4 / Step 2 已完成：先清 false-positive device / split-unit / testpoint 噪音。
- 調整重點：
  - refdes indexing 會跳過 TP* 與明顯 BGA ball / pin-map / layout-note 誤抓。
  - auditor 不再把 TP*、split-unit (`U1A/U20A/...`) 與保守判定下的 J* / T* 噪音寫進 schema device。
  - issue register 仍保留已知 compatible 的 J* 候選，避免誤傷真正 relevant device。
- BGW720 fresh pipeline 結果：
  - unresolved device：`51 -> 13`
  - `lookup-gap`：`35 -> 13`
  - `exclude-from-dts`：`16 -> 0`
  - `trace-gap` 維持 `8`
  - `python -m pytest tests/ -q --tb=short`：`118 passed`
- Phase 4 / Step 3 已完成：signal trace 會沿用 cross-page evidence、follow-on net 與 pass-through passive 線索，不再只停在 `(no trace found ...)`。
- 本輪重點結果：
  - `ROGUE_ONU_IN1`：已提升為 `VERIFIED`，trace 會走到 `WAN_SFP_TX_FAULT` / SFP fault control 鏈。
  - `RESET_OUT_L`：已提升為 `VERIFIED`，trace 會走到 `POR_RESET_B` → `SCLR` / serial LED reset bus。
  - auditor 每次重跑前會 reset schema，避免 `--resume` fresh rerun 時把舊 signal/device 重複 append 回 schema。
- BGW720 重新驗證結果：
  - unresolved summary：`total_items 14`
  - `trace-gap`：`8 -> 1`
  - `lookup-gap`：維持 `13`
  - `exclude-from-dts`：維持 `0`
  - `python -m pytest tests/ -q --tb=short`：`123 passed`
- Phase 4 / Step 4 已完成：補強 part lookup / normalization，並把非 DTS-relevant helper device 從 unresolved 主噪音移除。
- 本輪重點結果：
  - `build_refdes_index()`：
    - U* pin-map / BGA ball noise（如 `U18/U20/U21/U32`）會跟 J*/T* 一樣被濾掉，不再誤當 device。
    - part number 擷取改為支援 multi-line symbol block，不再只看 refdes 同一行。
  - `lookup_refdes()`：
    - 會做 multi-line fallback context search。
    - 會做 part normalization（如 `U74LVC1G08G-AL5-R -> SN74LVC1G08`、`U74LVC1G11G-AL6-R -> SN74LVC1G11`、`TCA9555PWR -> TCA9555`）。
    - 已能從 daughter page 5 抓到 `U41 = TCA9555PWR`, `compatible = nxp,pca9555`, `address = 0x27`。
  - auditor device 篩選：
    - 會跳過 power-sequencing helper / simple logic gate / regulator helper（如 `U39/U710/U302/U303/U98/U99/U100`），避免它們繼續佔用 `lookup-gap`。
    - `U41` 保留為唯一真正 ask-me device：part/compatible/address 已收斂，但 bus 仍待確認。
  - BOM / table / xlsx 檢查：
    - 已掃描 `dtsin_BGW720/GPIO_R0A_20250730.xlsx` 與 `tables/*.csv`，未找到可直接提供 refdes→part 的 BOM 欄位，因此本輪主要仍以 schematic OCR context 收斂。
- BGW720 本輪驗證結果：
  - unresolved summary：`total_items 2`
  - `trace-gap`：維持 `1`（`SW Boot Strap`）
  - `lookup-gap`：`13 -> 1`
  - 唯一剩餘 device：`U41`
  - `python -m pytest tests/ -q --tb=short`：`127 passed`
- Phase 4 / Step 5 已完成：resolver / validation 只聚焦真正 DTS-relevant unresolved，非 runtime 噪音降級為 informational。
- 本輪重點結果：
  - resolver：
    - 會沿用 issue register 的 DTS relevance 判斷，只對 `dts_relevant=true` 的 unresolved 產生 / 保留 clarification。
    - 舊的非 DTS-relevant clarification（例如 `SW Boot Strap`）會自動轉成 suppressed/skipped，不再進 interactive ask-me。
    - 先前 non-interactive 留下的 `SKIPPED` genuine ask-me（如 `U41`）會在後續 interactive rerun 被重新提問，不會卡死在已 answered 狀態。
  - validation：
    - unresolved 會拆成 actionable 與 informational 兩種視角：
      - `U41` 類維持 warning / actionable unresolved
      - `SW Boot Strap` 類改為 info / informational unresolved
    - `validation.json` 新增 `summary`，可直接看 actionable vs informational unresolved 分佈。
  - issue register：
    - summary 新增 `actionable_items` / `informational_items`，review 時可直接聚焦真正 ask-me candidate。
- Phase 4 / converge-final-review 已完成：以 fresh `--no-resume` pipeline 固化最終可交付輸出。
- 最終輸出狀態：
  - `python -m pytest tests/ -q --tb=short`：`131 passed`
  - `python -m dtsbuild clear-session dtsout_BGW720`
  - `python -m dtsbuild generate-dts dtsin_BGW720 --pipeline agents --no-resume`
  - 已重新產出並確認存在：
    - `dtsout_BGW720/BGW720.dts`（`2620 bytes` / `118 lines`）
    - `dtsout_BGW720/BGW720.schema.yaml`（`29011 bytes`）
    - `dtsout_BGW720/BGW720.validation.json`（`12743 bytes`）
    - `dtsout_BGW720/BGW720.coverage.json`（`6278 bytes`）
    - `dtsout_BGW720/BGW720.unresolved.json`（`2296 bytes`）
  - `validation`: `passed=true`, `coverage=40.8%`, `issues=45`, unresolved summary = `actionable 1 / informational 1`
  - `coverage`: `29 / 71` verified items covered（`40.8%`）
  - 最終 unresolved 僅剩兩個：
    - actionable ask-me：`U41 / TCA9555PWR`（`lookup-gap`, DTS-relevant=true）
    - informational：`SW Boot Strap`（`trace-gap`, DTS-relevant=false）
