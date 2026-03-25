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

若要看把本次 BGW720 tracing 經驗抽象成可重用 skill 的設計稿，請看：

- `skills/schematic-reasoner/SKILL.md`
- `skills/schematic-reasoner/references/analysis-playbook.md`
- `skills/gen-dts/SKILL.md`
- `skills/gen-dts/references/dts-generation-playbook.md`

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

如果你只想先從最小輸入開始，也可以直接複製 repo 內建的 `dtsin_template/`：

```bash
cp -r dtsin_template dtsin_MyBoard
```

`dtsin_template/` 故意只放最基本的 `manifest.yaml`，因為每個新案子的原始檔案差異很大，通常要等你手上有哪些 PDF / XLSX / public ref DTS 之後，才決定實際要放哪些檔案。

最終一個可工作的 `dtsin_{{PROJECT}}/` 通常會長成這樣：

```text
dtsin_MyBoard/
  manifest.yaml                  # 必備：專案基本資訊與 artifact 路徑
  tables/
    blockdiag.csv                # 建議：方塊圖/port inventory
    ddr.csv                      # 建議：DDR 顆粒與 bus 寬度
    gpio_led.csv                 # 建議：GPIO / LED / button / power / SFP / USB
    network.csv                  # 建議：switch / xport / phy / serdes topology
  968575REF1.dts                 # 選配：public reference DTS
  board-main.pdf                 # 選配：主板 schematic / block diagram
  board-daughter.pdf             # 選配：子板 schematic
  GPIO.xlsx                      # 選配：GPIO table / BOM / pin mux spreadsheet
  .analysis/                     # 自動產生，請不要手改
```

不是每個檔案都必須一開始就準備齊，但至少要有：

- `manifest.yaml`
- **以下兩類資料至少擇一**
  - 已整理好的標準化 tables（`tables/*.csv`）
  - 或原始 PDF / XLSX，讓 `bootstrap-manifest` / `bootstrap-tables` 去轉

若你希望工具更穩定地產出 `switch0` / `xport` / `mdio_bus` / `wan_sfp` / `i2c` / `buttons` 這類區塊，建議盡量同時提供 schematic PDF、GPIO Excel/table、block diagram 與 public reference DTS。

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
- 若 `generate-dts` 有拿到 `ref_dts_path`，compiler 也會沿用 reference DTS 的 top-level node 順序排列輸出，方便人工 diff / 手修
- 只會作為 reference rules，不可覆蓋 schematic/table 證據，也不可當答案卷
- 若某段 public reference 暫時需要保留，也只能作為 **non-executing review context**，不可直接變成 active DTS code

目前針對 `968575REF1.dts` 的 block-by-block 判準也一併收斂為：

- `memory_controller.memcfg`：若 DDR 顆粒/型別/寬度與 ref design 可由硬體共同證成，可完整沿用模板。
- `buttons`：先證成 button 的 GPIO/interrupt；button 一旦成立，可沿用 ref 的 `press/hold/release` 行為子節點；`linux,code` 這類 semantic property 不自動沿用；不存在的 button block 不保留。
- `i2c`：先證 bus，再證 child device；`i2c1` 若 pin 已證成他用，視為已證明不存在並排除。
- `wdt` / `cpufreq`：屬 SoC capability 的項目，可依 CPU datasheet / 已核對的 public-ref policy 啟用，不要求外部線路。
- `led_ctrl`：ref 只當命名/結構模板，child/crossbar/trigger 必須由 controller datasheet + 電路設計決定。

目前 heuristic 會優先使用：

- `xlsx/xlsm/csv`：直接讀取並標準化
- `pdf`：用 schematic page index、block diagram、datasheet text 抽證據
- public reference rules：僅用於像 `memcfg_macro` 這種可明確對應的 public rule，不使用 board DTS answer key

## 目前判斷標準

- **答案卷只作 diff oracle**
  - 例如 `dtsout_BGW720/BGW720-300_v11.dts` 僅用於 `calibrate-dts` / `refdiff` / human review。
  - 不可作為 compiler input，也不可直接決定 active DTS 值。
- **public reference 不能直接落成 active DTS**
  - `968575REF1.dts` 只能提供 public pattern / rule 線索。
  - 若保留其片段，也只能留在 generated DTS 中作 non-executing review context。
- **`serdes1` / 第二組 SFP 的判斷**
  - 只有在 raw evidence 能獨立證明第二組已裝配的 SFP cage/path 存在時，才可落地 `serdes1` / `lan_sfp`。
  - 單靠 `...1` 訊號名、Reserve、`CPU_Service_*` 或 RFIC reuse label，不構成充分證據。
