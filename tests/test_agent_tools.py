"""Comprehensive unit tests for all agent tools.

Covers:
  - TestIndexingTools   (indexing.py)
  - TestTracingTools    (tracing.py)
  - TestSchemaOps       (schema_ops.py)
  - TestCompilerTools   (compiler_tools.py)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from dtsbuild.schema import (
    ClarificationRequest,
    Device,
    HardwareSchema,
    Provenance,
    Signal,
)
from dtsbuild.schema_io import load_schema, save_schema
from dtsbuild.agents.auditor import _audit_devices, _trace_signal, run_auditor

# ── Tool imports ─────────────────────────────────────────────────────

from dtsbuild.agents.tools.indexing import (
    build_connector_index,
    build_refdes_index,
    build_tag_index,
    index_all_pdfs,
    index_pdf_pages,
)
from dtsbuild.agents.tools.tracing import (
    check_bom_population,
    infer_passive_role,
    lookup_refdes,
    trace_cross_pdf,
    trace_net,
    trace_tag_cross_page,
)
from dtsbuild.agents.tools.schema_ops import (
    emit_clarification,
    find_ambiguities,
    get_schema_summary,
    query_schema,
    record_answer,
    write_signal,
)
from dtsbuild.agents.tools.compiler_tools import (
    build_node_template,
    compute_coverage,
    render_dts_node,
    render_dts_reference,
    validate_dts_syntax,
)

# ── Fixtures ─────────────────────────────────────────────────────────

ANALYSIS_DIR = Path(__file__).resolve().parent.parent / "dtsin_BGW720" / ".analysis"


def _make_provenance(**overrides: Any) -> dict:
    """Minimal provenance dict for test helpers."""
    base = {
        "pdfs": ["test.pdf"],
        "pages": [1],
        "refs": ["U1"],
        "method": "test",
        "confidence": 1.0,
    }
    base.update(overrides)
    return base


@pytest.fixture(scope="module")
def all_indices() -> dict[str, Any]:
    """Run indexing once on real analysis data and share across tests."""
    assert ANALYSIS_DIR.is_dir(), f"Missing analysis dir: {ANALYSIS_DIR}"
    return index_all_pdfs(ANALYSIS_DIR)


@pytest.fixture()
def empty_schema(tmp_path: Path) -> Path:
    """Create a blank HardwareSchema YAML file and return its path."""
    schema = HardwareSchema(project="TestProject", chip="BCM68575")
    p = tmp_path / "schema.yaml"
    save_schema(schema, p)
    return p


# =====================================================================
#  TestIndexingTools
# =====================================================================


class TestIndexingTools:
    """Tests for dtsbuild.agents.tools.indexing."""

    def test_index_all_pdfs(self, all_indices: dict) -> None:
        """All four index types must be populated from real analysis data."""
        assert len(all_indices["page_indices"]) > 0, "No page indices"
        assert len(all_indices["tag_index"]) > 0, "No tags indexed"
        assert len(all_indices["refdes_index"]) > 0, "No refdes indexed"
        assert len(all_indices["connector_index"]) > 0, "No connectors indexed"

        # Expect at least mainboard + daughter PDFs
        assert "mainboard" in all_indices["page_indices"]
        assert "daughter" in all_indices["page_indices"]

    def test_tag_index_contains_known_signals(self, all_indices: dict) -> None:
        """Well-known schematic signals must appear in the tag index."""
        tags = all_indices["tag_index"]
        assert "UART0_SOUT" in tags, "UART0_SOUT missing from tag index"
        assert "SER_LED_DATA" in tags, "SER_LED_DATA missing from tag index"

        # At least one GPHY tag must be present
        gphy_tags = [t for t in tags if t.startswith("GPHY")]
        assert len(gphy_tags) > 0, "No GPHY tags found"

    def test_refdes_index_format(self, all_indices: dict) -> None:
        """Every refdes entry must carry pdf_id and page."""
        for rd, entries in all_indices["refdes_index"].items():
            assert isinstance(entries, list), f"{rd}: entries should be list"
            for entry in entries:
                assert "pdf_id" in entry, f"{rd}: missing pdf_id"
                assert "page" in entry, f"{rd}: missing page"

    def test_connector_index_has_pins(self, all_indices: dict) -> None:
        """At least some connectors must have extracted pin dicts."""
        conn_idx = all_indices["connector_index"]
        with_pins = {
            k: v for k, v in conn_idx.items() if v.get("pins")
        }
        assert len(with_pins) > 0, "No connectors have pin mappings"

        # Each connector entry should have pdf_id and a pins dict
        for conn, info in conn_idx.items():
            assert "pdf_id" in info, f"{conn}: missing pdf_id"
            assert isinstance(info.get("pins"), dict), f"{conn}: pins must be dict"

    def test_connector_index_tracks_adjacent_pin_rows_for_board_to_board_links(self) -> None:
        pages = {
            1: "\n".join(
                [
                    "Board 2 Board Connector",
                    "J12",
                    "58                              57",
                    "(5) SCL                         5G_PEWAKE (6)",
                    "60                              59",
                    "(5) SDA                         RESET_B (5)",
                ]
            )
        }

        conn_idx = build_connector_index("daughter", pages)

        assert conn_idx["J12"]["pins"]["SCL"] == "58"
        assert conn_idx["J12"]["pins"]["SDA"] == "60"

    def test_refdes_index_filters_pinmap_noise_and_testpoints(self) -> None:
        """Pin-table noise and TP refs should not become device candidates."""
        pages = {
            1: "\n".join(
                [
                    "VIN J4 3V3_D U41",
                    "TP72",
                ]
            ),
            2: "\n".join(
                [
                    "A21 J4 AB8 VDD_AA7",
                    "B21 C0_FEMCTRL_1/FEMCTRL_1 J19 C0_FEMCTRL_1_5G",
                    "For RF Layout Notes: T17",
                    "- Do RX RF traces length matching within 10degree phase,",
                    "CPU_VDDC T17 VDDM_T15 XPHY2P5G0_AVDD0P75",
                ]
            )
        }

        refdes_idx = build_refdes_index("test", pages)

        assert "TP72" not in refdes_idx
        assert "J19" not in refdes_idx
        assert "T17" not in refdes_idx
        assert "J4" in refdes_idx
        assert refdes_idx["J4"][0]["context"].startswith("VIN")
        assert "U41" in refdes_idx

    def test_refdes_index_filters_u_ball_map_noise(self) -> None:
        """BGA ball coordinates like U9/U21/U32 should not survive as devices."""
        pages = {
            1: "\n".join(
                [
                    "For RF Layout Notes:",
                    "BCM6726_PRELIM_1",
                    "BGA299_0.5mm",
                    "VSS_RF_F20 VSS_U5 U9",
                    "RF5G_IN_C0 RF5G_OUT_C0 U21",
                    "GPIO_51 N2 GPIO_52 U32 T32 N32",
                ]
            ),
            2: "\n".join(
                [
                    "VIN J4 3V3_D U41",
                    "SCL SDA I/O1_7",
                ]
            )
        }

        refdes_idx = build_refdes_index("test", pages)

        assert "U9" not in refdes_idx
        assert "U21" not in refdes_idx
        assert "U32" not in refdes_idx
        assert "U41" in refdes_idx

    def test_refdes_index_extracts_multiline_u41_part_without_stealing_j4_part(self) -> None:
        """U41 should capture the expander part while J4 keeps its connector part."""
        pages = {
            5: "\n".join(
                [
                    "WLAN WiFi Power Enables",
                    "PAF25-D5506-0100R-BU",
                    "VIN J4 3V3_D U41 1K 1K 1K",
                    "VIN C49 C50 5.5V",
                    "24 4 R44 4.7K",
                    "VDD I/O0_0 5",
                    "A0 I/O0_3 8",
                    "SDA I/O0_7 13",
                    "SCL I/O1_1 15",
                    "I/O1_7",
                    "TCA9555PWR",
                    "0x27",
                ]
            )
        }

        refdes_idx = build_refdes_index("test", pages)

        assert refdes_idx["J4"][0]["part_number"] == "PAF25-D5506-0100R-BU"
        assert refdes_idx["U41"][0]["part_number"] == "TCA9555PWR"


# =====================================================================
#  TestTracingTools
# =====================================================================


class TestTracingTools:
    """Tests for dtsbuild.agents.tools.tracing."""

    def test_trace_net_uart(self, all_indices: dict) -> None:
        """Tracing UART0_SOUT in mainboard should find at least one page."""
        pages = all_indices["page_indices"]["mainboard"]
        tag_idx = all_indices["tag_index"]
        result = trace_net("UART0_SOUT", "mainboard", tag_idx, pages)

        assert result["net_name"] == "UART0_SOUT"
        assert result["pdf_id"] == "mainboard"
        assert len(result["pages_found"]) >= 1

    def test_trace_tag_cross_page_surfaces_sfp_related_signals(self, all_indices: dict) -> None:
        """ROGUE_ONU_IN1 should expose its off-page SFP continuation context."""
        pages = all_indices["page_indices"]["mainboard"]
        tag_idx = {
            tag: [entry for entry in entries if entry["pdf_id"] == "mainboard"]
            for tag, entries in all_indices["tag_index"].items()
            if any(entry["pdf_id"] == "mainboard" for entry in entries)
        }

        result = trace_tag_cross_page("ROGUE_ONU_IN1", 7, "mainboard", tag_idx, pages)

        assert 14 in result["destination_pages"]
        assert "WAN_SFP_TX_FAULT" in result["related_signals"]

    def test_trace_net_collects_cross_page_hops(self, all_indices: dict) -> None:
        """Rogue ONU trace should now keep page-hop and related-signal evidence."""
        pages = all_indices["page_indices"]["mainboard"]
        tag_idx = {
            tag: [entry for entry in entries if entry["pdf_id"] == "mainboard"]
            for tag, entries in all_indices["tag_index"].items()
            if any(entry["pdf_id"] == "mainboard" for entry in entries)
        }

        result = trace_net("ROGUE_ONU_IN1", "mainboard", tag_idx, pages)

        assert result["pages_found"] == [7, 14]
        assert any(hop["from"] == 7 and hop["to"] == 14 for hop in result["page_hops"])
        assert "WAN_SFP_TX_FAULT" in result["related_signals"]

    def test_trace_signal_rogue_onu_in1_gains_concrete_path(self, empty_schema: Path, all_indices: dict) -> None:
        """ROGUE_ONU_IN1 should follow the SFP fault chain instead of stopping empty."""
        _trace_signal(
            "ROGUE_ONU_IN1",
            "GPIO_27",
            "GENERAL_GPIO",
            all_indices,
            str(empty_schema),
            aliases=["WAN_SFP_TX_SD"],
        )

        schema = load_schema(empty_schema)
        sig = next(signal for signal in schema.signals if signal.name == "ROGUE_ONU_IN1")

        assert sig.status == "VERIFIED"
        assert "ROGUE_ONU_IN1" in sig.traced_path
        assert "WAN_SFP_TX_FAULT" in sig.traced_path
        assert "R2" in sig.traced_path
        assert sig.provenance.pages == [7, 14]

    def test_trace_signal_reset_out_l_reaches_reset_bus(self, empty_schema: Path, all_indices: dict) -> None:
        """RESET_OUT_L should now show the POR reset bus / LED reset linkage."""
        _trace_signal(
            "RESET_OUT_L",
            "GPIO_67",
            "RESET",
            all_indices,
            str(empty_schema),
        )

        schema = load_schema(empty_schema)
        sig = next(signal for signal in schema.signals if signal.name == "RESET_OUT_L")

        assert sig.status == "VERIFIED"
        assert "POR_RESET_B" in sig.traced_path
        assert "SCLR" in sig.traced_path
        assert "R73" in sig.traced_path
        assert 17 in sig.provenance.pages

    def test_trace_cross_pdf_matches_connector_pin_continuations_across_boards(self) -> None:
        connector_index = {
            "J12": {"pdf_id": "daughter", "pins": {"SCL": "58", "SDA": "60"}},
            "J7": {
                "pdf_id": "mainboard",
                "pins": {"SCL": "58", "SDA": "60", "RESET_B": "59"},
            },
        }

        result = trace_cross_pdf(
            "SCL",
            "daughter",
            "J12",
            connector_index,
            {
                "daughter": {"SCL": [{"page": 5}]},
                "mainboard": {"SCL": [{"page": 7}]},
            },
        )

        assert result["pin_number"] == "58"
        assert result["destination_pdf"] == "mainboard"
        assert result["destination_connector"] == "J7"
        assert result["continued_as"] == "SCL"

    def test_trace_cross_pdf_uses_per_pdf_connector_sides_when_refdes_repeats(self) -> None:
        connector_index = {
            "J1": {
                "pdf_id": "daughter",
                "pins": {"SCL": "58", "BSCL": "58"},
                "pdfs": {
                    "daughter": {"pdf_id": "daughter", "pins": {"SCL": "58"}},
                    "mainboard": {"pdf_id": "mainboard", "pins": {"BSCL": "58"}},
                },
            }
        }

        result = trace_cross_pdf(
            "SCL",
            "daughter",
            "J1",
            connector_index,
            {
                "daughter": {"SCL": [{"page": 5}]},
                "mainboard": {"BSCL": [{"page": 7}]},
            },
        )

        assert result["pin_number"] == "58"
        assert result["destination_pdf"] == "mainboard"
        assert result["destination_connector"] == "J1"
        assert result["continued_as"] == "BSCL"

    def test_infer_passive_0R(self) -> None:
        """0R resistor must be classified as direct_connect, penetrable."""
        r = infer_passive_role("R23", "0R", "UART0_SOUT R23 0R connector")
        assert r["role"] == "direct_connect"
        assert r["penetrable"] is True

    def test_infer_passive_pullup(self) -> None:
        """High-value R to VCC rail must be classified as pull_up."""
        r = infer_passive_role("R100", "10K", "R100 10K connected to VCC +3.3V")
        assert r["role"] == "pull_up"
        assert r["penetrable"] is False

    def test_infer_passive_bypass_cap(self) -> None:
        """Cap to GND must be classified as bypass."""
        r = infer_passive_role("C50", "100nF", "C50 100nF bypass GND decoupling")
        assert r["role"] == "bypass"
        assert r["penetrable"] is False

    def test_lookup_refdes_known(self) -> None:
        """TCA9555PWR must map to 'nxp,pca9555' compatible."""
        refdes_idx: dict[str, Any] = {
            "U8": [
                {
                    "pdf_id": "mainboard",
                    "page": 7,
                    "part_number": "TCA9555PWR",
                    "context": "U8 TCA9555PWR",
                }
            ]
        }
        r = lookup_refdes("U8", refdes_idx, {})
        assert r["part_number"] == "TCA9555PWR"
        assert r["compatible"] == "nxp,pca9555"

    def test_lookup_refdes_multiline_context_returns_normalized_part_and_address(self) -> None:
        """Fallback lookup should mine part/address/bus metadata from multi-line context."""
        pages = {
            5: "\n".join(
                [
                    "VIN J4 3V3_D U41 1K 1K 1K",
                    "A0 I/O0_3 8",
                    "SDA_M1 I/O0_7 13",
                    "SCL_M1 I/O1_1 15",
                    "I/O1_7",
                    "TCA9555PWR",
                    "0x27",
                ]
            )
        }

        r = lookup_refdes("U41", {}, pages)

        assert r["part_number"] == "TCA9555PWR"
        assert r["normalized_part_number"] == "TCA9555"
        assert r["compatible"] == "nxp,pca9555"
        assert r["address"] == "0x27"
        assert r["bus"] == "i2c1"

    def test_check_bom_no_bom(self) -> None:
        """Without a BOM file the component should be assumed populated."""
        r = check_bom_population("R1", bom_path=None)
        assert r["populated"] is True
        assert r["dnp_reason"] is None


class TestAuditorDeviceFiltering:
    """Tests for device candidate filtering in the auditor."""

    def test_audit_devices_skips_noise_but_keeps_runtime_candidates(self, tmp_path: Path) -> None:
        schema_path = tmp_path / "schema.yaml"
        save_schema(HardwareSchema(project="TEST", chip="BCM68575"), schema_path)

        indices = {
            "page_indices": {
                "mainboard": {
                    1: "\n".join(
                        [
                            "J4 TCA9555PWR module expander",
                            "U41",
                            "CPU_VDDC T17 VDDM_T15 XPHY2P5G0_AVDD0P75",
                        ]
                    )
                }
            },
            "refdes_index": {
                "TP72": [
                    {"pdf_id": "mainboard", "page": 1, "part_number": None, "context": "TP72"}
                ],
                "U1A": [
                    {"pdf_id": "mainboard", "page": 1, "part_number": None, "context": "U1A"}
                ],
                "J19": [
                    {
                        "pdf_id": "mainboard",
                        "page": 1,
                        "part_number": None,
                        "context": "B21 C0_FEMCTRL_1/FEMCTRL_1 J19 C0_FEMCTRL_1_5G",
                    }
                ],
                "J4": [
                    {
                        "pdf_id": "mainboard",
                        "page": 1,
                        "part_number": "TCA9555PWR",
                        "context": "J4 TCA9555PWR module expander",
                    }
                ],
                "T17": [
                    {
                        "pdf_id": "mainboard",
                        "page": 1,
                        "part_number": None,
                        "context": "CPU_VDDC T17 VDDM_T15 XPHY2P5G0_AVDD0P75",
                    }
                ],
                "U41": [
                    {"pdf_id": "mainboard", "page": 1, "part_number": None, "context": "U41"}
                ],
            },
        }

        _audit_devices(indices, str(schema_path))
        schema = load_schema(schema_path)
        by_refdes = {device.refdes: device for device in schema.devices}

        assert "TP72" not in by_refdes
        assert "U1A" not in by_refdes
        assert "J19" not in by_refdes
        assert "T17" not in by_refdes

        assert by_refdes["J4"].part_number == "TCA9555PWR"
        assert by_refdes["J4"].compatible == "nxp,pca9555"
        assert "U41" in by_refdes

    def test_audit_devices_skips_power_helpers_but_keeps_runtime_i2c_candidate(self, tmp_path: Path) -> None:
        """Power sequencing logic and regulators should not stay as DTS unresolved devices."""
        schema_path = tmp_path / "schema.yaml"
        save_schema(HardwareSchema(project="TEST", chip="BCM68575"), schema_path)

        indices = {
            "page_indices": {
                "daughter": {
                    5: "\n".join(
                        [
                            "WLAN WiFi Power Enables",
                            "PAF25-D5506-0100R-BU",
                            "VIN J4 3V3_D U41 1K 1K 1K",
                            "A0 I/O0_3 8",
                            "SDA I/O0_7 13",
                            "SCL I/O1_1 15",
                            "I/O1_7",
                            "TCA9555PWR",
                            "0x27",
                        ]
                    ),
                    12: "\n".join(
                        [
                            "VDD3_3_5G",
                            "U99",
                            "P_GOOD_5G",
                            "U74LVC1G11G-AL6-R",
                            'Logic "AND" gate for 5G regulatory',
                        ]
                    ),
                },
                "mainboard": {
                    3: "\n".join(
                        [
                            "DCVIN 7 3V3_PG U39 0.1uF L33",
                            "PGOOD 3V3_PG (5) 25V 4.7uH 2000mA",
                            "VIN BOOT",
                            "LX",
                            "TPS562203DRLR",
                        ]
                    ),
                },
            },
            "refdes_index": {
                "U41": [
                    {
                        "pdf_id": "daughter",
                        "page": 5,
                        "part_number": None,
                        "context": "VIN J4 3V3_D U41 1K 1K 1K",
                    }
                ],
                "U99": [
                    {
                        "pdf_id": "daughter",
                        "page": 12,
                        "part_number": None,
                        "context": "U99 P_GOOD_5G",
                    }
                ],
                "U39": [
                    {
                        "pdf_id": "mainboard",
                        "page": 3,
                        "part_number": None,
                        "context": "DCVIN 7 3V3_PG U39 0.1uF L33",
                    }
                ],
            },
        }

        _audit_devices(indices, str(schema_path))
        schema = load_schema(schema_path)
        by_refdes = {device.refdes: device for device in schema.devices}

        assert "U41" in by_refdes
        assert by_refdes["U41"].part_number == "TCA9555PWR"
        assert by_refdes["U41"].compatible == "nxp,pca9555"
        assert by_refdes["U41"].address == "0x27"
        assert by_refdes["U41"].status == "INCOMPLETE"

        assert "U99" not in by_refdes
        assert "U39" not in by_refdes


class TestAuditorReruns:
    """Rerun behavior for the direct auditor."""

    def test_run_auditor_resets_schema_between_runs(self, tmp_path: Path) -> None:
        schema_path = tmp_path / "schema.yaml"
        gpio_table = tmp_path / "gpio.csv"
        gpio_table.write_text(
            "\n".join(
                [
                    "category,name,signal,pin_or_gpio,polarity,io,notes",
                    "gpio,Test signal,TEST_SIG,GPIO_01,,I,",
                ]
            ),
            encoding="utf-8",
        )

        indices = {
            "page_indices": {
                "mainboard": {
                    1: "TEST_SIG R1 0\nTEST_SIG (2)",
                    2: "(1) TEST_SIG ENDPOINT_SIG",
                }
            },
            "tag_index": {
                "TEST_SIG": [
                    {"pdf_id": "mainboard", "page": 1, "context": "TEST_SIG R1 0"},
                    {"pdf_id": "mainboard", "page": 2, "context": "(1) TEST_SIG ENDPOINT_SIG"},
                ],
                "ENDPOINT_SIG": [
                    {"pdf_id": "mainboard", "page": 2, "context": "(1) TEST_SIG ENDPOINT_SIG"},
                ],
            },
            "refdes_index": {
                "R1": [
                    {
                        "pdf_id": "mainboard",
                        "page": 1,
                        "part_number": None,
                        "context": "TEST_SIG R1 0",
                    }
                ]
            },
            "connector_index": {},
        }

        asyncio.run(run_auditor(indices, gpio_table, schema_path))
        asyncio.run(run_auditor(indices, gpio_table, schema_path))

        schema = load_schema(schema_path)
        assert len(schema.signals) == 1
        assert schema.signals[0].name == "TEST_SIG"


# =====================================================================
#  TestSchemaOps
# =====================================================================


class TestSchemaOps:
    """Tests for dtsbuild.agents.tools.schema_ops."""

    def test_write_and_query_signal(self, empty_schema: Path) -> None:
        """Write a signal, then query it back by status."""
        sp = str(empty_schema)
        write_signal(
            schema_path=sp,
            name="UART0_TX",
            soc_pin="A1",
            traced_path="U1→J1",
            role="DEBUG_UART_TX",
            status="VERIFIED",
            provenance=_make_provenance(),
        )

        results = query_schema(
            schema_path=sp,
            record_type="signal",
            status="VERIFIED",
        )
        assert len(results) == 1
        assert results[0]["name"] == "UART0_TX"
        assert results[0]["status"] == "VERIFIED"

    def test_find_ambiguities(self, empty_schema: Path) -> None:
        """Write mixed-status signals, find_ambiguities returns INCOMPLETE."""
        sp = str(empty_schema)
        write_signal(
            schema_path=sp,
            name="SIG_A",
            soc_pin="B1",
            traced_path="path_a",
            role="role_a",
            status="VERIFIED",
            provenance=_make_provenance(),
        )
        write_signal(
            schema_path=sp,
            name="SIG_B",
            soc_pin="B2",
            traced_path="path_b",
            role="role_b",
            status="INCOMPLETE",
            provenance=_make_provenance(),
        )

        amb = find_ambiguities(schema_path=sp)
        assert amb["total_unresolved"] >= 1
        assert len(amb["incomplete_signals"]) == 1
        assert amb["incomplete_signals"][0]["name"] == "SIG_B"

    def test_emit_and_record_answer(self, empty_schema: Path) -> None:
        """Full clarification lifecycle: emit → pending → record_answer → answered."""
        sp = str(empty_schema)

        # Emit a clarification
        cr = emit_clarification(
            schema_path=sp,
            id="cr-test-001",
            blocking=True,
            domain="gpio_assignment",
            question="Which GPIO is the reset button?",
            choices=["GPIO_12", "GPIO_48"],
            evidence_context="Schematic page 7",
            missing_evidence="No label on button",
        )
        assert cr["status"] == "pending"
        assert cr["id"] == "cr-test-001"

        # Verify it shows up as pending in ambiguities
        amb = find_ambiguities(schema_path=sp)
        assert amb["total_unresolved"] >= 1
        assert len(amb["pending_clarifications"]) == 1

        # Record the answer
        ans = record_answer(
            schema_path=sp,
            cr_id="cr-test-001",
            answer="GPIO_48",
        )
        assert ans["status"] == "ok"
        assert ans["answer"] == "GPIO_48"
        assert ans["method"] == "user_choice"

        # Verify it's now answered
        results = query_schema(
            schema_path=sp,
            record_type="clarification",
            status="answered",
        )
        assert len(results) == 1
        assert results[0]["answer"] == "GPIO_48"

    def test_get_schema_summary(self, empty_schema: Path) -> None:
        """Summary counts must match what was written."""
        sp = str(empty_schema)
        prov = _make_provenance()

        write_signal(schema_path=sp, name="S1", soc_pin="X1", traced_path="p1",
                     role="r1", status="VERIFIED", provenance=prov)
        write_signal(schema_path=sp, name="S2", soc_pin="X2", traced_path="p2",
                     role="r2", status="INCOMPLETE", provenance=prov)
        write_signal(schema_path=sp, name="S3", soc_pin="X3", traced_path="p3",
                     role="r3", status="AMBIGUOUS", provenance=prov)

        summary = get_schema_summary(schema_path=sp)
        assert summary["signals"]["total"] == 3
        assert summary["signals"]["verified"] == 1
        assert summary["signals"]["incomplete"] == 1
        assert summary["signals"]["ambiguous"] == 1
        assert summary["project"] == "TestProject"


# =====================================================================
#  TestCompilerTools
# =====================================================================


class TestCompilerTools:
    """Tests for dtsbuild.agents.tools.compiler_tools."""

    def test_render_dts_node(self) -> None:
        """Rendered node must have correct indentation and syntax."""
        result = render_dts_node(
            "buttons",
            [("compatible", '"brcm,buttons"'), ("status", '"okay"')],
        )
        assert "buttons {" in result
        assert '\tcompatible = "brcm,buttons";' in result
        assert '\tstatus = "okay";' in result
        assert result.rstrip().endswith("};")

    def test_render_dts_reference(self) -> None:
        """Reference node must start with '&name {'."""
        result = render_dts_reference(
            "uart0",
            [("status", '"okay"')],
        )
        assert result.startswith("&uart0 {")
        assert '\tstatus = "okay";' in result
        assert result.rstrip().endswith("};")

    def test_validate_dts_valid(self) -> None:
        """Well-formed DTS must pass validation."""
        dts = '/dts-v1/;\n\n/ {\n\tmodel = "test";\n};\n'
        result = validate_dts_syntax(dts)
        assert result["valid"] is True
        assert result["errors"] == []

    def test_validate_dts_missing_header(self) -> None:
        """Missing /dts-v1/; should produce a warning."""
        dts = '/ {\n\tmodel = "test";\n};\n'
        result = validate_dts_syntax(dts)
        assert any("/dts-v1/" in w for w in result["warnings"])

    def test_validate_dts_unbalanced_braces(self) -> None:
        """Unbalanced braces must produce an error."""
        dts = '/dts-v1/;\n\n/ {\n\tmodel = "test";\n\n'
        result = validate_dts_syntax(dts)
        assert result["valid"] is False
        assert any("Unbalanced" in e for e in result["errors"])

    def test_build_node_template(self) -> None:
        """Known subsystem templates must return expected structure."""
        for subsystem in ("uart", "i2c", "led_ctrl", "buttons", "pcie", "usb"):
            tmpl = build_node_template(subsystem)
            assert "node_name" in tmpl
            assert "is_reference" in tmpl
            assert "required_properties" in tmpl

        # uart specifics
        uart = build_node_template("uart")
        assert uart["node_name"] == "&uart0"
        assert uart["is_reference"] is True

        # Unknown subsystem must raise
        with pytest.raises(KeyError, match="Unknown subsystem"):
            build_node_template("nonexistent_subsystem")

    def test_compute_coverage(self) -> None:
        """Coverage computation with a known schema and DTS snippet."""
        prov = Provenance(
            pdfs=["m.pdf"], pages=[1], refs=["U1"],
            method="test", confidence=1.0,
        )
        schema = HardwareSchema(
            project="test",
            chip="BCM68575",
            signals=[
                Signal(
                    name="UART0_TX", soc_pin="A1", traced_path="U1→J1",
                    role="DEBUG_UART_TX", status="VERIFIED", provenance=prov,
                ),
                Signal(
                    name="SPI_CLK", soc_pin="A2", traced_path="U1→J2",
                    role="SPI_CLOCK", status="VERIFIED", provenance=prov,
                ),
                Signal(
                    name="MISSING_SIG", soc_pin="A3", traced_path="U1→J3",
                    role="MISSING_ROLE", status="INCOMPLETE", provenance=prov,
                ),
            ],
            devices=[
                Device(
                    refdes="U8", part_number="TCA9555PWR",
                    compatible="nxp,pca9555", bus="i2c0", address="0x27",
                    status="VERIFIED", provenance=prov,
                ),
            ],
        )

        dts = (
            '/dts-v1/;\n'
            '&uart0 { status = "okay"; };\n'
            '/* no spi_clk or tca9555 mention */\n'
        )
        cov = compute_coverage(schema, dts)

        # UART0_TX verified + covered (uart in dts), SPI_CLK verified + not covered,
        # U8 verified + not covered, MISSING_SIG incomplete
        assert cov["total_verified"] == 3
        assert cov["covered"] >= 1  # at least uart
        assert cov["incomplete_not_in_dts"] == 1
        assert 0.0 <= cov["coverage_pct"] <= 100.0
        assert isinstance(cov["uncovered"], list)

    def test_compute_coverage_treats_led_control_and_spi_semantically(self) -> None:
        prov = Provenance(
            pdfs=["board.pdf"],
            pages=[1],
            refs=["U1"],
            method="net_trace",
            confidence=0.9,
        )
        schema = HardwareSchema(
            project="TEST",
            chip="BCM68575",
            signals=[
                Signal(
                    name="SER_LED_DATA", soc_pin="GPIO_55", traced_path="U1→U12",
                    role="LED_CONTROL", status="VERIFIED", provenance=prov,
                ),
                Signal(
                    name="SPIS_CLK", soc_pin="GPIO_8", traced_path="U1→J2",
                    role="SPI", status="VERIFIED", provenance=prov,
                ),
            ],
            devices=[],
        )

        dts = (
            '/dts-v1/;\n'
            '&hsspi { status = "okay"; };\n'
            '&led_ctrl { status = "okay"; };\n'
        )
        cov = compute_coverage(schema, dts)

        assert cov["total_verified"] == 2
        assert cov["covered"] == 2
        assert cov["coverage_pct"] == 100.0
