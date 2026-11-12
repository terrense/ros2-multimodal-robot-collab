#!/usr/bin/env python3
"""Render the README's figures from the committed artifacts.

Every figure is generated from a file in `results/` or `benchmarks/` -- never
from a number typed into the README -- and the numbers it plots are written
next to it in `docs/figures/figures.json`. A test compares that sidecar with
the artifacts, so a figure cannot drift away from the data it claims to show.

    python3 scripts/make_figures.py            # write the figures
    python3 scripts/make_figures.py --check    # fail if the sidecar is stale

matplotlib is needed to draw, but not to check: `--check` only compares
numbers, so CI can run it without matplotlib installed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
FIGURE_DIR = REPO_ROOT / "docs" / "figures"
SIDECAR = FIGURE_DIR / "figures.json"

CALIBRATION = REPO_ROOT / "benchmarks" / "calibration_v1.json"
WIRE_DIR = REPO_ROOT / "results" / "wire"
EVAL_DIR = REPO_ROOT / "results" / "eval"

# ---------------------------------------------------------------------------
# The palette. One hue, two shades, because every figure here is a
# before/after comparison -- and the numbers are ink, never the series colour.
# ---------------------------------------------------------------------------
LIGHT = {
    "surface": "#fcfcfb",
    "ink": "#0b0b0b",
    "ink_2": "#52514e",
    "muted": "#898781",
    "grid": "#e1e0d9",
    "axis": "#c3c2b7",
    "context": "#86b6ef",   # blue 250 -- the "before"
    "focus": "#2a78d6",     # blue 450 -- the "after"
    "focus_soft": "#cde2fb",
    "note": "#d03b3b",      # status critical: the "never came back" case
}
DARK = {
    "surface": "#1a1a19",
    "ink": "#f0efec",
    "ink_2": "#c3c2b7",
    "muted": "#898781",
    "grid": "#2c2c2a",
    "axis": "#383835",
    "context": "#184f95",    # blue 600 -- dimmer against a dark surface
    "focus": "#3987e5",      # blue 400 -- brighter against a dark surface
    "focus_soft": "#184f95",
    "note": "#d03b3b",
}

#: The families the calibration measured, in the order the story reads.
FAMILY_ORDER = (
    "nav_timeout",
    "identity_mismatch",
    "low_confidence",
    "user_cancel",
    "tool_missing",
)
FAMILY_LABELS = {
    "nav_timeout": "nav_timeout",
    "identity_mismatch": "identity_mismatch",
    "low_confidence": "low_confidence",
    "user_cancel": "user_cancel",
    "tool_missing": "tool_missing",
}
SCENARIO_ORDER = (
    "duplicated_packets",
    "slow_ack",
    "dropped_result",
    "bursty_loss",
    "severe",
    "blackout",
)
SCENARIO_LABELS = {
    "duplicated_packets": "duplicated_packets",
    "slow_ack": "slow_ack",
    "dropped_result": "dropped_result",
    "bursty_loss": "bursty_loss",
    "severe": "severe",
    "blackout": "blackout",
}


# --------------------------------------------------------------------- data


def calibration_rows() -> list[dict[str, Any]]:
    """L1's assumption and what the stack measured, per family."""
    data = json.loads(CALIBRATION.read_text(encoding="utf-8"))
    families = {family["name"]: family for family in data["families"]}
    rows = []
    for name in FAMILY_ORDER:
        family = families.get(name)
        if family is None or not family.get("samples"):
            continue
        rows.append(
            {
                "family": name,
                "label": FAMILY_LABELS[name],
                "l1_rate": float(family["l1_rate"]),
                "l2_rate": float(family["l2_rate"]),
                "wilson": [float(value) for value in family["wilson_95"]],
                "samples": int(family["samples"]),
                "verdict": str(family["reason"]),
            }
        )
    return rows


def wire_rows() -> list[dict[str, Any]]:
    """What each transport fault cost the command."""
    rows = []
    for name in SCENARIO_ORDER:
        path = WIRE_DIR / f"wire_{name}.json"
        if not path.exists():
            continue
        report = json.loads(path.read_text(encoding="utf-8"))
        measurements = report.get("measurements") or {}
        rows.append(
            {
                "scenario": name,
                "label": SCENARIO_LABELS[name],
                "baseline_seconds": measurements.get("baseline_seconds"),
                "faulted_seconds": measurements.get("faulted_seconds"),
                "faulted_status": measurements.get("faulted_status"),
                "link_dropped_packets": measurements.get("link_dropped_packets"),
                "link_sent_packets": measurements.get("link_sent_packets"),
                "verdict": str(report.get("verdict")),
            }
        )
    return rows


