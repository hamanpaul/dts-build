# 68575 公版 source code：要幫新案子產生 DTS 前，你應該準備哪些資料

## Executive Summary

在這份 68575 公版樹裡，實際的板級 DTS 目錄是 `kernel/dts/68375/`，而且該目錄用 `wildcard *.dts` 自動收集要編譯的板檔；實務上新增新板 `.dts` 通常不需要再手改這個目錄的 `Makefile`，只要檔名、include 結構和內容正確即可進入 `dtbs` 流程。[^1]

現有 68375/68575 板檔明顯分成「SoC 共用 `68375.dtsi`」、「板型共用 `.dtsi`（例如 `inc/968575DV.dtsi`）」以及「專案最終 `.dts`」三層，所以我最希望你先給我的不是一張空白需求，而是「最接近的 reference board 名稱」以及「你和它的差異表」。[^2]

若要可靠地生成新板 DTS，最少要準備：板名/基底板、DDR 規格、網路拓樸（哪些埠、哪些 PHY/SERDES、WAN/LAN 角色）、所有板級 GPIO/pinmux/bus 對照，以及是否有 SFP、I2C expander、PCIe/Wi-Fi、LED、buttons、voice、USB 等外設。這不是額外加分資訊，而是現有 68375 SoC dtsi 預設把多數網路 block 與 port 設成 `disabled`，最後都是由各板 `.dts` 依硬體實際接法去開啟與補值。[^3]

其中 DDR `memcfg` 不能憑感覺猜；memc driver 會直接從 device tree 讀 `memcfg`，再解碼出實際 DDR 容量與速度，因此記憶體顆粒型別、速度 bin、總位寬、總容量等資料必須準確。[^4]

## 結論先講：我最需要你先給的 3 樣東西

1. **最接近的 reference board 名稱**：例如你的板比較像 `968575DV`、`968575REF1`、`968575SV_PVT1`、`968575DV_2TEN` 還是 `968575DV_V`；這會直接決定我要從哪個現成檔案當模板開始改。[^2]

2. **DDR 規格表**：至少要有 DDR 類型（LPDDR4 / LPDDR5 / DDR4）、速度、位寬、總容量、是否有 SSC；目前樹裡就同時存在 LPDDR4、LPDDR5 和較舊 DDR4 風格的 `memcfg` 寫法。[^4][^5]

3. **完整板級連接表（GPIO / pinmux / bus map）**：因為同一顆 SoC 在不同板上，SFP、I2C mux、按鍵、LED、外接 PHY、PCIe 供電、voice reset/IRQ 都是靠板檔綁到不同 GPIO/pinctrl/bus，不能從 SoC 型號直接反推出來。[^3][^6]

## 68575 DTS 在這棵樹裡的結構

| 層級 | 例子 | 作用 | 你要提供什麼 |
|---|---|---|---|
| SoC 共用層 | `kernel/dts/68375/inc/68375.dtsi` | 定義 68375 的共用 controller、GPIO、I2C、USB、pincontroller、switch/port 骨架；多數網路 block 與 port 預設為 `disabled`。[^3][^7] | 你不需要重畫 SoC 資源，但要告訴我哪些 block 在你板上真的有用。 |
| 板型共用層 | `kernel/dts/68375/inc/968575DV.dtsi` | 把某個板家族共用的 DDR、buttons、I2C expander、USB、LED、PCIe regulator define、外部供電控制整理成可複用模板。[^8] | 若你的板和某個家族高度相似，請直接指出「最像哪個家族」。 |
| 專案最終層 | `968575DV.dts`、`968575DV_2TEN.dts`、`968575DV_V.dts`、`968575REF1.dts`、`968575SV_PVT1.dts` | 啟用實際用到的 port、填入 SFP/PHY/voice/LED/pinctrl/GPIO 差異，必要時在現成板上再疊一層覆寫。[^2][^9] | 你要提供「和 reference board 不同的地方」。 |

## 你要準備的資料清單

### A. 一定要準備的資料

