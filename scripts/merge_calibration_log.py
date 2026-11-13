"""Merge the calibration measurements of one family into the committed log.

    python3 scripts/merge_calibration_log.py results/calibration_lab.json \\
        /tmp/cal_grasp_failure.json

The committed log holds what the live stack was observed to do, family by
family. A family measured again replaces its own rows and leaves the others
alone: the log is a record of observations, not a summary of the last run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: merge_calibration_log.py LOG.json NEW.json [...]", file=sys.stderr)
        return 2
    log_path = Path(argv[0])
    payload = json.loads(log_path.read_text(encoding="utf-8"))
    families = {row["family"]: row for row in payload.get("families", [])}

    for source in argv[1:]:
        incoming = json.loads(Path(source).read_text(encoding="utf-8"))
        for row in incoming.get("families", []):
            families[row["family"]] = row
            print(f"{row['family']}: {row['successes']}/{row['samples']}")

    payload["families"] = [families[name] for name in sorted(families)]
    payload["unmeasured"] = [
        row for row in payload.get("unmeasured", []) if row.get("family") not in families
    ]
    log_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"-> {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
