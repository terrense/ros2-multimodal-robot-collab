"""JSONL trajectory protocol shared by SFT / DPO / rollout / evaluation.

One line per step:
  {"record": "step", "episode_id", "step_index", "observation", "action",
   "result", "reward_components", "phase", "progress"}
One line per episode close:
  {"record": "episode", "episode_id", "scenario", "summary", "source", "seed"}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TextIO

from .protocol import Action, Observation, StepResult


class TrajectoryLogger:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle: TextIO = self.path.open("a", encoding="utf-8")

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "TrajectoryLogger":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def log_step(
        self,
        episode_id: str,
        step_index: int,
        observation: Observation,
        action: Action,
        result: StepResult,
        reward_components: dict[str, float],
        info: dict[str, Any],
    ) -> None:
        self._write(
            {
                "record": "step",
                "episode_id": episode_id,
                "step_index": step_index,
                "observation": observation.to_dict(),
                "action": action.to_dict(),
                "result": result.to_dict(),
                "reward_components": reward_components,
                "phase": info.get("phase"),
                "progress": info.get("progress"),
            }
        )

    def log_episode(
        self,
        episode_id: str,
        scenario: dict[str, Any],
        summary: dict[str, Any],
        source: str,
        seed: int,
    ) -> None:
        self._write(
            {
                "record": "episode",
                "episode_id": episode_id,
                "scenario": scenario,
                "summary": summary,
                "source": source,
                "seed": seed,
            }
        )

    def _write(self, payload: dict[str, Any]) -> None:
        self._handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def read_records(path: Path | str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records