| 資料項目 | 為什麼一定要有 | 你至少要提供的內容 |
|---|---|---|
| **1. 新板名稱與最接近的 reference board** | 現有板檔不是從零開始寫，而是明顯在現成板上分層繼承；例如 `968575DV_2TEN.dts` 直接 include `inc/968575DV.dtsi`，`968575DV_V.dts` 甚至直接 include `968575DV.dts` 再疊 voice 設定。[^2] | 新板 DTS 檔名、`model` 字串、最像哪一塊現有板、以及和它不同的功能/接線。 |
| **2. DDR 規格** | `memory_controller/memcfg` 在多個板檔中都不同，而且 memc driver 會讀這個欄位去推導容量與速度；樹內同時可看到 LPDDR4、LPDDR5、DDR4 等不同組合。[^4][^5] | DDR 類型、speed bin、總位寬、總容量、SSC、是否沿用現有 reference board 的相同記憶體配置。 |
| **3. 網路拓樸** | `68375.dtsi` 的 `xport`、`ethphytop`、`serdes`、`mdio`、`switch0` port skeleton 都是先定義再預設關閉；每塊板才決定哪些 `xphy0..4`、`serdes0/1`、`port_wan`/`port_slan` 被打開，還要指定 `phy-mode`、`phy-handle`、`port-group`、WAN/LAN 角色。[^3] | 每個實體 RJ45/SFP/光口對應到哪個 xphy/serdes、速率能力、WAN/LAN 角色、是否為 optical / copper / USXGMII、是否有 mux/detect。**若要成立第二組 SFP / `serdes1`，必須能獨立證明第二組已裝配的 SFP cage/path 存在；僅靠 `...1` 命名、Reserve 或 service label 不足以成立。** |
| **4. 板級 GPIO / pinmux / bus map** | 同一個功能在不同板上會綁到不同 GPIO 或 pinctrl；例如 SFP 的 lane enable/select、I2C1 的 SDA/SCL、TOD 的 1PPS/8K、serial LED 的 data/clk/mask 都是用具名 pinctrl 條目綁定的。[^6] | 一張表列出：每個功能訊號（reset、interrupt、LOS、TX_DISABLE、LED、按鍵、I2C、SPI CS、USB power…）對應哪個 GPIO / pin / bus、極性是高有效還是低有效。 |

### B. 有某些功能時，還要補的資料

