# DTS Generation Playbook

這份 playbook 專門描述：

- 已有硬體證據時，怎麼保守地生成 DTS
- 哪些事實可以升成 active DTS
- 哪些事實只能 retained / 問使用者 / 排除

## 1. 分層要清楚

請把兩個 skill 分開看：

- `schematic-reasoner`：負責看圖、找線、識別器件、理解電氣語義
- `gen-dts`：負責把已收斂的事實轉成 DTS

不要把「還沒 tracing 清楚」的工作，偷塞進 `gen-dts` 內解決。

## 2. Generation gate matrix

| 狀態 | 可否進 active DTS | 建議動作 |
|---|---|---|
| `VERIFIED` | 可以 | 生成 active node/property |
| `INCOMPLETE` | 不可以 | 保留 review context 或 ask-me |
| `EXCLUDED` | 不可以 | 明確排除，不輸出 |

## 3. Public reference 的正確用途

public reference DTS 可以幫忙：

- 對齊 node order
- 提供 public binding pattern
- 提供已證成裝置的 naming style
- 提供已證成 parent node 的 behavior template

但不能拿來：

- 決定板上是否有某顆 device
- 決定某條 GPIO 的 board meaning
- 決定某個未證成 child block 應該 active

## 4. Retained context 的用途

retained context 的目的，是讓人工 review 時能看到：

- public ref 原本長什麼樣
- 目前 active DTS 少了哪些欄位
- 哪些欄位是因證據不足而刻意不生成

retained context 不應：

- 偽裝成 active code
- 混淆已證成與未證成的邊界

## 5. 重要 domain rules

### Network topology

- 若 `switch_port` / `phy_group` / `port_group` 只有 inferred evidence，
  不可直接驅動 active `switch0` / `ethphytop`。
- lane-swap 只能由獨立 tracing hint 啟用，不能因 inferred GPHY row 自動落地。

### WAN SFP

- `wan_sfp:i2c-bus` 需要 cage-level I2C path 證據。
- `tx-disable` / `pinctrl-*` / ref-only property 必須逐項證成。
- 第二組 SFP 路徑要有獨立 populated evidence，不能只靠 `...1` 命名。

### USB

- `port1-disabled` 需要 board population 證據。
- controller-side `USB1_*` signal name 不等於板上真的有第二個 USB port。

### LED

- `led_ctrl` parent 可由 serial LED controller + board wiring 證成。
- child LED mapping 不可只靠 signal 順序硬猜。
- 若只有 physical LED net 證據，卻沒有完整 logical crossbar mapping，
  要區分 physical LED fact 與 DTS logical LED source。

### Buttons

- button block 先由 GPIO / interrupt 的硬體證據決定是否存在。
- `press/hold/release` 這類 behavior 可沿用 public ref template，
  但前提是 parent button 已被證成。

### I2C / expanders

- 先證 bus，再證 child device address / compatible / pins used。
- 若 reference path 與 compatible 都相符，可沿用 ref label naming。
- naming reuse 不等於 hardware identity；identity 仍要靠 address + compatible + evidence。

### Power / GPIO controllers

- `ext_pwr_ctrl` 只可消費真正 `POWER_CONTROL` 類 signal。
- `USB_POWER` 這類 signal 不可被誤轉成假的 power GPIO。
- `&gpioc` Wi-Fi hog 要從 `PCIE_WIFI` 類 control line 證成，不是從 power block 猜出來。

## 6. Ask-me 只問高影響問題

好的 ask-me 例子：

- 這顆 button 已證成存在，但要沿用哪組 behavior？
- 這個 retained block 要保留到什麼程度？
- 這個 device label 要沿用哪個 public ref naming？

壞的 ask-me 例子：

- 「這樣可以嗎？」
- 「你看一下這個 DTS」

## 7. Validation checklist

gen-dts 完成後，至少要回頭檢查：

- DTS syntax 是否正確
- active block 是否都能回到 `VERIFIED` evidence
- retained block 是否仍保持 non-executing context
- unresolved item 是否真的沒有被偷渡進 active DTS

## 8. BGW720 這次最值得固化的 downstream 規則

- `wan_sfp:i2c-bus` 可以由 cage-level I2C tracing 升成 active
- `usb_ctrl:port1-disabled` 要靠 board-population 證據
- `wakeup-trigger-pin-gpio` 這類 ref-only property 必須單獨證成
- `USB_POWER` 不能混進 `ext_pwr_ctrl`
- `RF_DISABLE_L` / `PEWAKE` 這類 Wi-Fi control line 應落到 `&gpioc` hog
- `gpio@27` 的 naming reuse 必須建立在已證成 identity 上
- `swap` 是可進 schema / report 的一級事實，但要有獨立證據才能影響 active DTS

## 9. 一句話總結

`gen-dts` 的目標不是把 reference 拼起來，
而是把 **已證成的硬體事實** 穩定地轉成 **可審核、可驗證、可維護** 的 DTS。
