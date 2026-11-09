"""Durable execution ledger.

The ledger is the *record* of what was submitted and what is known about it. It
is never the authority for mission state; the mission state machine owns that.

Three rules make it worth having:

1. a submission is written before it is dispatched, so a crash leaves a trace
   of a command that may have moved the robot;
2. a terminal receipt is never rewritten by a late or contradictory one, so a
   cancel that arrives after completion cannot rewrite history;
3. an `unknown` row can only leave `unknown` with named evidence — a receipt
   read back, or an operator acknowledgement that never claims success.

Stdlib only: ``sqlite3`` gives transactions and crash safety without adding a
dependency. The connection is shared with worker threads, so every operation
holds a lock; SQLite itself serialises writes, but our multi-statement
transactions must not interleave.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_harness.contracts import (
    ActionRequest,
    ExecutionReceipt,
    ExecutionStatus,
    FailureCode,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS commands (
    command_id     TEXT PRIMARY KEY,
    episode_id     TEXT NOT NULL,
    capability     TEXT NOT NULL,
    resource_key   TEXT NOT NULL,
    goal_uuid      TEXT,
    status         TEXT NOT NULL,
    call_ok        INTEGER NOT NULL DEFAULT 0,
    physical_effect_verified INTEGER,
    failure_code   TEXT,
    detail         TEXT NOT NULL DEFAULT '',
    created_at     REAL NOT NULL,
    started_at     REAL,
    finished_at    REAL,
    reconciled_by  TEXT,
    receipt_json   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id  TEXT NOT NULL,
    at          REAL NOT NULL,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS commands_status ON commands(status);
"""

_TERMINAL = ("completed", "failed", "rejected")


class DuplicateCommand(RuntimeError):
    """A command id was submitted twice. This is a client bug, not a retry."""


class LedgerError(RuntimeError):
    pass


@dataclass(frozen=True)
class LedgerRecord:
    command_id: str
    episode_id: str
    capability: str
    resource_key: str
    status: ExecutionStatus
    goal_uuid: str | None
    failure_code: FailureCode | None
    reconciled_by: str | None
    created_at: float
    finished_at: float | None
    receipt: dict[str, Any]


class ExecutionLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------------ writes

    def record_submitted(self, request: ActionRequest, receipt: ExecutionReceipt) -> None:
        with self._lock:
            try:
                with self._conn:
                    self._conn.execute(
                        "INSERT INTO commands (command_id, episode_id, capability,"
                        " resource_key, status, call_ok, failure_code, detail,"
                        " created_at, receipt_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (
                            request.command_id,
                            request.episode_id,
                            request.capability,
                            request.resource_key,
                            receipt.execution_status.value,
                            int(receipt.call_ok),
                            receipt.failure_code.value if receipt.failure_code else None,
                            receipt.detail,
                            time.time(),
                            json.dumps(receipt.to_dict(), ensure_ascii=False),
                        ),
                    )
            except sqlite3.IntegrityError as error:
                raise DuplicateCommand(
                    f"command {request.command_id!r} already exists"
                ) from error
            self._event_locked(request.command_id, "submitted", {"capability": request.capability})

    def record_started(self, command_id: str, goal_uuid: str | None = None) -> bool:
        with self._lock:
            with self._conn:
                cursor = self._conn.execute(
                    "UPDATE commands SET status=?, started_at=?,"
                    " goal_uuid=COALESCE(?, goal_uuid)"
                    " WHERE command_id=? AND status NOT IN (?,?,?)",
                    (
                        ExecutionStatus.RUNNING.value,
                        time.time(),
                        goal_uuid,
                        command_id,
                        *_TERMINAL,
                    ),
                )
            started = bool(cursor.rowcount)
            if started:
                self._event_locked(command_id, "started", {"goal_uuid": goal_uuid})
            return started

    def record_finished(self, receipt: ExecutionReceipt) -> bool:
        """Write a terminal receipt. A terminal row is never rewritten."""
        with self._lock:
            with self._conn:
                cursor = self._conn.execute(
                    "UPDATE commands SET status=?, call_ok=?, physical_effect_verified=?,"
                    " failure_code=?, detail=?, finished_at=?, receipt_json=?,"
                    " goal_uuid=COALESCE(?, goal_uuid)"
                    " WHERE command_id=? AND status NOT IN (?,?,?)",
                    (
                        receipt.execution_status.value,
                        int(receipt.call_ok),
                        None
                        if receipt.physical_effect_verified is None
                        else int(receipt.physical_effect_verified),
                        receipt.failure_code.value if receipt.failure_code else None,
                        receipt.detail,
                        receipt.finished_at or time.time(),
                        json.dumps(receipt.to_dict(), ensure_ascii=False),
                        receipt.backend_ref,
                        receipt.command_id,
                        *_TERMINAL,
                    ),
                )
            applied = bool(cursor.rowcount)
            if applied:
                self._event_locked(receipt.command_id, "finished", receipt.to_dict())
            else:
                self._event_locked(
                    receipt.command_id,
                    "ignored_terminal",
                    {"arrived": receipt.execution_status.value},
                )
            return applied

    def record_event(self, command_id: str, kind: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._event_locked(command_id, kind, payload)

    def _event_locked(self, command_id: str, kind: str, payload: dict[str, Any]) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO events (command_id, at, kind, payload) VALUES (?,?,?,?)",
                (command_id, time.time(), kind, json.dumps(payload, ensure_ascii=False)),
            )

    # --------------------------------------------------------- reconciliation

    def mark_orphans(self) -> int:
        """Called on startup: anything not terminal died with the previous process."""
        with self._lock:
            with self._conn:
                cursor = self._conn.execute(
                    "UPDATE commands SET status=?, failure_code=?, reconciled_by=NULL"
                    " WHERE status NOT IN (?,?,?)",
                    (
                        ExecutionStatus.UNKNOWN.value,
                        FailureCode.EXECUTION_UNCERTAIN.value,
                        *_TERMINAL,
                    ),
                )
                command_ids = [
                    row["command_id"]
                    for row in self._conn.execute(
                        "SELECT command_id FROM commands WHERE status=?",
                        (ExecutionStatus.UNKNOWN.value,),
                    )
                ]
            for command_id in command_ids:
                self._event_locked(command_id, "orphaned", {"reason": "process restart"})
            return int(cursor.rowcount)

    def unresolved(self) -> list[LedgerRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM commands WHERE status=? ORDER BY created_at",
                (ExecutionStatus.UNKNOWN.value,),
            ).fetchall()
        return [self._to_record(row) for row in rows]

    def acknowledge_unknown(self, command_id: str, *, operator: str, note: str) -> LedgerRecord:
        """Clear an orphan by human decision. It becomes failed, never completed."""
        if not operator or not note:
            raise ValueError("acknowledgement requires both an operator and a note")
        with self._lock:
            with self._conn:
                cursor = self._conn.execute(
                    "UPDATE commands SET status=?, reconciled_by=?, finished_at=?"
                    " WHERE command_id=? AND status=?",
                    (
                        ExecutionStatus.FAILED.value,
                        f"operator:{operator}",
                        time.time(),
                        command_id,
                        ExecutionStatus.UNKNOWN.value,
                    ),
                )
            if not cursor.rowcount:
                raise LedgerError(f"command {command_id!r} is not an unresolved orphan")
            self._event_locked(command_id, "acknowledged", {"operator": operator, "note": note})
            record = self.get(command_id)
            assert record is not None
            return record

    # ------------------------------------------------------------------ reads

    def get(self, command_id: str) -> LedgerRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM commands WHERE command_id=?", (command_id,)
            ).fetchone()
        return None if row is None else self._to_record(row)

    def count(self, status: ExecutionStatus | None = None) -> int:
        with self._lock:
            if status is None:
                row = self._conn.execute("SELECT COUNT(*) AS n FROM commands").fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM commands WHERE status=?", (status.value,)
                ).fetchone()
        return int(row["n"])

    def events(self, command_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT at, kind, payload FROM events WHERE command_id=? ORDER BY id",
                (command_id,),
            ).fetchall()
        return [
            {"at": row["at"], "kind": row["kind"], "payload": json.loads(row["payload"])}
            for row in rows
        ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _to_record(row: sqlite3.Row) -> LedgerRecord:
        return LedgerRecord(
            command_id=row["command_id"],
            episode_id=row["episode_id"],
            capability=row["capability"],
            resource_key=row["resource_key"],
            status=ExecutionStatus(row["status"]),
            goal_uuid=row["goal_uuid"],
            failure_code=FailureCode(row["failure_code"]) if row["failure_code"] else None,
            reconciled_by=row["reconciled_by"],
            created_at=row["created_at"],
            finished_at=row["finished_at"],
            receipt=json.loads(row["receipt_json"]),
        )
