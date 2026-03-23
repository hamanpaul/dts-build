from __future__ import annotations

import asyncio

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
    assert not any(row["signal"] == "GPIO_5GRFIC" for row in rows)


def test_classify_signal_role_marks_aux_wifi_controls_as_pcie_wifi():
    assert _classify_signal_role("PCIE13_WiFi_PWR_DIS") == "PCIE_WIFI"
    assert _classify_signal_role("2G_RF_DISABLE_L") == "PCIE_WIFI"
    assert _classify_signal_role("6G_PEWAKE") == "PCIE_WIFI"


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
