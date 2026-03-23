# DTS 產生工具 Todo（落地版）

## Phase 0：文件與輸入契約

### 0.1 定義 diff-folder 規格
- 定義必要檔案
  - `manifest`
  - 至少一份差異表
- 定義可選檔案
  - GPIO/LED Excel
  - schematic PDF
  - board notes
  - 其他附件
- 定義命名與資料夾結構建議
- 建議第一版 sample 命名：`dtsin_<project>`
- `profile` / `reference board` 都改由 `manifest.yaml` 保存

### 0.2 定義 HW 差異表模板
- 列出 68575 phase 1 必填欄位
  - reference board/profile
  - DDR
  - storage/flash
  - network topology
  - GPIO/pinctrl/bus
  - optional blocks（SFP / xPHY / PCIe / voice / USB / LED / buttons）
- 定義可空欄位與缺件標記方式
- 規劃輸出成 HW 能直接填的格式

### 0.3 定義 manifest 格式
- reference board
- profile
- 差異表檔名/路徑
- 附件檔案索引
- 專案名稱 / board name / model name

### 0.4 收集開發樣本
- 收一份真實 sample diff-folder
- 收對應的 reference board / profile
- 收至少一份結構化差異表（GPIO / LED / network / DDR 任一）
- 收 schematic PDF 或其他輔助文件
- 若有既有人工作法，收 DTS diff / note / naming rule
- 樣本資料夾優先依建議命名格式整理

## Phase 1：68575 中介資料模型

### 1.1 定義 canonical spec
- project metadata
- reference/profile metadata
- DDR
- storage/flash
- network topology
- GPIO / pinmux / bus map
- optional subsystems

### 1.2 定義 missing-information 規則
- 哪些欄位缺了仍可先出草稿
- 哪些欄位缺了就只能報錯
- 哪些欄位缺了要在 DTS 草稿中保留 TODO 標記

## Phase 2：解析與正規化

### 2.1 diff-folder ingest
- 掃描 manifest
- 掃描表格檔
- 掃描附件檔
- 建立專案輸入索引

### 2.2 structured table parser
- 先支援 Excel/CSV
- 將差異表欄位對應到 canonical spec
- 對欄位做基本型別與枚舉驗證

### 2.3 PDF/附件策略
- phase 1 不做完整自動解析
- 只在報告中記錄附件存在與路徑
- 需要人工補資料的欄位要明確回報

## Phase 3：reference/profile 解析

### 3.1 68575 reference board 映射
- 對接 68575 公版 DTS family
- 支援 reference board 選擇
- 支援 profile 作為輔助 mapping

### 3.2 profile resolver
- 先支援已知 profile lineage
- 例如 `968375GWO -> 968375GO, WLAN, WL25DX`
- 預留後續擴充其他 family 的接口

## Phase 4：DTS 草稿生成

### 4.1 模板策略
- 決定用 reference DTS + patch rule
- 或 reference DTS + structured renderer
- phase 1 以 68575 最少風險方案優先

### 4.2 生成結果
- 產出可編輯 DTS 草稿
- 標出待補欄位
- 盡量保留和 reference board 的可追溯性

### 4.3 驗證
- 檢查必填欄位
- 輸出缺件報告
- 標示哪些輸入是由 Excel 取得、哪些仍需 HW 補件

## Phase 5：專案骨架

### 5.1 建立專案目錄
- `docs/`
- `templates/`
- `schemas/`
- `samples/`
- `src/` 或等價實作目錄

### 5.2 準備範例
- 68575 sample diff-folder
- sample manifest
- sample HW diff-table
- sample generated DTS draft

## Phase 6：文件交付

### 6.1 docs 內容
- 使用方式
- diff-folder 規格
- HW 差異表填寫說明
- 68575 參考板型與 profile 說明

### 6.2 使用流程
- HW 填差異表
- 專案整理 diff-folder
- 工具 ingest
- 產出 DTS 草稿
- 回看缺件報告
