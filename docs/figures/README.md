# docs/figures — the README's pictures

Every file here is **generated** from an artifact in the repository:

| File | Generated from |
|---|---|
| `calibration.svg`, `calibration-dark.svg` | `benchmarks/calibration_v1.json` |
| `wire_faults.svg`, `wire_faults-dark.svg` | `results/wire/wire_*.json` |
| `figures.json` | all of the above — the exact numbers each figure plots |
| `stack_camera.png` | the simulated camera, via `scripts/snapshot_camera.py` |

Regenerate them with:

```bash
python3 scripts/make_figures.py
```

and check that they still say what the data says with:

```bash
python3 scripts/make_figures.py --check   # also run by tests/test_figures.py
```

`figures.json` is the point of the arrangement. A picture cannot be checked by
reading it, so the numbers each figure plots are written next to it, and
`tests/test_figures.py` recomputes them from the artifacts and compares. A
figure cannot drift away from the data it claims to show.

The light and dark variants are the same chart with each mode's own steps —
the dark one is not an automatic inversion, and the colours are validated for
colour-vision deficiency on both surfaces.

Drawing needs matplotlib. Checking does not: `--check` only compares numbers,
so CI runs it without a plotting stack.