def receipt_rows() -> list[dict[str, Any]]:
    """The receipt metrics per evaluation, L1 and L2 side by side."""
    rows = []
    for path in sorted(EVAL_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        receipts = data.get("receipts") or {}
        if not receipts:
            continue
        rows.append(
            {
                "evaluation": str(data.get("label") or path.stem),
                "source": str(receipts.get("source", "unknown")),
                "calls": int(receipts.get("calls", 0)),
                "uncertain_outcome_rate": float(receipts.get("uncertain_outcome_rate", 0.0)),
                "latency_p50_ms": receipts.get("latency_p50_ms"),
                "duplicate_effect_rate": receipts.get("duplicate_effect_rate"),
            }
        )
    return rows


def sidecar() -> dict[str, Any]:
    """The numbers the figures plot, in one place."""
    return {
        "calibration": calibration_rows(),
        "wire_faults": wire_rows(),
        "receipts": receipt_rows(),
    }


# ------------------------------------------------------------------- figures


def _style(ax, theme: dict[str, str]) -> None:
    ax.set_facecolor(theme["surface"])
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(theme["axis"])
        ax.spines[side].set_linewidth(1.0)
    ax.tick_params(colors=theme["muted"], labelsize=9, length=3, width=1.0)
    ax.yaxis.grid(True, color=theme["grid"], linewidth=0.8)
    ax.xaxis.grid(False)
    ax.set_axisbelow(True)


def _fonts() -> list[str]:
    """A Latin font, and a CJK one for the labels that need it.

    matplotlib picks one family for a text run, so the CJK font has to carry
    the Latin glyphs too -- hence Microsoft YaHei, which has both.
    """
    import matplotlib.font_manager as font_manager

    families = ["DejaVu Sans"]
    for candidate in (
        Path("/mnt/c/Windows/Fonts/msyh.ttc"),
        Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"),
    ):
        if candidate.exists():
            try:
                font_manager.fontManager.addfont(str(candidate))
                families.append(font_manager.FontProperties(fname=str(candidate)).get_name())
            except Exception:  # noqa: BLE001 - a font we cannot read is not a font
                continue
    return families


def draw_calibration(rows: list[dict[str, Any]], theme: dict[str, str], path: Path) -> None:
    """L1's assumption against what the stack did, per family.

    A before/after comparison per item, so: one hue, two shades, and the gap
    is the point. The Wilson interval is the measured value's own uncertainty,
    drawn on it.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = list(reversed(rows))
    labels = [row["label"] for row in rows]
    y = list(range(len(rows)))

    figure, ax = plt.subplots(figsize=(9.6, 5.0), dpi=170)
    figure.patch.set_facecolor(theme["surface"])
    _style(ax, theme)

    for index, row in enumerate(rows):
        low, high = row["wilson"]
        ax.plot([low, high], [index, index], color=theme["focus"], linewidth=2.0,
                solid_capstyle="round", zorder=2)
        ax.plot([row["l1_rate"]], [index], marker="o", markersize=9,
                color=theme["context"], markeredgecolor=theme["surface"],
                markeredgewidth=2.0, zorder=3, linestyle="none")
        ax.plot([row["l2_rate"]], [index], marker="o", markersize=9,
                color=theme["focus"], markeredgecolor=theme["surface"],
                markeredgewidth=2.0, zorder=4, linestyle="none")
        ax.text(row["l1_rate"], index + 0.22, f"L1 {row['l1_rate']:.3f}",
                color=theme["ink_2"], fontsize=8.5, ha="center", va="bottom")
        ax.text(row["l2_rate"], index - 0.24, f"{row['l2_rate']:.3f}",
                color=theme["ink"], fontsize=9.5, ha="center", va="top",
                fontweight="bold")

    ax.set_yticks(y)
    ax.set_yticklabels(labels, color=theme["ink"], fontsize=9.5)
    ax.set_ylim(-0.55, len(rows) - 0.45)
    ax.set_xlim(-0.14, 1.12)
    ax.set_xticks([0.0, 0.25, 0.5, 0.75, 1.0])
    ax.set_xticklabels(["0", "0.25", "0.5", "0.75", "1.0"])
    ax.set_xlabel("recovery rate", color=theme["ink_2"], fontsize=9.5, labelpad=8)
    figure.subplots_adjust(top=0.80, left=0.24, right=0.98, bottom=0.15)
    figure.text(0.02, 0.94, "L1 assumes a self-healing the live stack does not have",
                color=theme["ink"], fontsize=13, fontweight="bold", va="bottom")
    figure.text(
        0.02, 0.895,
        "30 samples per family against the Gazebo stack; the bar is the measured "
        "rate's 95% Wilson interval",
        color=theme["ink_2"], fontsize=9.5, va="bottom",
    )
    handles = [
        plt.Line2D([], [], marker="o", markersize=8, linestyle="none",
                   color=theme["context"], label="L1 assumes"),
        plt.Line2D([], [], marker="o", markersize=8, linestyle="none",
                   color=theme["focus"], label="the stack measured"),
    ]
    legend = figure.legend(
        handles=handles, loc="upper right", bbox_to_anchor=(0.98, 0.97),
        frameon=False, fontsize=9.5, ncol=2, handletextpad=0.4, columnspacing=1.6,
    )
    for text in legend.get_texts():
        text.set_color(theme["ink_2"])
    _save(figure, path, theme)
    plt.close(figure)


def _save(figure, path: Path, theme: dict[str, str]) -> None:
    """Write the figure, deterministically.

    The metadata is pinned so that regenerating an unchanged figure produces an
    unchanged file: a diff on the SVGs then means the data moved.
    """
    figure.savefig(
        path,
        format=path.suffix.lstrip(".") or "svg",
        facecolor=theme["surface"],
        metadata={"Date": None},
    )


def draw_wire_faults(rows: list[dict[str, Any]], theme: dict[str, str], path: Path) -> None:
    """What each transport fault cost the command.

    Two bars per scenario: the healthy dispatch and the one that ran while the
    fault was in place. Two `unknown` outcomes reach the 30 s timeout, and that
    contrast is the point -- so the axis is linear and the difference is visible.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = list(reversed(rows))
    labels = [row["label"] for row in rows]
    baseline = [row["baseline_seconds"] or 0.0 for row in rows]
    faulted = [row["faulted_seconds"] or 0.0 for row in rows]
    height = 0.34
    y = list(range(len(rows)))

    figure, ax = plt.subplots(figsize=(9.6, 5.2), dpi=170)
    figure.patch.set_facecolor(theme["surface"])
    _style(ax, theme)

    ax.barh([value + height / 2 + 0.02 for value in y], baseline, height=height,
            color=theme["context"], label="healthy dispatch", zorder=2)
    ax.barh([value - height / 2 - 0.02 for value in y], faulted, height=height,
            color=theme["focus"], label="under the fault", zorder=2)

    for index, row in enumerate(rows):
        ax.text((row["baseline_seconds"] or 0.0) + 0.5, index + height / 2 + 0.02,
                f"{row['baseline_seconds']:.2f}s", va="center", ha="left",
                color=theme["ink_2"], fontsize=8.5)
        if row["faulted_status"] == "unknown":
            ax.text((row["faulted_seconds"] or 0.0) + 0.5, index - height / 2 - 0.02,
                    "never came back", va="center", ha="left",
                    color=theme["note"], fontsize=8.5, fontweight="bold")
        else:
            ax.text((row["faulted_seconds"] or 0.0) + 0.5, index - height / 2 - 0.02,
                    f"{row['faulted_seconds']:.2f}s", va="center", ha="left",
                    color=theme["ink"], fontsize=8.5)

    ax.set_yticks(y)
    ax.set_yticklabels(labels, color=theme["ink"], fontsize=9.5)
    ax.set_xlim(0, 52)
    ax.set_xticks([0, 10, 20, 30, 40, 50])
    ax.set_xlabel("seconds for one navigation command", color=theme["ink_2"], fontsize=9.5,
                   labelpad=8)
    figure.subplots_adjust(top=0.82, left=0.24, right=0.98, bottom=0.13)
    figure.text(0.02, 0.945, "A lossy link either costs time or takes the command away",
                color=theme["ink"], fontsize=13, fontweight="bold", va="bottom")
    figure.text(
        0.02, 0.895,
        "the same command, once on a healthy link and once under each injected fault",
        color=theme["ink_2"], fontsize=9.5, va="bottom",
    )
    handles = [
        plt.Rectangle((0, 0), 1, 1, color=theme["context"], label="healthy dispatch"),
        plt.Rectangle((0, 0), 1, 1, color=theme["focus"], label="under the fault"),
    ]
    legend = figure.legend(
        handles=handles, loc="upper right", bbox_to_anchor=(0.98, 0.97),
        frameon=False, fontsize=9.5, ncol=2, handletextpad=0.6, columnspacing=1.6,
    )
    for text in legend.get_texts():
        text.set_color(theme["ink_2"])
    _save(figure, path, theme)
    plt.close(figure)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the sidecar is stale")
    args = parser.parse_args(argv)

    data = sidecar()
    if args.check:
        if not SIDECAR.exists():
            print(f"{SIDECAR} does not exist: run scripts/make_figures.py")
            return 1
        current = json.loads(SIDECAR.read_text(encoding="utf-8"))
        if current != data:
            print("the figures' numbers no longer match the artifacts")
            print(json.dumps(data, indent=2, sort_keys=True))
            return 1
        print("the figures' numbers match the artifacts")
        return 0

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.family"] = _fonts()
    plt.rcParams["svg.fonttype"] = "none"

    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    draw_calibration(data["calibration"], LIGHT, FIGURE_DIR / "calibration.svg")
    draw_calibration(data["calibration"], DARK, FIGURE_DIR / "calibration-dark.svg")
    draw_wire_faults(data["wire_faults"], LIGHT, FIGURE_DIR / "wire_faults.svg")
    draw_wire_faults(data["wire_faults"], DARK, FIGURE_DIR / "wire_faults-dark.svg")
    SIDECAR.write_text(
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"-> {FIGURE_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