| 功能 | 為什麼需要額外資料 | 你要補哪些資訊 |
|---|---|---|
| **SFP / 光模組** | 現有 dual-SFP 板都要提供 `i2c-bus`、`los-gpio`、`mod-def0-gpio`、`tx-disable-gpio`，有些還多了 `tx-power-gpio`、`tx-power-down-gpio`、`rx-power-gpio`；對應 serdes 端還會綁 `trx`、`lane-enable`、`lane-select`、signal detect pinctrl 與支援速率模式。[^9][^10] | 每個 **已實際裝配** 的 SFP cage 都要提供：I2C bus、MOD_DEF0、LOS、TX_DISABLE、TX_POWER、RX_POWER、TX_POWER_DOWN、signal detect、lane enable/select、支援 1G/2.5G/5G/10G 哪些模式。**若無法證明第二組 cage/path 實際存在，就不能落地第二個 `serdes` / `lan_sfp`。** |
| **外接 XPHY / USXGMII PHY** | `968575DV_2TEN.dts` 顯示如果 serdes 後面不是 SFP 而是外接 USXGMII PHY，就需要 `serdes*_xphy` 節點、MDIO address、`phy-power`、`phy-reset`、有時還要 `phy-magic-gpio` / `phy-link-gpio` 與 lane select 極性。[^11] | 外接 PHY 型號、MDIO 位址、是否用 USXGMII、reset/power GPIO、任何 vendor 特殊 GPIO。 |
| **I2C 裝置 / expander / mux** | 同樣是 68575 板，不同板上 I2C 拓樸差很多：`968575DV` 類板在 `i2c0` 下掛 `pca9557`/`pca9555`/`ina236`，而 `968575SV_PVT1` 類板則在 `i2c0` 下掛 `pca9548` 再把 WAN SFP 走到 mux channel。[^8][^12] | 每顆 I2C device 的 bus、7-bit 地址、用途（boardid expander / WLAN expander / current monitor / mux / EEPROM / SFP EEPROM）。 |
| **LED** | 現有板的 LED 不是只寫 label；還要定義 `serial-shifters-installed`、每顆 LED 的 `crossbar-output`、是否 `active_low`、是否是 `network-leds`、亮度、是否由 USB port trigger。[^8][^13] | 每個 LED 的名稱、對應埠/功能、是否走 serial LED、crossbar index 或 GPIO、active high/low、是否有 default trigger。 |
| **Buttons / Reset / WPS** | `buttons` 節點除了 GPIO/IRQ 外，還包含 reset 長按秒數與 WPS short/long period 等行為參數；而且不同板的按鍵 GPIO 可能完全不同。[^8][^14] | Reset/WPS/其他按鍵的 GPIO、IRQ edge、長按秒數需求、是否要 factory reset、是否要 WPS。 |
| **PCIe / Wi-Fi 供電** | 68375 板檔普遍用 preprocessor define 餵進 `bcm_pcie_regulator.dtsi`，由它生成 `regulator-fixed` 與 `brcm,supply-names`；因此 slot-to-enable GPIO 與極性一定要知道，而且可能多個 PCIe slot 共用同一個 power rail。[^8][^15][^16] | 每個 PCIe slot 是否使用、enable GPIO、極性、高/低有效、是否共用電源。 |
| **Voice / SLIC / PCM / TOD** | voice 板不只要打開 `bcm_voice`，還要決定 `sliclist`、SPI 裝置、`reset-gpio`、SLIC power GPIO、PCM pinctrl，甚至 TOD 的 1PPS/8K pin 也要補。[^9][^17][^18] | SLIC/SLAC 型號、SPI CS、reset GPIO、power GPIO、IRQ GPIO、PCM pin、是否需要 TOD/SyncE。 |
| **USB** | 有些板把 `usb_ctrl` 設為 `xhci-enable` 並兩個埠都開，有些板則額外標註 `port1-disabled`；電源控制 pin 也要綁到 pinctrl。[^8][^9][^12] | 有幾個 USB 埠、哪個要 disable、power enable pin、是否要 LED trigger。 |
| **其他電源控制** | `ext_pwr_ctrl` 這類節點會額外定義 PHY 或外設的 power control GPIO。[^8] | 哪些外設需要額外 power GPIO，GPIO 編號與極性。 |

## 我建議你直接丟給我的資料模板

下面這份模板，已經對齊目前 68375/68575 板檔真正會用到的欄位；你可以把不知道的欄位先留空，但**至少要把 reference board 和差異寫清楚**。[^2][^3][^8][^9]

