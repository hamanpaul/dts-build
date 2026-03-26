from __future__ import annotations

import asyncio
from unittest.mock import patch

from dtsbuild.agents.auditor import _classify_signal_role, _read_gpio_table, run_auditor
from dtsbuild.schema_io import load_schema


def test_read_gpio_table_keeps_meaningful_pcie_wifi_rows(tmp_path):
    csv_path = tmp_path / "gpio_led.csv"
    csv_path.write_text(
        "\n".join(
            [
                "category,name,signal,pin_or_gpio,polarity,io,notes",
                "gpio,PCIE13_WiFi_PWR_DIS,NA,GPIO_11,active_low,O,NA",
                "gpio,PCIE02_WiFi_PWR_DIS,NA,GPIO_51,active_low,O,NA",
                "gpio,Not used,2G_RF_DISABLE_L,GPIO_76,active_low,O,Low=disable",
                "gpio,Not used,5G_PEWAKE,GPIO_79,,O,5G_PEWAKE",
                "gpio,Not used,GPIO_5GRFIC,GPIO_54,active_low,O,Low=enable",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    rows = _read_gpio_table(csv_path)

    assert any(row["name"] == "PCIE13_WiFi_PWR_DIS" for row in rows)
    assert any(row["name"] == "PCIE02_WiFi_PWR_DIS" for row in rows)
    assert any(row["signal"] == "2G_RF_DISABLE_L" for row in rows)
    assert any(row["signal"] == "5G_PEWAKE" for row in rows)
    assert any(row["signal"] == "GPIO_5GRFIC" for row in rows)


def test_classify_signal_role_marks_aux_wifi_controls_as_pcie_wifi():
    assert _classify_signal_role("PCIE13_WiFi_PWR_DIS") == "PCIE_WIFI"
    assert _classify_signal_role("2G_RF_DISABLE_L") == "PCIE_WIFI"
    assert _classify_signal_role("6G_PEWAKE") == "PCIE_WIFI"
    assert _classify_signal_role("GPIO_5GRFIC") == "PCIE_WIFI"


def test_run_auditor_records_grouped_pcie_power_signal_from_gpio_table(tmp_path):
    csv_path = tmp_path / "gpio_led.csv"
    csv_path.write_text(
        "\n".join(
            [
                "category,name,signal,pin_or_gpio,polarity,io,notes",
                "gpio,PCIE13_WiFi_PWR_DIS,NA,GPIO_11,active_low,O,NA",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    schema_path = tmp_path / "schema.yaml"

    asyncio.run(run_auditor({}, csv_path, schema_path))

    schema = load_schema(schema_path)
    signal = next(sig for sig in schema.signals if sig.name == "PCIE13_WiFi_PWR_DIS")

    assert signal.soc_pin == "GPIO_11"
    assert signal.role == "PCIE_WIFI"
    assert signal.status == "VERIFIED"
    assert signal.provenance.method == "gpio_table"


def test_run_auditor_records_usb_signals_from_blockdiag_and_page_scan(tmp_path):
    csv_path = tmp_path / "gpio_led.csv"
    csv_path.write_text(
        "category,name,signal,pin_or_gpio,polarity,io,notes\n",
        encoding="utf-8",
    )
    (tmp_path / "blockdiag.csv").write_text(
        "\n".join(
            [
                "domain,interface,present,controller,endpoint,page_ref,notes",
                "usb,usb2,true,usb2,usb,mainboard:15,USB subsystem present",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    schema_path = tmp_path / "schema.yaml"
    indices = {
        "page_indices": {
            "mainboard": {
                15: "\n".join(
                    [
                        "M33   USB0_PWRON_N",
                        "USB1_PWRON    M31",
                        "USB0_SSRXN   H32",
                    ]
                )
            }
        },
        "tag_index": {},
        "refdes_index": {},
        "connector_index": {},
    }

    asyncio.run(run_auditor(indices, csv_path, schema_path))

    schema = load_schema(schema_path)
    usb_names = {sig.name for sig in schema.signals}

    assert {"USB0_PWRON_N", "USB1_PWRON", "USB0_SSRXN"} <= usb_names
    for name in ("USB0_PWRON_N", "USB1_PWRON", "USB0_SSRXN"):
        signal = next(sig for sig in schema.signals if sig.name == name)
        assert signal.role.startswith("USB")
        assert signal.status == "VERIFIED"
        assert signal.provenance.method == "blockdiag+page_scan"


def test_run_auditor_records_usb_port1_disabled_hint_from_single_port_page(tmp_path):
    csv_path = tmp_path / "gpio_led.csv"
    csv_path.write_text(
        "category,name,signal,pin_or_gpio,polarity,io,notes\n",
        encoding="utf-8",
    )
    (tmp_path / "blockdiag.csv").write_text(
        "domain,interface,present,controller,endpoint,page_ref,notes\n"
        "usb,usb2,true,usb2,usb,mainboard:15,USB subsystem present\n",
        encoding="utf-8",
    )
    schema_path = tmp_path / "schema.yaml"
    indices = {
        "page_indices": {
            "mainboard": {
                15: "\n".join(
                    [
                        "USB0_VBUS",
                        "USB0_DP",
                        "USB0_DM",
                        "USB0_SSRXN",
                        "USB1_DP",
                        "USB1_DM",
                        "USB1_ID",
                        "USB1_PWRON",
                        "J33",
                        "J34",
                    ]
                )
            }
        },
        "tag_index": {},
        "refdes_index": {},
        "connector_index": {},
    }

    asyncio.run(run_auditor(indices, csv_path, schema_path))

    schema = load_schema(schema_path)
    hint_index = {(hint.target, hint.property or ""): hint for hint in schema.dts_hints}
    hint = hint_index[("&usb_ctrl", "port1-disabled")]

    assert hint.value is None
    assert hint.provenance.method == "page_scan"
    assert 15 in hint.provenance.pages


def test_run_auditor_records_uart_only_with_header_context(tmp_path):
    csv_path = tmp_path / "gpio_led.csv"
    csv_path.write_text(
        "category,name,signal,pin_or_gpio,polarity,io,notes\n",
        encoding="utf-8",
    )
    schema_path = tmp_path / "schema.yaml"
    indices = {
        "page_indices": {
            "mainboard": {
                7: "\n".join(
                    [
                        "P301V-04-SMT-G1-RT",
                        "4PX1R",
                        "UART0_SOUT     GPIO_14",
                        "UART0_SIN      GPIO_15",
                    ]
                )
            }
        },
        "tag_index": {},
        "refdes_index": {},
        "connector_index": {},
    }

    asyncio.run(run_auditor(indices, csv_path, schema_path))

    schema = load_schema(schema_path)
    uart = {sig.name: sig for sig in schema.signals if sig.role == "UART"}

    assert set(uart) == {"UART0_SOUT", "UART0_SIN"}
    assert uart["UART0_SOUT"].soc_pin == "GPIO_14"
    assert uart["UART0_SIN"].soc_pin == "GPIO_15"
    assert uart["UART0_SOUT"].provenance.method == "page_scan"


def test_run_auditor_records_wan_sfp_i2c_hint_from_page_scan(tmp_path):
    csv_path = tmp_path / "gpio_led.csv"
    csv_path.write_text(
        "category,name,signal,pin_or_gpio,polarity,io,notes\n",
        encoding="utf-8",
    )
    schema_path = tmp_path / "schema.yaml"
    indices = {
        "page_indices": {
            "mainboard": {
                14: "\n".join(
                    [
                        "U6",
                        "I2C Address: 0XA0/A2",
                        "SFP_SCL",
                        "SFP_SDA",
                        "SCL",
                        "SDA_0",
                    ]
                )
            }
        },
        "tag_index": {},
        "refdes_index": {},
        "connector_index": {},
    }

    asyncio.run(run_auditor(indices, csv_path, schema_path))

    schema = load_schema(schema_path)
    hint_index = {(hint.target, hint.property or ""): hint for hint in schema.dts_hints}
    hint = hint_index[("wan_sfp", "i2c-bus")]

    assert hint.value == "<&i2c0>"
    assert hint.provenance.method == "page_scan"
    assert 14 in hint.provenance.pages


def test_run_auditor_records_network_topology_hints_from_network_table(tmp_path):
    csv_path = tmp_path / "gpio_led.csv"
    csv_path.write_text(
        "category,name,signal,pin_or_gpio,polarity,io,notes\n",
        encoding="utf-8",
    )
    (tmp_path / "network.csv").write_text(
        "\n".join(
            [
                "name,present,role,source,phy_handle,phy_mode,phy_group,switch_port,port_group,lane_count,lane_swap_status,notes",
                "lan_gphy0,true,LAN,mainboard: pages 13 14,gphy0,internal-2.5gphy,PHY1,port_xgphy0,slan_sd,1,pending_audit,",
                "lan_gphy3,true,LAN,mainboard: pages 13 14,gphy3,internal-2.5gphy,PHY2,port_xgphy3,slan_sd,1,pending_audit,",
                "wan_10g,true,WAN,mainboard: pages 13 14,xphy10g,xfi,PHY3,port_wan@xpon_ae,xpon_ae,1,pending_audit,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    schema_path = tmp_path / "schema.yaml"

    asyncio.run(run_auditor({}, csv_path, schema_path))

    schema = load_schema(schema_path)
    hint_index = {(hint.target, hint.property or ""): hint for hint in schema.dts_hints}

    assert ("&xport", "status") in hint_index
    assert ("&ethphytop", "xphy0-enabled") in hint_index
    assert ("&ethphytop", "xphy3-enabled") in hint_index
    assert ("&switch0/ports/port_xgphy0", "status") in hint_index
    assert ("&switch0/ports/port_xgphy3", "status") in hint_index
    assert ("&switch0/ports/port_wan@xpon_ae", "status") in hint_index


def test_run_auditor_keeps_inferred_rows_out_of_topology_hints_but_allows_lane_swap_trace(tmp_path):
    csv_path = tmp_path / "gpio_led.csv"
    csv_path.write_text(
        "category,name,signal,pin_or_gpio,polarity,io,notes\n",
        encoding="utf-8",
    )
    (tmp_path / "network.csv").write_text(
        "\n".join(
            [
                "name,present,role,source,phy_handle,phy_mode,phy_group,switch_port,port_group,lane_count,lane_swap_status,notes",
                "lan_gphy0,inferred,LAN,mainboard: pages 13 14,gphy0,internal-2.5gphy,,,,1,pending_audit,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    schema_path = tmp_path / "schema.yaml"
    indices = {
        "page_indices": {"mainboard": {13: "GPHY0_DP0"}},
        "tag_index": {"GPHY0_DP0": [{"pdf_id": "mainboard", "page": 13, "context": "GPHY0_DP0"}]},
        "refdes_index": {},
        "connector_index": {},
    }

    with patch(
        "dtsbuild.agents.auditor.detect_lane_swap",
        return_value={"swap_detected": True, "swap_detail": "Pair 0 swapped", "trace_paths": []},
    ):
        asyncio.run(run_auditor(indices, csv_path, schema_path))

    schema = load_schema(schema_path)
    hint_index = {(hint.target, hint.property or ""): hint for hint in schema.dts_hints}

    assert ("&xport", "status") not in hint_index
    assert ("&switch0/ports/port_xgphy0", "status") not in hint_index
    assert ("&mdio_bus/xphy0", "enet-phy-lane-swap") in hint_index


def test_run_auditor_maps_trace_prefix_lane_swap_to_cpu_xphy_index(tmp_path):
    csv_path = tmp_path / "gpio_led.csv"
    csv_path.write_text(
        "category,name,signal,pin_or_gpio,polarity,io,notes\n",
        encoding="utf-8",
    )
    (tmp_path / "network.csv").write_text(
        "\n".join(
            [
                "name,present,role,source,phy_handle,phy_mode,phy_group,switch_port,port_group,lane_count,lane_swap_status,trace_prefix,notes",
                "lan_gphy0,true,LAN,mainboard: pages 13 14,gphy1,internal-2.5gphy,,,,1,pending_audit,GPHY0,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    schema_path = tmp_path / "schema.yaml"
    indices = {
        "page_indices": {"mainboard": {13: "GPHY0_DP0"}},
        "tag_index": {"GPHY0_DP0": [{"pdf_id": "mainboard", "page": 13, "context": "GPHY0_DP0"}]},
        "refdes_index": {},
        "connector_index": {},
    }

    with patch(
        "dtsbuild.agents.auditor.detect_lane_swap",
        return_value={"swap_detected": True, "swap_detail": "Pair 0 swapped", "trace_paths": []},
    ):
        asyncio.run(run_auditor(indices, csv_path, schema_path))

    schema = load_schema(schema_path)
    hint_index = {(hint.target, hint.property or ""): hint for hint in schema.dts_hints}

    assert ("&mdio_bus/xphy1", "enet-phy-lane-swap") in hint_index
    assert ("&mdio_bus/xphy0", "enet-phy-lane-swap") not in hint_index


def test_run_auditor_emits_control_plane_and_switch0_inventory_hints_from_inventory_notes(tmp_path):
    csv_path = tmp_path / "gpio_led.csv"
    csv_path.write_text(
        "category,name,signal,pin_or_gpio,polarity,io,notes\n",
        encoding="utf-8",
    )
    (tmp_path / "network.csv").write_text(
        "\n".join(
            [
                "name,present,role,source,phy_handle,phy_mode,phy_group,switch_port,port_group,lane_count,lane_swap_status,notes",
                (
                    'lan_gphy0,inferred,LAN,mainboard: pages 2 13 14,gphy0,internal-2.5gphy,,,,1,pending_audit,'
                    '"Derived from concrete GPHY differential net labels on schematic pages. '
                    'CPU datasheet validates XPORT inventory '
                    'port_xgphy0,port_xgphy1,port_xgphy2,port_xgphy3,port_xgphy4."'
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    schema_path = tmp_path / "schema.yaml"

    asyncio.run(run_auditor({}, csv_path, schema_path))

    schema = load_schema(schema_path)
    hint_index = {(hint.target, hint.property or ""): hint for hint in schema.dts_hints}

    assert ("&xport", "status") in hint_index
    assert ("&ethphytop", "xphy0-enabled") in hint_index
    assert ("&ethphytop", "xphy4-enabled") in hint_index
    assert ("&mdio_bus/xphy0", "status") in hint_index
    assert ("&mdio_bus/xphy4", "status") in hint_index
    assert ("&switch0/ports/port_xgphy0", "status") in hint_index
    assert hint_index[("&switch0/ports/port_xgphy0", "status")].value == '"disabled"'
    assert ("&switch0/ports/port_xgphy4", "status") in hint_index
    assert hint_index[("&switch0/ports/port_xgphy4", "status")].value == '"disabled"'


def test_run_auditor_emits_internal_switch0_xgphy4_for_wan_10g_row(tmp_path):
    csv_path = tmp_path / "gpio_led.csv"
    csv_path.write_text(
        "category,name,signal,pin_or_gpio,polarity,io,notes\n",
        encoding="utf-8",
    )
    (tmp_path / "network.csv").write_text(
        "\n".join(
            [
                "name,present,role,source,phy_handle,phy_mode,phy_group,switch_port,port_group,lane_count,lane_swap_status,notes",
                "wan_10g,true,WAN,mainboard: pages 13 14,xphy10g,xfi,,port_wan@xpon_ae,xpon_ae,1,pending_audit,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    schema_path = tmp_path / "schema.yaml"

    asyncio.run(run_auditor({}, csv_path, schema_path))

    schema = load_schema(schema_path)
    hint_index = {(hint.target, hint.property or ""): hint for hint in schema.dts_hints}

    assert ("&switch0/ports/port_xgphy4", "status") in hint_index
    assert hint_index[("&switch0/ports/port_xgphy4", "status")].value == '"okay"'
    assert ("&switch0/ports/port_wan@xpon_ae", "status") in hint_index


def test_run_auditor_skips_inventory_disabled_for_active_internal_xgphy(tmp_path):
    csv_path = tmp_path / "gpio_led.csv"
    csv_path.write_text(
        "category,name,signal,pin_or_gpio,polarity,io,notes\n",
        encoding="utf-8",
    )
    (tmp_path / "network.csv").write_text(
        "\n".join(
            [
                "name,present,role,source,phy_handle,phy_mode,phy_group,switch_port,port_group,lane_count,lane_swap_status,trace_prefix,notes",
                (
                    'lan_gphy0,true,LAN,mainboard: pages 2 13 14,gphy1,internal-2.5gphy,,,,1,pending_audit,GPHY0,'
                    '"Derived from concrete GPHY differential net labels on schematic pages. '
                    'CPU datasheet validates XPORT inventory '
                    'port_xgphy0,port_xgphy1,port_xgphy2,port_xgphy3,port_xgphy4."'
                ),
                "wan_10g,true,WAN,mainboard: pages 13 14,xphy10g,xfi,,port_wan@xpon_ae,xpon_ae,1,pending_audit,,",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    schema_path = tmp_path / "schema.yaml"

    asyncio.run(run_auditor({}, csv_path, schema_path))

    schema = load_schema(schema_path)
    values = {
        hint.value
        for hint in schema.dts_hints
        if hint.target == "&switch0/ports/port_xgphy1" and hint.property == "status"
    }

    assert values == {'"okay"'}
