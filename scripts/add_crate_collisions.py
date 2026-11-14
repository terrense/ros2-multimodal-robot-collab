"""Give the workshop crates real collisions.

The crates were visual only: the base drove straight through them, and the
arm on the base went through them too. A crate that a robot can walk
through is not an obstacle, it is a picture of one.

The collision is the same box the visual is, in the model's own frame, so
the crate is exactly as big as it looks.
"""

from __future__ import annotations

import re
from pathlib import Path

WORLD = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "robot_collab_sim"
    / "worlds"
    / "collab_lab.world"
)


def add_collisions(text: str) -> tuple[str, int]:
    pattern = re.compile(
        r'(<model name="feature_crate_[a-i]">.*?<link name="link">)'
        r"(<visual name=\"visual\">.*?</visual>)"
        r"(\s*</link>)",
        re.DOTALL,
    )

    def _replace(match: re.Match[str]) -> str:
        visual = match.group(2)
        size = re.search(r"<size>([^<]+)</size>", visual)
        if size is None:
            return match.group(0)
        collision = (
            '<collision name="collision"><geometry><box>'
            f"<size>{size.group(1)}</size>"
            "</box></geometry></collision>"
        )
        return match.group(1) + visual + collision + match.group(3)

    return pattern.subn(_replace, text)


def main() -> int:
    text = WORLD.read_text(encoding="utf-8")
    patched, count = add_collisions(text)
    if not count:
        raise SystemExit("no crates were found in the lab world")
    WORLD.write_text(patched, encoding="utf-8")
    print(f"gave {count} crates a collision")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
