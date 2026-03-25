"""Tests for dtsbuild.agents.refdiff."""

from __future__ import annotations

from pathlib import Path

from dtsbuild.agents.refdiff import (
    build_refdiff_report,
    parse_dts_document,
)


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_parse_dts_document_tracks_labels_booleans_and_delete_property(tmp_path):
    path = _write(
        tmp_path / "sample.dts",
        """\
/dts-v1/;

/ {
    demo: sample-node {
        compatible = "vendor,demo";
        flag-prop;
    };
};

&hsspi {
    /delete-property/ pinctrl-0;
    status = "okay";
};
""",
    )

    doc = parse_dts_document(path)
    nodes = {node.path: node for node in doc.nodes}

    assert "/sample-node" in nodes
    assert nodes["/sample-node"].label == "demo"
    assert nodes["/sample-node"].properties["compatible"].value == '"vendor,demo"'
    assert nodes["/sample-node"].properties["flag-prop"].value is None
    assert nodes["/&hsspi"].properties["/delete-property/ pinctrl-0"].value is None


def test_build_refdiff_report_detects_missing_nodes_properties_and_values(tmp_path):
    generated = _write(
        tmp_path / "generated.dts",
        """\
/dts-v1/;
/ {
    buttons {
        compatible = "brcm,buttons";
    };
};

&ext_pwr_ctrl {
    status = "okay";
};
""",
    )
    reference = _write(
        tmp_path / "reference.dts",
        """\
/dts-v1/;
/ {
    buttons {
        compatible = "brcm,buttons";
        linux,code = <0x198>;
    };
};

&ext_pwr_ctrl {
    status = "disabled";
};

&hsspi {
    status = "okay";
};
""",
    )

    report = build_refdiff_report(
        project="TEST",
        generated_dts_path=generated,
        reference_dts_path=reference,
    )

    by_type = {}
    for candidate in report.candidates:
        by_type.setdefault(candidate.candidate_type, []).append(candidate)

    assert any(c.target == "/buttons:linux,code" for c in by_type["missing_property"])
    assert any(c.target == "/&ext_pwr_ctrl:status" for c in by_type["value_mismatch"])
    assert any(c.target == "/&hsspi" for c in by_type["missing_node"])
    assert any(c.compiler_surface == "_render_hsspi" for c in by_type["missing_node"])


def test_build_refdiff_report_maps_cpufreq_to_compiler_surface(tmp_path):
    generated = _write(tmp_path / "generated.dts", "/dts-v1/;\n/ { };\n")
    reference = _write(
        tmp_path / "reference.dts",
        """\
/dts-v1/;
/ { };

&cpufreq {
    op-mode = "dvfs";
};
""",
    )

    report = build_refdiff_report(
        project="TEST",
        generated_dts_path=generated,
        reference_dts_path=reference,
    )

    missing = [c for c in report.candidates if c.candidate_type == "missing_node"]
    assert len(missing) == 1
    assert missing[0].target == "/&cpufreq"
    assert missing[0].route_hint == "renderer"
    assert missing[0].compiler_surface == "_render_cpufreq"


def test_build_refdiff_report_maps_xport_to_compiler_surface(tmp_path):
    generated = _write(tmp_path / "generated.dts", "/dts-v1/;\n/ { };\n")
    reference = _write(
        tmp_path / "reference.dts",
        """\
/dts-v1/;
/ { };

&xport {
    status = "okay";
};
""",
    )

    report = build_refdiff_report(
        project="TEST",
        generated_dts_path=generated,
        reference_dts_path=reference,
    )

    missing = [c for c in report.candidates if c.candidate_type == "missing_node"]
    assert len(missing) == 1
    assert missing[0].target == "/&xport"
    assert missing[0].route_hint == "renderer"
    assert missing[0].compiler_surface == "_render_xport"


def test_build_refdiff_report_maps_mdio_to_compiler_surface(tmp_path):
    generated = _write(tmp_path / "generated.dts", "/dts-v1/;\n/ { };\n")
    reference = _write(
        tmp_path / "reference.dts",
        """\
/dts-v1/;
/ { };

&mdio {
    status = "okay";
};
""",
    )

    report = build_refdiff_report(
        project="TEST",
        generated_dts_path=generated,
        reference_dts_path=reference,
    )

    missing = [c for c in report.candidates if c.candidate_type == "missing_node"]
    assert len(missing) == 1
    assert missing[0].target == "/&mdio"
    assert missing[0].route_hint == "renderer"
    assert missing[0].compiler_surface == "_render_mdio"


def test_build_refdiff_report_maps_mdio_bus_to_compiler_surface(tmp_path):
    generated = _write(tmp_path / "generated.dts", "/dts-v1/;\n/ { };\n")
    reference = _write(
        tmp_path / "reference.dts",
        """\
/dts-v1/;
/ { };

&mdio_bus {
    xphy1 {
        enet-phy-lane-swap;
        status = "okay";
    };
};
""",
    )

    report = build_refdiff_report(
        project="TEST",
        generated_dts_path=generated,
        reference_dts_path=reference,
    )

    missing = [c for c in report.candidates if c.candidate_type == "missing_node"]
    assert any(c.target == "/&mdio_bus" for c in missing)
    assert any(c.compiler_surface == "_render_mdio_bus" for c in missing)


