---
name: dts-build
description: >
  從硬體設計資料（電路圖、GPIO table、datasheet）自動產生 Broadcom BCM68575 系列 DTS 檔案。
  使用 4-agent pipeline (Indexer→Auditor→Resolver→Compiler) 搭配 Hardware Schema 中間層，
  確保所有 DTS 內容皆可追溯至硬體證據，永不參考答案卷。
---

# DTS Build Skill

## 概述

dts-build 是一個用於從硬體設計資料自動產生 Device Tree Source (DTS) 檔案的工具。
專為 Broadcom BCM68575 (BGW720 等) 平台設計。

若要把這套能力拆成可安裝 skill，請另外參考：

- `skills/schematic-reasoner/SKILL.md`
- `skills/schematic-reasoner/references/analysis-playbook.md`
- `skills/gen-dts/SKILL.md`
- `skills/gen-dts/references/dts-generation-playbook.md`

## 核心原則

1. **證據驅動** — 所有 DTS 內容必須可追溯至電路圖、GPIO table 或 datasheet
2. **永不抄答案** — 絕不從現有 DTS 答案卷複製內容
3. **可追溯性** — 每個信號/元件都帶有 Provenance（來源PDF、頁碼、信心度）
4. **可互動** — 資料不足時主動詢問使用者（ask-me 機制）
5. **跨板延續** — board-to-board connector 即使在不同 PDF / 不同 refdes，也要優先用 pin number continuation 保守接回同一條電路

### 目前判斷標準（必須一致遵守）

- **答案卷只作 diff oracle / human review**
  - 例如 `dtsout_BGW720/BGW720-300_v11.dts` 這類 board DTS answer key，只能拿來做事後比對與差異盤點。
  - 不得作為 compiler input，不得直接決定 active DTS 值。
- **public reference DTS 只作 public pattern source**
  - `968575REF1.dts` 這類 public reference 只能提供 public rule / pattern 線索。
  - 若 compiler 有拿到 `ref_dts_path`，generated DTS 可沿用 reference DTS 的 top-level node 順序，方便人工 diff。
  - 若保留其片段，也只能作為 **non-executing review context**，不得直接變成 active DTS code。
- **第二組 SFP / `serdes1` 的門檻**
  - 只有在 raw evidence 能獨立證明「第二組已裝配的 SFP cage/path」存在時，才能落地 `serdes1` / `lan_sfp` 類內容。
  - 僅有 `...1` 命名、Reserve、`CPU_Service_*`、RFIC reuse label 等訊號名，不足以證成第二組 SFP。
- **`wan_sfp:i2c-bus` 需要 cage-level bus 證據**
  - 若 schematic 同頁能同時看到 SFP cage（例如 `U6`）、`I2C Address: 0xA0/A2`、`SFP_SCL`、`SFP_SDA`，並且其 bus 端落到 `SDA_0` / `SCL`，則可把 `wan_sfp` 的 `i2c-bus = <&i2c0>;` 升成 active DTS。
  - 不可只因其他 I2C device（例如 expander）在 `i2c0`，就把 `wan_sfp:i2c-bus` 一起猜成 `i2c0`。
- **`usb_ctrl:port1-disabled` 需要實際 port population 證據**
  - 若 schematic page 只證明 `USB0` 具備 VBUS / connector / superspeed path，而 `USB1` 沒有對應 VBUS 或 superspeed wiring，則可把 `&usb_ctrl { port1-disabled; }` 升成 active DTS。
  - 不可只因看到 `USB1_PWRON` / `USB1_DP` / `USB1_DM` 這類 controller-side signal name，就假設第二個板上 USB port 已裝配。
- **`ext_pwr_ctrl` 只可消費 `POWER_CONTROL`**
  - 不可把 `USB_POWER` 類 signal 的 SoC ball 名稱（例如 `K3` / `M31`）誤抽成 `gpioc 3/31` 之類假的 power GPIO。
  - active `&ext_pwr_ctrl` 只能由真正 `POWER_CONTROL` role 的 signal 生成。
- **`&gpioc` Wi-Fi hog 要從 `PCIE_WIFI` signal 落地**
  - `RF_DISABLE_L` 生成 `GPIO_ACTIVE_LOW + output-low` 的 `gpio-hog`。
  - `PEWAKE` 生成 `GPIO_ACTIVE_HIGH + output-high` 的 `gpio-hog`。