```yaml
project:
  dts_file_name: 968575NEWBOARD.dts
  model: 968575NEWBOARD
  reference_board: 968575DV   # 或 968575REF1 / 968575SV_PVT1 / 968575DV_2TEN / 968575DV_V
  differences_from_reference:
    - WAN 改成單 SFP / 雙 SFP / copper / USXGMII
    - DDR 改成 LPDDR5 16Gb
    - 有/沒有 voice
    - 有/沒有 PCIe Wi-Fi
    - 哪些 GPIO / pin 改了

ddr:
  type: LPDDR4          # LPDDR4 / LPDDR5 / DDR4
  speed_bin: BP1_DDR_SPEED_2133_36_39_39
  width: BP1_DDR_WIDTH_32BIT
  total_size: BP1_DDR_TOTAL_SIZE_16Gb
  ssc: BP1_DDR_SSC_CONFIG_1

network:
  ports:
    - name: port_xgphy0
      present: true
      role: LAN
      phy: xphy0
      speed: 10G/5G/2.5G/1G
    - name: port_wan
      present: true
      role: WAN
      source: serdes0 / phy_wan_serdes
      media: SFP / PON / copper / USXGMII
  serdes:
    serdes0:
      attached_device: wan_sfp / serdes0_xphy / phy_wan_serdes
      lane_enable_gpio:
      lane_select_gpio:
      supported_modes: [1000-Base-X, 2500-Base-X, 5000-Base-R, 10000-Base-R]
    serdes1:
      attached_device:
      lane_enable_gpio:
      lane_select_gpio:
      supported_modes: []

sfp:
  wan_sfp:
    present: true
    i2c_bus: i2c0 / i2c0_mux1
    los_gpio:
    mod_def0_gpio:
    tx_disable_gpio:
    tx_power_gpio:
    tx_power_down_gpio:
    rx_power_gpio:
    signal_detect_pinctrl:
  lan_sfp:
    present: false

external_phys:
  - name: serdes0_xphy
    present: false
    mdio_addr:
    interface: USXGMII-S
    phy_power_gpio:
    phy_reset_gpio:
    phy_magic_gpio:
    phy_link_gpio:

i2c:
  buses:
    - name: i2c0
      pinctrl:
    - name: i2c1
      pinctrl:
  devices:
    - type: pca9557
      address: 0x1e
      purpose: boardid
    - type: pca9555
      address: 0x20
      purpose: gpio expander
    - type: pca9548
      address: 0x72
      purpose: i2c mux
    - type: ina236
      address: 0x41
      purpose: current monitor

spi:
  hsspi_enabled: true
  devices:
    - type: bcm-spi-voice
      cs:
      reset_gpio:

buttons:
  reset_button:
    gpio:
    irq:
    active_level:
    hold_seconds_to_factory_reset: 5
  ses_button:
    gpio:
    irq:
    short_period:
    long_period: 3

leds:
  serial_shifters_installed: 3
  network_led_map:
    xphy0: [led0, led1]
    xphy1: [led2, led3]
  custom_leds:
    - label: WAN
      crossbar_output:
      active_low: true
      default_trigger:

pcie:
  slots:
    - id: pcie0
      enabled: true
      power_gpio:
      polarity:
      shared_with:
    - id: pcie1
      enabled: false
      power_gpio:
      polarity:
      shared_with:

voice:
  enabled: false
  sliclist:
  slic_power_gpio:
  pcm_pins:
  tod_pins:

usb:
  enabled: true
  xhci_enable: true
  port1_disabled: false
  power_pins:

misc_power:
  phy_power_gpio:
  ext_power_gpios: []

pinmux_gpio_map:
  - signal: wan0_lbe
    pinctrl_name:
    gpio_or_pin:
    polarity:
  - signal: bsc_m1_sda
    pinctrl_name:
    gpio_or_pin:
    polarity:
```

## 我建議你怎麼準備資料，效率最高

1. **先選一塊最像的現成板**：如果你是 dual-SFP 類型，可先從 `968575DV.dts` 或 `968575REF1.dts` 看起；若是 serdes 後面接外部 USXGMII PHY，`968575DV_2TEN.dts` 是更好的起點；若要 voice，`968575DV_V.dts` 或 `968575REF1.dts` 會更接近；若 WAN SFP 走 I2C mux，請以 `968575SV_PVT1.dts` 為起點。[^9][^10][^11][^12][^17] **但這些板檔只適合拿來看結構與欄位，不可直接當答案卷抄回新板 DTS。**

2. **把差異整理成一張表，而不是只給 schematic PDF**：因為實際要寫進 DTS 的資訊是 `GPIO / polarity / pinctrl / bus / MDIO address / port role / LED crossbar` 這類離散欄位；若你能把 schematic 轉成表格，我可以更快把它翻成 DTS。現有板檔正是以這種粒度在描述硬體。[^3][^6][^8][^9]

3. **優先提供「有變的地方」**：由於 68375 SoC 共用層和板型共用層已經封裝了大量固定資源，真正需要人工判斷的是你和 reference board 不同的部分。[^2][^3][^8]

4. **若你只想先拿到第一版可編譯 DTS**：只要先給我 reference board、DDR 規格、網路拓樸、SFP/PHY/PCIe/voice 是否存在，以及 GPIO/pinmux/bus 差異，我就能先起出一版；其餘像 LED 亮度、USB trigger、細節 label 可第二輪再補。現有板檔也確實把「先讓 block enable，再慢慢補齊外設細節」分散在不同層級。[^3][^8][^9]

## 68375/68575 目錄下可直接拿來當模板的板型

