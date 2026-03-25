---
name: gen-dts
description: >
  從 schematic-reasoner 或 dtsin_<project> 中已收斂的硬體證據，
  生成 evidence-driven DTS、review context 與 validation artifact。
---

# Gen DTS

## 這個 skill 的本質

這是一個 **把已收斂的硬體事實轉成可編譯 DTS** 的 skill。

它不是第一線的 schematic tracing skill。
若任務還停留在「看 PDF、找線、辨識 IC、判讀 pull-up / active-low / swap」，
應先交給 `../schematic-reasoner/SKILL.md`。

`gen-dts` 的職責，是把已經整理好的 evidence / table / schema / ask-me 結果，
保守地轉成 active DTS 與 review-friendly artifact。

## 輸入優先順序

1. **local structured evidence**
   - `tables/*.csv`
   - `.analysis/*.txt`
   - `*.schema.yaml`
   - `validation / unresolved / coverage` 類 artifact

2. **schematic-reasoner 輸出**
   - `VERIFIED / INCOMPLETE / EXCLUDED`
   - signal table
   - device table
   - topic report
   - clarification answers

3. **public reference DTS**
   - 只能拿來做 public pattern / ordering / naming template

4. **user confirmation**
   - 用來解決 schematic 無法直接定案、但使用者能提供的 board policy

5. **board DTS answer key**
   - 不可當成 active generation input
   - 只能拿來事後 diff / human review

## 核心責任

這個 skill 必須能做：

1. **Sufficiency gate**
   - 先判斷 evidence 是否足以產生某個 node / property
   - 不足時不要硬生 DTS

2. **Evidence → DTS mapping**
   - 把 signal / device / topology / behavior 收斂成 DTS binding 可用的 facts

3. **Active vs retained 決策**
   - `VERIFIED` 才能進 active DTS
   - `INCOMPLETE` 視情況保留成 review context 或提問
   - `EXCLUDED` 不應生成

4. **Reference-aware rendering**
   - 可沿用 public ref DTS 的 node order、結構模板、已證成裝置的命名風格
   - 不可直接抄 board answer key 值

5. **Targeted questions**
   - 只在 blocker 或高影響歧義時問使用者

6. **Validation output**
   - 產出 DTS 後，要能回報哪裡是 active、哪裡是 retained、哪裡仍 unresolved

## 核心原則

1. **Only VERIFIED facts become active DTS**
   - active DTS 不可建立在「看起來像」或「ref 裡有」之上。

2. **Reference is template, not truth**
   - public ref DTS 可以提供 public pattern，但 board fact 仍須回原始證據。

3. **Retained context is for review, not execution**
   - retained snippet / note 可以協助人工 review，但不能冒充 active code。

4. **No silent guesses**
   - 缺證據時要留白、保留、或提問；不能靜默 fallback 成成功。

5. **Identity before behavior**
   - 先證成 device / bus / signal identity，再決定 binding/property。

## 標準工作流

### Step 1. 盤點輸入 artifact

至少盤點：

- `manifest.yaml`
- `tables/*.csv`
- `.analysis/*.txt`
- public reference DTS（若有）
- `schematic-reasoner` 或 ask-me 的已回答結果（若有）

### Step 2. 建立 generation gate

對每個候選 node / property，先分成：

- `active_candidate`
- `retained_candidate`
- `question_required`
- `excluded`

### Step 3. 做 DTS node plan

至少先規劃：

- 哪些 top-level node 會 active
- 哪些 child node 會 active
- 哪些 property 僅能 retained
- 哪些 naming/order 允許沿用 public ref

### Step 4. 套用 domain rules

至少要檢查：

- `network topology`
- `wan_sfp`
- `usb_ctrl`
- `buttons`
- `led_ctrl`
- `i2c child devices`
- `ext_pwr_ctrl`
- `&gpioc` hogs
- `ethphytop` / `mdio_bus` / lane-swap 相關 gating

### Step 5. 產生 DTS + review context

輸出時要明確區分：

- active DTS
- retained public-reference context
- unresolved / blocked items

### Step 6. 驗證

至少回報：

- syntax / parser 結果
- 哪些 block 是由 `VERIFIED` evidence 生成
- 哪些 block 因證據不足未生成
- 哪些 block 仍需 ask-me 或人工 review

## Ask-me 觸發條件

只有以下情況才問：

- 缺的不是 tracing，而是 **board policy / software policy**
- 同一個 binding 有兩個以上都合理的實作
- 不問就會導致 active DTS 內容不同

典型例子：

- button 的 software behavior 要沿用哪一組
- 某個 ref-only child block 是否要 retained
- 同一顆已證成 device，要沿用哪個 public ref label / behavior template

## 輸出格式

建議至少輸出四段：

1. `Active DTS decisions`
2. `Retained review context`
3. `Blocked / unresolved items`
4. `Questions for user`

若任務是整板輸出，也可附：

- `Generated blocks summary`
- `Coverage / unresolved summary`
- `Validation summary`

## 重要判準

### Public reference 可以幫什麼

- top-level node order
- 已證成裝置的 label / naming style
- 已證成 parent node 之下的 behavior template
- public compatible / binding pattern

### Public reference 不可以幫什麼

- 板上是否真的有這個裝置
- 某條 GPIO 是否真的接到這個功能
- 某個 second path / second port 是否真的 populated
- 某個 property 是否應該 active

## 反模式

- 直接拿 answer key DTS 填 active node/property
- 因 signal 順序很像，就自動推 LED child mapping
- 因 controller side signal 存在，就自動認定 board port populated
- 因某個 ref block 存在，就把整段 retained 變 active
- 用 `INCOMPLETE` facts 偷渡 active DTS

## 參考

- `../schematic-reasoner/SKILL.md`
- `references/dts-generation-playbook.md`