- **ref-only property 不可因答案卷存在就啟用**
  - 例如 `&hsspi:/delete-property/ pinctrl-0`、`&ethphytop:xphy3-enabled`、`xphy4-enabled`、`wakeup-trigger-pin-gpio`，都必須回到 raw evidence 證成。
  - 不能因 public ref 或 answer key 出現，就直接寫回 DTS。

### 以 `968575REF1.dts` 逐區塊對齊後的補充規則

- **`memory_controller.memcfg`**
  - 若 DDR 顆粒/型別/寬度可由硬體資料共同證成與 ref design 相符，可完整沿用對應 `memcfg` 模板。
- **`buttons`**
  - 先用硬體證據決定有哪些 button 與其 GPIO/interrupt。
  - 某顆 button 一旦被證成存在，可沿用該 button 在 public ref 內的 `press/hold/release` 行為子節點；`linux,code` / `linux,press` / `linux,release` 這類 semantic property 不自動沿用。
  - 不存在的 button block 應刪除，而不是保留為 retained comment。
- **`i2c0` / `i2c1`**
  - 先證 bus/pinctrl，再逐個 child device 看 address/part evidence。
  - 第二組 I2C 必須先證成 bus/pin 線路存在；若相關 pin 已被證成他用，視為「已證明不存在」並排除。
- **`wdt` / `cpufreq`**
  - `wdt` 屬 SoC 內建 capability，可依 CPU datasheet / chip capability 開啟，不要求外部電路。
  - `&cpufreq { op-mode = "dvfs"; }` 依目前核對結果，對 BCM68375/68575 family 可沿用 public ref policy。
- **`led_ctrl`**
  - `ref DTS` 只能當命名/結構模板；實際 LED child、crossbar、trigger 必須以 LED controller datasheet 與電路設計為主，不可由 signal 順序硬猜。

## 架構

```
dtsin_<project>/          ← 輸入資料
  ├── .analysis/*.txt     ← 電路圖文字萃取
  ├── gpio_table.csv      ← GPIO 對照表
  └── *.pdf               ← 電路圖/datasheet

    ↓ [4-Agent Pipeline]

  Indexer   → 建立 tag/refdes/connector 索引
  Auditor   → 追蹤信號、偵測 lane swap、識別元件
  Resolver  → 詢問使用者解決歧義（ask-me）
  Compiler  → 從 VERIFIED schema 產生 DTS

    ↓

dtsout_<project>/         ← 輸出
  ├── <project>.dts       ← 產出的 DTS
  ├── <project>.schema.yaml ← Hardware Schema
  ├── <project>.validation.json ← validation / review 問題摘要
  ├── <project>.coverage.json   ← schema ↔ DTS 覆蓋率
  └── <project>.unresolved.json ← unresolved register（actionable / informational）
```

## 網路拓樸來源分析（skill 必須先做對）

- 在修 `switch0` / `ethphytop` / `xport` pipeline 之前，**必須先把 source-analysis / stable artifact 做對**。
- 對 BCM68575 網路面，skill 不只要抓單條 net，還要抽出可重用的 topology fact：
  - `phy_group`：例如 `PHY1` / `PHY2` / `PHY3`
  - `phy_handle`：例如 `gphy0` / `gphy1` / `xphy10g`
  - `switch_port`：例如 `port_xgphy0` / `port_wan@xpon_ae`
  - `port_group`：例如 `slan_sd` / `xpon_ae`
  - `lane_swap_status`：例如 `pending_audit` / `proven_swap` / `proven_no_swap`
- 這些事實應優先穩定落在 `tables/network.csv` 與 `schema.yaml`，而不是直接在 compiler 端猜。

### switch0 / ethphytop / 10GPHY 的 skill 規則

- block diagram（例如 P13）與對應 schematic page 若能證明：
  - 哪些 2.5GPHY / 10GPHY 被使用
  - 它們屬於哪個 PHY 分組
  - 對應哪個 switch port
  - WAN / LAN / slan 角色
  則這些都應先轉成 stable topology artifact。