- **ref-only property 需要獨立證據**
  - 例如 `&hsspi:/delete-property/ pinctrl-0`、`&ethphytop:xphy3-enabled`、`xphy4-enabled`、`wakeup-trigger-pin-gpio`，都必須由 raw evidence 單獨證成。
- **active DTS 與 retained comment 的分界要逐區塊判斷**
  - `buttons` 這類 block，若 button 本體已被硬體證成存在，可把對應 ref 行為子節點提升為 active DTS，而不是只留 retained comment。
  - 但像第二組 SFP、第二組 I2C、`ethphytop` 額外 property 這些仍需各自達到獨立證據門檻，不能因同一大 block 其他部分成立就一併帶入。
- **`wan_sfp:i2c-bus` 可由 SFP cage page-scan 證成**
  - 若 schematic 同頁同時證明 `U6`（或對應 SFP cage）、`I2C Address: 0xA0/A2`、`SFP_SCL`、`SFP_SDA`，且能看到它們落到 `SDA_0` / `SCL`，則可把 `wan_sfp` 的 `i2c-bus = <&i2c0>;` 升成 active DTS。
  - 這條證據屬於 `wan_sfp` 自身的 cage wiring 證明，不應退化成「只因某顆別的 I2C device 在 i2c0，所以順便猜 wan_sfp 也是 i2c0」。
- **`usb_ctrl:port1-disabled` 必須由 port population 證成**
  - 若 schematic page 能證明 `USB0` 具備完整的 VBUS / connector / superspeed path，但 `USB1` 只停留在 controller-side pin 名稱，缺少對應的 VBUS 或 superspeed wiring，則可把 `&usb_ctrl { port1-disabled; }` 升成 active DTS。
  - 不可只因看到 `USB1_PWRON` 這類 signal name，就反推出第二個板上 USB port 已實際裝配。
- **`ext_pwr_ctrl` 只可吃 `POWER_CONTROL` role**
  - `USB_POWER` 這類 signal 不能因 SoC ball 名稱含數字（例如 `K3`、`M31`）就被抽成 `gpioc 3/31` 類假 power GPIO。
  - `&ext_pwr_ctrl` 只能從真正的 `POWER_CONTROL` signal 生成 active property。
- **`&gpioc` 的 Wi-Fi hog block 由 `PCIE_WIFI` 證據生成**
  - `RF_DISABLE_L` 類 signal 應生成 `GPIO_ACTIVE_LOW` + `output-low` 的 `gpio-hog`。
  - `PEWAKE` 類 signal 應生成 `GPIO_ACTIVE_HIGH` + `output-high` 的 `gpio-hog`。

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

## `manifest.yaml` 與 `dtsin_{{PROJECT}}` 準備說明

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
  schematic_pdfs:
    - board-main.pdf
    - board-daughter.pdf