| 你的新板比較像什麼 | 建議先看哪個檔 | 理由 |
|---|---|---|
| dual-SFP、GPIO expander、多數功能完整 | `kernel/dts/68375/968575DV.dts` | 有雙 SFP、兩個 serdes、外接 GPIO expander、LED、switch port 對映。[^9] |
| dual-SFP + voice + TOD | `kernel/dts/68375/968575REF1.dts` | 除了 dual-SFP，還把 `bcm_voice`、`slicpowerctl`、TOD、LED、PCIe 都配齊。[^17][^19] |
| dual-SFP，但 WAN SFP 走 I2C mux | `kernel/dts/68375/968575SV_PVT1.dts` | 顯示 `pca9548` I2C mux 與 `i2c0_mux1` 的寫法。[^12] |
| serdes 接外部 USXGMII PHY，而不是直接接 SFP | `kernel/dts/68375/968575DV_2TEN.dts` | 完整示範 `serdes*_xphy`、`USXGMII-S`、PHY power/reset/link/magic GPIO。[^11] |
| voice 只是 DV 板上的一個衍生版本 | `kernel/dts/68375/968575DV_V.dts` | 示範在既有板上再疊一層 voice/SLIC 覆寫。[^2][^18] |
| LPDDR5 板 | `kernel/dts/68375/968575REF4.dts` | 顯示 LPDDR5 與不同按鍵 GPIO 的變化。[^5][^14] |
| 舊式 DDR4 風格 | `kernel/dts/68375/968375REF2.dts` | 顯示非 LPDDR4/5 風格的 `memcfg` 寫法。[^5] |

## Confidence Assessment

**高信心部分：**

- 這份清單對「產生 68375/68575 新板 DTS 需要哪些輸入」的判斷，直接來自現有板檔與共用 dtsi 內容，因此對 DDR、port/PHY、GPIO/pinctrl、SFP、I2C、PCIe、LED、buttons、voice、USB 等欄位的要求，我有高信心。[^3][^4][^8][^9][^11][^12][^15][^17]

**中等信心部分：**

- 這份報告聚焦在 `kernel/dts/68375/` 這條 DTS 生成路徑；我已確認新增 `.dts` 會被既有 `dtbs` 流程自動收進來，但我沒有把 image profile、NVRAM `boardid`、量產鏡像命名等 DTS 以外的產品整合流程完整追到最後一層，因此如果你的新案子還牽涉 bootloader 選板、image tag、量產腳本，可能還會需要額外的非-DTS 資料。[^1]

**最實用的結論：**

- 如果你現在就要我開始起草新板 DTS，**最少請先給我：`reference board + DDR 規格 + network topology + GPIO/pinmux/bus 差異表`**。只要這四組資料夠完整，我就能先做出第一版可信的 DTS 草稿。[^2][^3][^4][^6]

## Footnotes

[^1]: `/home/build20/BCM-68575-BDK/build/Makefile:684-694` and `/home/build20/BCM-68575-BDK/kernel/dts/68375/Makefile:6-11` (commit `02a6ec7749a461a0c88759be15fb394e6cb168ef`).

[^2]: `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575DV.dts:1-5`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575DV_2TEN.dts:1-5`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575DV_V.dts:1-6`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575REF1.dts:1-5` (commit `02a6ec7749a461a0c88759be15fb394e6cb168ef`).

[^3]: `/home/build20/BCM-68575-BDK/kernel/dts/68375/inc/68375.dtsi:639-669`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/inc/68375.dtsi:671-754`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/inc/68375.dtsi:770-869`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575DV.dts:75-167`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575REF1.dts:208-310` (commit `02a6ec7749a461a0c88759be15fb394e6cb168ef`).

[^4]: `/home/build20/BCM-68575-BDK/bcmdrivers/opensource/misc/memc/impl2/bcm_memc.c:36-58`, `/home/build20/BCM-68575-BDK/bcmdrivers/opensource/misc/memc/impl2/bcm_memc.c:72-125`, `/home/build20/BCM-68575-BDK/bcmdrivers/opensource/misc/memc/impl2/bcm_memc.c:139-169`, `/home/build20/BCM-68575-BDK/bcmdrivers/opensource/include/bcm963xx/bcmbca_memc_dt_bindings.h:120-169` (commit `02a6ec7749a461a0c88759be15fb394e6cb168ef`).

[^5]: `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575REF1.dts:7-14`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575REF4.dts:7-14`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968375REF2.dts:7-13`, `/home/build20/BCM-68575-BDK/bcmdrivers/opensource/include/bcm963xx/bcmbca_memc_dt_bindings.h:120-169` (commit `02a6ec7749a461a0c88759be15fb394e6cb168ef`).

[^6]: `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575DV.dts:7-20`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575DV.dts:54-72`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575REF1.dts:86-90`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/inc/68375_pinctrl.dtsi:449-456`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/inc/68375_pinctrl.dtsi:839-846`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/inc/68375_pinctrl.dtsi:1086-1098`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/inc/68375_pinctrl.dtsi:1371-1378`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/inc/68375_pinctrl.dtsi:2051-2058` (commit `02a6ec7749a461a0c88759be15fb394e6cb168ef`).

