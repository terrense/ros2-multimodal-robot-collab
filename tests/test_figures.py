"""The README's figures have to say what the artifacts say.

A figure is a claim about the data, and a picture cannot be checked by
reading it. So the numbers each figure plots are written next to it in
`docs/figures/figures.json`, and this test recomputes them from the
artifacts and compares.

That means a figure cannot drift: change the calibration manifest without
regenerating the figure, and this fails.

The test needs no matplotlib -- it only compares numbers -- so CI can run it
without installing a plotting stack.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIGURE_DIR = REPO_ROOT / "docs" / "figures"
SIDECAR = FIGURE_DIR / "figures.json"
SCRIPT = REPO_ROOT / "scripts" / "make_figures.py"


def _sidecar() -> dict:
    assert SIDECAR.exists(), (
        f"{SIDECAR} does not exist: run python3 scripts/make_figures.py"
    )
    return json.loads(SIDECAR.read_text(encoding="utf-8"))


def test_the_sidecar_matches_the_artifacts() -> None:
    """The numbers the figures plot are the numbers in the artifacts."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_calibration_figure_covers_every_measured_family() -> None:
    """A family that was measured has to be in the figure."""
    data = _sidecar()
    measured = [
        family
        for family in json.loads(
            (REPO_ROOT / "benchmarks" / "calibration_v1.json").read_text(encoding="utf-8")
        )["families"]
        if family.get("samples")
    ]
    plotted = {row["family"] for row in data["calibration"]}
    assert plotted == {family["name"] for family in measured}
    for row in data["calibration"]:
        assert row["l2_rate"] == row["wilson"][0] or row["l2_rate"] <= row["wilson"][1]
        assert row["l2_rate"] >= row["wilson"][0]


def test_the_wire_figure_covers_every_committed_scenario() -> None:
    """A scenario that was run has to be in the figure."""
    data = _sidecar()
    committed = {
        path.stem.removeprefix("wire_") for path in (REPO_ROOT / "results" / "wire").glob("*.json")
    }
    assert committed, "no wire-fault reports are committed"
    plotted = {row["scenario"] for row in data["wire_faults"]}
    assert plotted == committed


def test_every_wire_report_passed() -> None:
    """The figure is not a picture of a failure."""
    for path in sorted((REPO_ROOT / "results" / "wire").glob("*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        assert report["verdict"] == "pass", f"{path.name}: {report['verdict']}"


def test_the_figures_exist() -> None:
    """The README points at these; a missing file is a broken image."""
    for name in (
        "calibration.svg",
        "calibration-dark.svg",
        "wire_faults.svg",
        "wire_faults-dark.svg",
    ):
        path = FIGURE_DIR / name
        assert path.exists(), f"{path} is missing"
        assert path.stat().st_size > 2000, f"{path} looks empty"