```

### `manifest.yaml` 每個欄位在做什麼

| 欄位 | 是否必填 | 說明 | 你要填什麼 |
|---|---|---|---|
| `project` | 必填 | 專案代號，也是輸出檔名與預設 sibling output dir 的基礎名稱。 | 例如 `BGW720`、`MyBoard` |
| `family` | 必填 | SoC family；目前第一階段主要以 `bcm68575` 為主。 | 例如 `bcm68575` |
| `profile` | 必填 | public profile / build profile 名稱；用來對齊 family-specific public rules。 | 例如 `968375GWO_WL25DX_WLMLO` |
| `refboard` | 必填 | public ref board 名稱；供 public pattern / rule 對照，不靠資料夾名稱猜。 | 例如 `968375GO` |
| `model` | 必填 | 人看得懂的板名；通常與 `project` 一樣即可。 | 例如 `BGW720` |
| `output_dts` | 必填 | 產出的 DTS 檔名。 | 例如 `BGW720.dts` |
| `output_dir` | 必填 | 輸出目錄；通常設成 sibling `dtsout_{{PROJECT}}`。 | 例如 `dtsout_BGW720` |
| `base_include` | 必填 | SoC base dtsi include。 | 例如 `inc/68375.dtsi` |
| `compatible` | 必填 | top-level DTS compatible string。 | 例如 `brcm,bcm968375` |
| `artifacts.blockdiag_table` | 強烈建議 | 標準化 block diagram / port inventory table；sufficiency scanner 會把它當作第一層介面盤點來源。 | `tables/blockdiag.csv` |
| `artifacts.ddr_table` | 強烈建議 | DDR 顆粒、bus width、容量等整理表。 | `tables/ddr.csv` |
| `artifacts.gpio_led_table` | 強烈建議 | GPIO / LED / button / reset / SFP / USB / power 等主要引腳表。 | `tables/gpio_led.csv` |
| `artifacts.network_table` | 強烈建議 | switch / xport / phy / serdes / wan topology 整理表。 | `tables/network.csv` |
| `artifacts.public_ref_dts` | 選填，但建議 | public reference DTS；只作 public rule / ordering / review context，不可當答案卷。 | 例如 `968575REF1.dts` |
| `artifacts.schematic_pdfs` | 選填，但很有幫助 | 原始 schematic / mainboard / daughterboard PDF；給 bootstrap、auditor 與 ask-me 使用。 | 一個或多個 PDF 檔名 |
| `notes` | 選填 | 人工備註。 | 任意短句 |

### 路徑怎麼寫

- `manifest.yaml` 裡所有 artifact 路徑，都以 **`manifest.yaml` 所在的 `dtsin_{{PROJECT}}/` 為相對路徑基準**
- 不需要寫成絕對路徑
- 只放你真的有提供的檔案；如果某個 artifact 還沒有，就先刪掉或留待 `bootstrap-manifest` / `bootstrap-tables` 回填

### `dtsin_{{PROJECT}}/` 裡到底要放什麼

最少需要這些：

1. `manifest.yaml`
2. 至少一種可被工具理解的硬體證據來源：
   - 已整理 tables：`blockdiag.csv` / `ddr.csv` / `gpio_led.csv` / `network.csv`
   - 或原始 PDF / XLSX，讓 bootstrap 工具去抽出 tables

若你希望最後 DTS 比較完整，建議額外準備：

1. **主板 / 子板 schematic PDF**
   - 用來 trace `GPIO`、`I2C`、`USB`、`SFP`、`PCIE_WIFI`、`power control`
2. **GPIO Excel 或 GPIO table**
   - 用來對齊 SoC pin、signal name、角色分類
3. **block diagram**
   - 用來建立 `switch0` / `xport` / `WAN/LAN` / `port inventory`
4. **DDR 資料**
   - 用來決定 `memory_controller.memcfg`
5. **public reference DTS**
   - 用來當 public pattern source、輸出排序與 review context

### 哪些東西不是你要手填的

- `.analysis/`：由 pipeline / bootstrap 自動產生
- `dtsout_{{PROJECT}}/`：輸出目錄，不放在 `dtsin_{{PROJECT}}/` 裡面
- sufficiency / validation / coverage / calibration artifacts：都會寫到 `dtsout_{{PROJECT}}/`

### 如果我一開始只有 PDF / XLSX，沒有 tables，可以嗎？

可以。

先把原始檔放進 `dtsin_{{PROJECT}}/`，再跑：

```bash
python3 -m dtsbuild bootstrap-manifest dtsin_MyBoard
python3 -m dtsbuild bootstrap-tables dtsin_MyBoard
```

工具會盡量把現有 PDF / XLSX 轉成標準化 tables，再回寫 `manifest.yaml`。

### 哪些資料最容易影響生成品質

- `blockdiag_table`：決定第一層介面盤點與 topology 起點
- `gpio_led_table`：決定大多數 active GPIO-backed property
- `network_table`：決定 `switch0` / `xport` / `mdio_bus` / `ethphytop` 相關拓樸 hint
- schematic PDF：決定 trace 類證據，例如 `wan_sfp:i2c-bus`、`usb_ctrl:port1-disabled`、lane-swap、GPIO hog、power control
- `public_ref_dts`：只影響 public pattern / review / ordering，不替代 raw evidence

`profile` 與 `refboard` 一律放在 `manifest.yaml`，不靠資料夾名稱解析。
`blockdiag_table` 建議在前期就先整理好，因為 sufficiency scanner 會把它當成第一層的介面盤點來源。
在 agents pipeline 中，`network_table` 內 **明確證成** 的 topology facts（例如 `present=true` 且已有明確 `switch_port`）才會由 auditor 轉成 schema hints，驅動 `&switch0/ports/*`、`&xport` 與直接的 topology enablement。若 bootstrap 只能從 lane label 推到候選 row，則應保留為 `present=inferred`，不可直接啟用 active switch port；`network-leds` 之類非純拓樸屬性仍需獨立證據。**但對 `GPHY` lane-swap 這種可由 tracing 獨立證成的屬性，`present=inferred` row 可以作為 detector 的 candidate list；真正落成 active `&mdio_bus/xphy*:enet-phy-lane-swap` 與對應 `&ethphytop:xphy*-enabled` 的，仍是後續 detector 產生的 traced hint，不是 inferred row 本身。** 對 BGW720 這類板子，bootstrap 也會對主板 block diagram page 做 OCR fallback；只有當 block diagram OCR 與 CPU datasheet XPORT inventory 同時支持 WAN/SFP 拓樸時，才會把 `wan_10g` 安全映射到 `port_wan@xpon_ae`。

若一開始還沒有這些 table，可以先跑 `bootstrap-tables` 讓工具從現有 PDF/XLSX 自動轉出第一版，再進入 `scan-sufficiency` / `generate-dts`。
