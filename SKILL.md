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

## 核心原則

1. **證據驅動** — 所有 DTS 內容必須可追溯至電路圖、GPIO table 或 datasheet
2. **永不抄答案** — 絕不從現有 DTS 答案卷複製內容
3. **可追溯性** — 每個信號/元件都帶有 Provenance（來源PDF、頁碼、信心度）
4. **可互動** — 資料不足時主動詢問使用者（ask-me 機制）
5. **跨板延續** — board-to-board connector 即使在不同 PDF / 不同 refdes，也要優先用 pin number continuation 保守接回同一條電路

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
