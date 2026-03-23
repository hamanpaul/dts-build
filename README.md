# dts-build

以 mixed diff-folder 為輸入，產生可編輯 DTS 草稿的工具骨架。

目前工作流已改成 **ask-me / sufficiency-first**：

- 先掃描現有資料是否足夠
- 對缺件輸出結構化 sufficiency report
- 條件足夠後再產生 DTS 草稿
- 預設輸出到 sibling 目錄 `dtsout_<project>/`

## 目前支援

- `init-folder`：建立 `dtsin_<project>/` 樣板資料夾
- `inspect-folder`：檢查 `manifest.yaml` 與結構化表格是否齊備
- `bootstrap-manifest`：從只有 Excel/PDF 的原始資料夾自動建立 `manifest.yaml`
- `bootstrap-tables`：從現有 PDF/XLSX 自動轉出 `blockdiag/ddr/network/gpio` tables
- `extract-spec`：從 diff-folder 抽出 normalized spec
- `scan-sufficiency`：檢查目前資料是否足夠直接產生 DTS
- `generate-dts`：依 `manifest.yaml` 與 normalized spec 輸出最小可編輯 DTS 草稿

第一階段先以 **BCM68575** 為主。

## Parser 策略

- 預設 backend：`auto`
  - 優先走 **Copilot SDK agent**
  - 若 SDK 不可用，再退回 `manual` fallback
- 強制 agent：

```bash
python -m dtsbuild extract-spec dtsin_MyBoard --backend agent
python -m dtsbuild generate-dts dtsin_MyBoard --backend agent
```

- 若要啟用 Copilot SDK agent：

```bash
python -m pip install -r requirements-agent.txt
```

也可依 Copilot SDK 文件，改用既有 CLI server：

```bash
copilot --server --port 4321
python -m dtsbuild extract-spec dtsin_MyBoard --backend agent --cli-url localhost:4321
```

## 安裝

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

## 用法

### 1. 建立樣板資料夾

```bash
python3 -m dtsbuild init-folder MyBoard \
  --profile 968375GWO_WL25DX_WLMLO \
  --refboard 968375GO
```

會建立：

```text
dtsin_MyBoard/
  manifest.yaml
  tables/
    blockdiag.csv
    ddr.csv
    gpio_led.csv
    network.csv
  hw/
    README.txt
  notes/
    README.txt
dtsout_MyBoard/
```

### 2. 檢查資料夾

```bash
python3 -m dtsbuild inspect-folder dtsin_MyBoard
```

若目前只有 Excel / PDF、還沒有 `manifest.yaml`：

```bash
python3 -m dtsbuild bootstrap-manifest dtsin_MyBoard
python3 -m dtsbuild inspect-folder dtsin_MyBoard
```

### 3. 從原始 PDF / XLSX bootstrap tables

若資料夾裡目前只有 schematic PDF、datasheet PDF、GPIO Excel 之類的原始檔案：

```bash
python3 -m dtsbuild bootstrap-tables dtsin_MyBoard
```

這個步驟會：

- 產生 `tables/blockdiag.csv`
- 產生 `tables/ddr.csv`
- 產生 `tables/network.csv`
- 產生/正規化 `tables/gpio_led.csv`
- 回寫 `manifest.yaml` 讓 artifacts 指向上述標準化 table

若資料夾內還有 public reference DTS，例如 `968575REF1.dts`，可在 `manifest.yaml` 裡加入：

```yaml
artifacts:
  public_ref_dts: 968575REF1.dts
```

它會被當成 **public reference pattern source** 使用：

- 可作為 DDR `memcfg_macro` 的 public pattern 來源
- 可提供 `buttons` / `wan_sfp` / `wan_serdes` / `i2c` / `tod` 之類的 node pattern 線索
- 只會作為 reference rules，不可覆蓋 schematic/table 證據，也不可當答案卷

目前 heuristic 會優先使用：

- `xlsx/xlsm/csv`：直接讀取並標準化
- `pdf`：用 schematic page index、block diagram、datasheet text 抽證據
- public reference rules：僅用於像 `memcfg_macro` 這種可明確對應的 public rule，不使用 board DTS answer key

### 4. 產生 DTS 草稿

```bash
python3 -m dtsbuild generate-dts dtsin_MyBoard --backend auto
```

若想先確認資料是否足夠：

```bash
python3 -m dtsbuild scan-sufficiency dtsin_MyBoard --backend auto
```

預設會輸出到 sibling 目錄 `dtsout_MyBoard/<output_dts>`。

同時會寫出：

- `dtsout_MyBoard/<project>.spec.json`
- `dtsout_MyBoard/<project>.sufficiency.json`
- `dtsout_MyBoard/<project>.gaps.json`

## manifest 最小欄位

```yaml
project: MyBoard
family: bcm68575
profile: 968375GWO_WL25DX_WLMLO
refboard: 968375GO
model: MyBoard
output_dts: MyBoard.dts
output_dir: dtsout_MyBoard
base_include: inc/68375.dtsi
compatible: brcm,bcm968375
artifacts:
  blockdiag_table: tables/blockdiag.csv
  ddr_table: tables/ddr.csv
  gpio_led_table: tables/gpio_led.csv
  network_table: tables/network.csv
  public_ref_dts: 968575REF1.dts
  schematic_pdf: hw/schematic.pdf
```

`profile` 與 `refboard` 一律放在 `manifest.yaml`，不靠資料夾名稱解析。
`blockdiag_table` 建議在前期就先整理好，因為 sufficiency scanner 會把它當成第一層的介面盤點來源。

若一開始還沒有這些 table，可以先跑 `bootstrap-tables` 讓工具從現有 PDF/XLSX 自動轉出第一版，再進入 `scan-sufficiency` / `generate-dts`。