- 若目前只能從 schematic OCR 看到 `2.5GPHY N` / `10GPHY` 這類 lane label，但還無法從現有 evidence table 證明 board-level `switch_port` mapping，則 row 應標成 `present=inferred`，且 `switch_port`/`port_group` 保持空白，不能直接驅動 active `switch0`.
- block diagram 若缺可用 text layer，可對候選 topology page（例如主板 page 2）做 OCR fallback；但 OCR 只負責補介面盤點與 WAN/SFP 證據，不可單靠 OCR 直接猜 LAN `switch_port`.
- `ethphytop` 的 `xphy*-enabled` 可以由這些 topology fact 與 per-port 證據支持。
- `switch0` 則必須等每個 port 的 topology fact 夠穩定後，再交給後續 renderer 落地。
- 目前 agents pipeline 已會在 auditor 階段 ingest `tables/network.csv`；`present=true` 的 proven topology row 可直接驅動 active `&switch0/ports/*` / `&xport`。
- `present=inferred` 的候選 row 仍**不能直接**落成 active `switch0` topology，但可作為 `GPHY` lane-swap detector 的 candidate list；真正落成 active `&mdio_bus/xphy*:enet-phy-lane-swap` 與對應 `&ethphytop:xphy*-enabled` 的，仍必須來自後續 tracing detector 的獨立 hint。
- 若 CPU datasheet 已驗證 XPORT inventory，且 block diagram OCR 同時證明 WAN/SFP path，則 `wan_10g` 可補回 `switch_port=port_wan@xpon_ae`；LAN `xgphy` rows 仍須等待 board-level mapping 證據。
- `network-leds` 不屬於單純 topology；即使 port 被證成，LED binding 仍需獨立證據。

## 使用方式

本地開發建議在 repo 內啟用 venv 後直接使用 `python -m dtsbuild ...`。

### 初始化專案
```bash
python -m dtsbuild init-folder BGW720
```

### 準備 GPIO 表
```bash
python -m dtsbuild bootstrap-tables dtsin_BGW720
```

### 產生 DTS（使用 agent pipeline）
```bash
python -m dtsbuild generate-dts dtsin_BGW720 --pipeline agents --interactive
```

### 產生 DTS（非互動模式）
```bash
python -m dtsbuild generate-dts dtsin_BGW720 --pipeline agents
```

### 恢復中斷的 session
```bash
python -m dtsbuild generate-dts dtsin_BGW720 --pipeline agents --resume
```

### fresh rerun（先清 session，再強制重跑）
```bash
python -m dtsbuild clear-session dtsin_BGW720
python -m dtsbuild generate-dts dtsin_BGW720 --pipeline agents --no-resume
```

### 清除 session（接受 `dtsin_` 或 `dtsout_`）
```bash
python -m dtsbuild clear-session dtsout_BGW720
```

### 檢視 schema
```bash
python -m dtsbuild audit-schema dtsin_BGW720
```

## 輸出與 review workflow

- `<project>.dts`：主要檢查入口；Phase 4 後應優先直接看這個檔案。
- `<project>.schema.yaml`：Hardware Schema 中間層與 provenance。
- `<project>.validation.json`：validation 問題與對應訊息。
- `<project>.coverage.json`：coverage 摘要。
- `<project>.unresolved.json`：unresolved register。
  - `actionable`：仍需 ask-me / 人工決策的項目。
  - `informational`：保留背景資訊，不阻塞 DTS review。
- 建議 review 順序：**先看 DTS，再參考 unresolved / validation；coverage 作為輔助。**
- 既有 `<project>.answers.json` 會在 non-interactive rerun 時被回放，讓已確認的 ask-me 證據能重新落到 schema / DTS。

## 需要準備的輸入資料

| 資料 | 必要性 | 說明 |
|------|--------|------|
| 主板電路圖 PDF | 必要 | 含完整 net/tag 標註 |
| 子板電路圖 PDF | 視設計 | 若有子板（如 WiFi/SFP 模組） |
| GPIO table CSV | 必要 | Pin 名稱 ↔ GPIO 編號對照 |
| Datasheet PDF | 視需要 | 特殊元件（如 74HC595、TCA9555） |
| BOM 檔案 | 選用 | 判斷 DNP 元件 |

## 子系統規則庫

內建 12 個子系統規則：
buttons, uart, led, i2c, usb, pcie, serdes, ethernet, power, memory, pinctrl

每個規則從 schema 中的 VERIFIED 信號/元件自動比對並產生對應 DTS 節點。

## 開發

```bash
cd ~/prj-arc/dts-build
source .venv/bin/activate
python -m pytest tests/ -v
```
