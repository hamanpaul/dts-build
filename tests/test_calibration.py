"""Tests for dtsbuild.agents.calibration sidecars."""

from __future__ import annotations

import json

from dtsbuild.agents.calibration import (
    AppliedChange,
    CalibrationDecision,
    CalibrationLog,
    EvidencePointer,
    RefDiffCandidate,
    RefDiffReport,
    load_calibration_log,
    load_refdiff_report,
    make_candidate_id,
    write_calibration_log,
    write_refdiff_report,
)


def _candidate() -> RefDiffCandidate:
    return RefDiffCandidate(
        id=make_candidate_id("missing_node", "&hsspi", "BGW720-300_v01.dts:142-145"),
        candidate_type="missing_node",
        target="&hsspi",
        project="BGW720",
        summary="Reference DTS enables hsspi while generated DTS does not.",
        route_hint="renderer",
        subsystem="spi",
        dts_relevant=True,
        generated_locator="BGW720.dts",
        reference_locator="BGW720-300_v01.dts:142-145",
        reason="SPIS_* signals exist but renderer gating may be mismatched.",
        compiler_surface="_render_hsspi",
        validation_link="BGW720.validation.json#issues[0]",
        evidence=[
            EvidencePointer(
                source="reference_dts",
                path="BGW720-300_v01.dts",
                locator="142-145",
                summary="Reference node exists.",
            ),
            EvidencePointer(
                source="validation",
                path="BGW720.validation.json",
                locator="issues[0]",
                summary="Missing-node signal warning points at SPI coverage gap.",
            ),
        ],
    )


def test_make_candidate_id_slugifies_target_and_locator():
    candidate_id = make_candidate_id(
        "binding_mismatch",
        "&ext_pwr_ctrl",
        "BGW720-300_v01.dts:363-366",
    )

    assert candidate_id == "binding-mismatch-ext-pwr-ctrl-bgw720-300-v01-dts-363-366"


def test_refdiff_report_summary_counts_candidate_shapes():
    report = RefDiffReport(
        project="BGW720",
        generated_dts_path="BGW720.dts",
        reference_dts_path="BGW720-300_v01.dts",
        candidates=[
            _candidate(),
            RefDiffCandidate(
                id=make_candidate_id("unsupported_surface", "&cpufreq"),
                candidate_type="unsupported_surface",
                target="&cpufreq",
                project="BGW720",
                summary="Reference DTS has cpufreq but no supported renderer surface yet.",
                route_hint="capability",
                subsystem="power",
                dts_relevant=False,
            ),
        ],
    )

    assert report.summary["total_candidates"] == 2
    assert report.summary["by_type"]["missing_node"] == 1
    assert report.summary["by_type"]["unsupported_surface"] == 1
    assert report.summary["by_route_hint"]["renderer"] == 1
    assert report.summary["by_route_hint"]["capability"] == 1
    assert report.summary["dts_relevant"] == {"true": 1, "false": 1}
    assert report.summary["by_subsystem"] == {"spi": 1, "power": 1}


def test_refdiff_report_json_round_trip(tmp_path):
    report = RefDiffReport(
        project="BGW720",
        generated_dts_path="BGW720.dts",
        reference_dts_path="BGW720-300_v01.dts",
        schema_path="BGW720.schema.yaml",
        validation_path="BGW720.validation.json",
        unresolved_path="BGW720.unresolved.json",
        candidates=[_candidate()],
    )
    output_path = tmp_path / "BGW720.refdiff.json"
    write_refdiff_report(report, output_path)

    loaded = load_refdiff_report(output_path)
    raw = json.loads(output_path.read_text(encoding="utf-8"))

    assert raw == report.to_dict()
    assert loaded.to_dict() == report.to_dict()
    assert loaded.candidates[0].evidence[0].source == "reference_dts"


def test_calibration_log_summary_counts_decisions_and_changes():
    log = CalibrationLog(
        project="BGW720",
        refdiff_path="BGW720.refdiff.json",
        schema_path="BGW720.schema.yaml",
        decisions=[
            CalibrationDecision(
                candidate_id="missing-node-hsspi",
                decision="DEFER_UNPROVEN",
                route="renderer",
                rationale="Need more evidence before enabling HSSPI binding.",
            ),
            CalibrationDecision(
                candidate_id="binding-mismatch-ext-pwr-ctrl",
                decision="ACCEPT",
                route="renderer",
                rationale="Existing power evidence supports aggregation fix.",
                applied_changes=[
                    AppliedChange(
                        kind="renderer",
                        target="_render_power_ctrl",
                        summary="Aggregate multiple POWER signals into one node.",
                    ),
                ],
            ),
        ],
    )

    assert log.summary["total_decisions"] == 2
    assert log.summary["by_decision"]["ACCEPT"] == 1
    assert log.summary["by_decision"]["DEFER_UNPROVEN"] == 1
    assert log.summary["by_route"]["renderer"] == 2
    assert log.summary["by_change_kind"] == {"renderer": 1}


def test_calibration_log_json_round_trip(tmp_path):
    log = CalibrationLog(
        project="BGW720",
        refdiff_path="BGW720.refdiff.json",
        schema_path="BGW720.schema.yaml",
        decisions=[
            CalibrationDecision(
                candidate_id="u41-gpio27",
                decision="ASK_ME",
                route="ask-me",
                rationale="Bus metadata remains incomplete after schematic trace.",
                evidence=[
                    EvidencePointer(
                        source="schema",
                        path="BGW720.schema.yaml",
                        locator="devices[U41]",
                        summary="Address known, bus missing.",
                    ),
                ],
                applied_changes=[
                    AppliedChange(
                        kind="ask-me",
                        target="cr-dev-u41",
                        summary="Preserve clarification for I2C expander bus metadata.",
                    ),
                ],
                follow_up="Route to resolver only if evidence stays incomplete.",
            ),
        ],
    )
    output_path = tmp_path / "BGW720.calibration.json"
    write_calibration_log(log, output_path)

    loaded = load_calibration_log(output_path)
    raw = json.loads(output_path.read_text(encoding="utf-8"))

    assert raw == log.to_dict()
    assert loaded.to_dict() == log.to_dict()
    assert loaded.decisions[0].applied_changes[0].kind == "ask-me"