def test_build_refdiff_report_maps_switch0_to_compiler_surface(tmp_path):
    generated = _write(tmp_path / "generated.dts", "/dts-v1/;\n/ { };\n")
    reference = _write(
        tmp_path / "reference.dts",
        """\
/dts-v1/;
/ { };

&switch0 {
    ports {
        port_xgphy0 {
            status = "okay";
        };
    };
};
""",
    )

    report = build_refdiff_report(
        project="TEST",
        generated_dts_path=generated,
        reference_dts_path=reference,
    )

    missing = [c for c in report.candidates if c.candidate_type == "missing_node"]
    assert any(c.target == "/&switch0" for c in missing)
    assert any(c.compiler_surface == "_render_switch0" for c in missing)


def test_build_refdiff_report_maps_ext_pwr_ctrl_to_power_subsystem(tmp_path):
    generated = _write(tmp_path / "generated.dts", "/dts-v1/;\n/ { };\n")
    reference = _write(
        tmp_path / "reference.dts",
        """\
/dts-v1/;
/ { };

&ext_pwr_ctrl {
    pwr-ctrl-0-gpios = <&gpioc 90 0>;
};
""",
    )

    report = build_refdiff_report(
        project="TEST",
        generated_dts_path=generated,
        reference_dts_path=reference,
    )

    missing = [c for c in report.candidates if c.target == "/&ext_pwr_ctrl"]
    assert len(missing) == 1
    assert missing[0].subsystem == "power"


def test_build_refdiff_report_detects_binding_mismatch_for_duplicate_overlays(tmp_path):
    generated = _write(
        tmp_path / "generated.dts",
        """\
/dts-v1/;
/ { };

&ext_pwr_ctrl {
    foo-gpio = <1>;
};

&ext_pwr_ctrl {
    bar-gpio = <2>;
};
""",
    )
    reference = _write(
        tmp_path / "reference.dts",
        """\
/dts-v1/;
/ { };

&ext_pwr_ctrl {
    foo-gpio = <1>;
    bar-gpio = <2>;
};
""",
    )

    report = build_refdiff_report(
        project="TEST",
        generated_dts_path=generated,
        reference_dts_path=reference,
    )

    binding = [c for c in report.candidates if c.candidate_type == "binding_mismatch"]
    assert len(binding) == 1
    assert binding[0].target == "/&ext_pwr_ctrl"
    assert binding[0].compiler_surface == "_render_power_ctrl"


def test_parse_dts_document_skips_inactive_preprocessor_else_branch(tmp_path):
    path = _write(
        tmp_path / "conditional.dts",
        """\
/dts-v1/;

/ {
#if defined(CONFIG_BCM_BUTTON)
    buttons {
        compatible = "brcm,buttons";
    };
#else
    gpio-keys {
        compatible = "gpio-keys";
    };
#endif
};
""",
    )

    doc = parse_dts_document(path)
    node_paths = {node.path for node in doc.nodes}

    assert "/buttons" in node_paths
    assert "/gpio-keys" not in node_paths


def test_build_refdiff_report_ignores_inactive_gpio_keys_branch(tmp_path):
    generated = _write(
        tmp_path / "generated.dts",
        """\
/dts-v1/;
/ {
    buttons {
        compatible = "brcm,buttons";
    };
};
""",
    )
    reference = _write(
        tmp_path / "reference.dts",
        """\
/dts-v1/;
/ {
#if defined(CONFIG_BCM_BUTTON)
    buttons {
        compatible = "brcm,buttons";
    };
#else
    gpio-keys {
        compatible = "gpio-keys";
    };
#endif
};
""",
    )

    report = build_refdiff_report(
        project="TEST",
        generated_dts_path=generated,
        reference_dts_path=reference,
    )

    assert not any(c.target.startswith("/gpio-keys") for c in report.candidates)


def test_build_refdiff_report_maps_phy_wan_serdes_to_serdes_subsystem(tmp_path):
    generated = _write(tmp_path / "generated.dts", "/dts-v1/;\n/ { };\n")
    reference = _write(
        tmp_path / "reference.dts",
        """\
/dts-v1/;
/ { };

&phy_wan_serdes {
    status = "okay";
};
""",
    )

    report = build_refdiff_report(
        project="TEST",
        generated_dts_path=generated,
        reference_dts_path=reference,
    )

    missing = [c for c in report.candidates if c.target == "/&phy_wan_serdes"]
    assert len(missing) == 1
    assert missing[0].subsystem == "serdes"


def test_build_refdiff_report_maps_wan_sfp_to_dedicated_surface(tmp_path):
    generated = _write(tmp_path / "generated.dts", "/dts-v1/;\n/ { };\n")
    reference = _write(
        tmp_path / "reference.dts",
        """\
/dts-v1/;
/ {
    wan_sfp: wan_sfp {
        compatible = "brcm,sfp";
    };
};
""",
    )

    report = build_refdiff_report(
        project="TEST",
        generated_dts_path=generated,
        reference_dts_path=reference,
    )

    missing = [c for c in report.candidates if c.target == "/wan_sfp"]
    assert len(missing) == 1
    assert missing[0].compiler_surface == "_render_wan_sfp"