[^7]: `/home/build20/BCM-68575-BDK/kernel/dts/68375/inc/68375.dtsi:81-91`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/inc/68375.dtsi:349-358`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/inc/68375.dtsi:372-388`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/inc/68375.dtsi:407-431`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/inc/68375.dtsi:499-530` (commit `02a6ec7749a461a0c88759be15fb394e6cb168ef`).

[^8]: `/home/build20/BCM-68575-BDK/kernel/dts/68375/inc/968575DV.dtsi:4-11`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/inc/968575DV.dtsi:13-41`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/inc/968575DV.dtsi:52-90`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/inc/968575DV.dtsi:92-111`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/inc/968575DV.dtsi:123-126`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/inc/968575DV.dtsi:148-296`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/inc/968575DV.dtsi:298-332` (commit `02a6ec7749a461a0c88759be15fb394e6cb168ef`).

[^9]: `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575DV.dts:7-20`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575DV.dts:23-36`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575DV.dts:40-51`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575DV.dts:83-128`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575DV.dts:131-167` (commit `02a6ec7749a461a0c88759be15fb394e6cb168ef`).

[^10]: `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575REF1.dts:46-75`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575REF1.dts:93-105`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575REF1.dts:226-267`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575SV_PVT1.dts:17-41`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575SV_PVT1.dts:186-208` (commit `02a6ec7749a461a0c88759be15fb394e6cb168ef`).

[^11]: `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575DV_2TEN.dts:15-64`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575DV_2TEN.dts:67-102` (commit `02a6ec7749a461a0c88759be15fb394e6cb168ef`).

[^12]: `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575SV_PVT1.dts:17-41`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575SV_PVT1.dts:71-103`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575SV_PVT1.dts:111-129` (commit `02a6ec7749a461a0c88759be15fb394e6cb168ef`).

[^13]: `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575REF1.dts:274-310`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575REF1.dts:312-462`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/inc/968575DV.dtsi:151-296` (commit `02a6ec7749a461a0c88759be15fb394e6cb168ef`).

[^14]: `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575REF1.dts:16-44`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575REF4.dts:17-45`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/inc/968575DV.dtsi:13-41` (commit `02a6ec7749a461a0c88759be15fb394e6cb168ef`).

[^15]: `/home/build20/BCM-68575-BDK/kernel/dts/bcm_pcie_regulator.dtsi:71-153` (commit `02a6ec7749a461a0c88759be15fb394e6cb168ef`).

[^16]: `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575REF1.dts:473-501`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575SV_PVT1.dts:247-275`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/inc/968575DV.dtsi:303-332` (commit `02a6ec7749a461a0c88759be15fb394e6cb168ef`).

[^17]: `/home/build20/BCM-68575-BDK/kernel/dts/bcm_voice.dtsi:33-64`, `/home/build20/BCM-68575-BDK/kernel/dts/bcm_voice.dtsi:73-76`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575REF1.dts:78-90`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575REF1.dts:165-177` (commit `02a6ec7749a461a0c88759be15fb394e6cb168ef`).

[^18]: `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575DV_V.dts:8-14`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575DV_V.dts:17-23`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575DV_V.dts:25-43`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575DV_V.dts:45-94` (commit `02a6ec7749a461a0c88759be15fb394e6cb168ef`).

[^19]: `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575REF1.dts:115-163`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575REF1.dts:468-501`, `/home/build20/BCM-68575-BDK/kernel/dts/68375/968575REF4.dts:144-150` (commit `02a6ec7749a461a0c88759be15fb394e6cb168ef`).
