"""Tests for dtsbuild.agents.calibration_workflow."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from dtsbuild.agents.calibration_workflow import run_calibration_workflow


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def test_run_calibration_workflow_writes_refdiff_and_calibration_sidecars(tmp_path):
    project_dir = tmp_path / "dtsin_TEST"
    output_dir = tmp_path / "dtsout_TEST"
    project_dir.mkdir()
    output_dir.mkdir()

    manifest = {
        "project": "TEST",
        "family": "bcm68575",
        "output_dir": "dtsout_TEST",
    }
    (project_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )

    _write(
        output_dir / "TEST.dts",
        """\
/dts-v1/;
/ { };

&ext_pwr_ctrl {
    status = "okay";
};
""",
    )
    _write(
        output_dir / "TEST-REF.dts",
        """\
/dts-v1/;
/ { };

&ext_pwr_ctrl {
    foo-gpio = <1>;
    status = "okay";
};

&cpufreq {
    op-mode = "dvfs";
};
""",
    )
    _write(
        output_dir / "TEST.schema.yaml",
        yaml.safe_dump(
            {
                "version": "1.0",
                "project": "TEST",
                "chip": "BCM68575",
                "signals": [],
                "devices": [],
                "paths": [],
                "clarification_requests": [],
                "dts_hints": [],
                "user_answers": {},
            },
            sort_keys=False,
        ),
    )
    _write_json(output_dir / "TEST.validation.json", {"issues": []})
    _write_json(output_dir / "TEST.unresolved.json", {"items": []})

    paths = run_calibration_workflow(project_dir)

    assert paths["refdiff"].exists()
    assert paths["calibration"].exists()

    refdiff = json.loads(paths["refdiff"].read_text(encoding="utf-8"))
    calibration = json.loads(paths["calibration"].read_text(encoding="utf-8"))

    assert refdiff["project"] == "TEST"
    assert refdiff["summary"]["total_candidates"] >= 1
    assert calibration["project"] == "TEST"
    assert calibration["summary"]["total_decisions"] == 0
    assert str(paths["reference_dts"]).endswith("TEST-REF.dts")


def test_run_calibration_workflow_requires_unique_reference_when_ambiguous(tmp_path):
    project_dir = tmp_path / "dtsin_TEST"
    output_dir = tmp_path / "dtsout_TEST"
    project_dir.mkdir()
    output_dir.mkdir()

    (project_dir / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "project": "TEST",
                "family": "bcm68575",
                "output_dir": "dtsout_TEST",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    _write(output_dir / "TEST.dts", "/dts-v1/;\n/ { };\n")
    _write(output_dir / "REF1.dts", "/dts-v1/;\n/ { };\n")
    _write(output_dir / "REF2.dts", "/dts-v1/;\n/ { };\n")
    _write(
        output_dir / "TEST.schema.yaml",
        yaml.safe_dump(
            {
                "version": "1.0",
                "project": "TEST",
                "chip": "BCM68575",
                "signals": [],
                "devices": [],
                "paths": [],
                "clarification_requests": [],
                "dts_hints": [],
                "user_answers": {},
            },
            sort_keys=False,
        ),
    )
    _write_json(output_dir / "TEST.validation.json", {"issues": []})
    _write_json(output_dir / "TEST.unresolved.json", {"items": []})

    try:
        run_calibration_workflow(project_dir)
    except FileNotFoundError as exc:
        assert "multiple reference DTS candidates" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError for ambiguous reference DTS discovery")
