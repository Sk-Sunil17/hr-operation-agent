"""The audit trail: append-only and hash-chained, one chain per tenant.

Each row's hash covers the previous row's hash, so the rows form a chain:

    row_hash = sha256( prev_hash | tenant | at | actor | kind | stage | action | detail )

Editing a row changes its own hash. Deleting a row breaks the link between its
neighbours. Either way `verify()` reports the exact row where the chain stops
being consistent, which is the difference between an audit trail and a log file.

There is no update or delete path in this module, and a test in `tests/test_db.py`
asserts that no such statement exists anywhere in the codebase.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from . import db
from .redact import Redactor, guard

# The stages a row may belong to. These are the same stations the work-item
# screen draws, so the trail and the UI cannot drift apart.
# Same order the work-item rail draws, including retrieve before decide.
STAGES = ("receive", "understand", "retrieve", "decide", "draft", "verify", "act", "handover")
ACTOR_KINDS = ("agent", "rule", "person", "scheduler")

GENESIS = ""  # the prev_hash a tenant's first row chains from


def _row_hash(
    prev_hash: str | None,
    tenant_id: str,
    at: str,
    actor: str,
    actor_kind: str,
    stage: str,
    action: str,
    detail_json: str,
) -> str:
    payload = json.dumps(
        [prev_hash or GENESIS, tenant_id, at, actor, actor_kind, stage, action, detail_json],
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def append(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    actor: str,
    actor_kind: str,
    stage: str,
    action: str,
    redactor: Redactor,
    work_item_id: str | None = None,
    detail: Any = None,
    config_hash: str | None = None,
) -> str:
    """Add one row to a tenant's chain and return its hash.

    `detail` is redacted before it is stored. The redactor is a required argument
    rather than an optional one so that there is no quiet path to writing
    unredacted text into the permanent record.
    """
    guard()
    if stage not in STAGES:
        raise ValueError(f"unknown audit stage {stage!r}; expected one of {', '.join(STAGES)}")
    if actor_kind not in ACTOR_KINDS:
        raise ValueError(f"unknown actor kind {actor_kind!r}; expected one of {', '.join(ACTOR_KINDS)}")

    detail_json = json.dumps(redactor.mapping(detail if detail is not None else {}), sort_keys=True, separators=(",", ":"))
    at = db.now()

    conn.execute("BEGIN IMMEDIATE")
    try:
        previous = db.one(conn, "SELECT row_hash FROM audit WHERE tenant_id = ? ORDER BY seq DESC LIMIT 1", (tenant_id,))
        prev_hash = previous["row_hash"] if previous else None
        digest = _row_hash(prev_hash, tenant_id, at, actor, actor_kind, stage, action, detail_json)
        conn.execute(
            "INSERT INTO audit (tenant_id, work_item_id, at, actor, actor_kind, stage, action, detail_json, "
            "config_hash, prev_hash, row_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (tenant_id, work_item_id, at, actor, actor_kind, stage, action, detail_json, config_hash, prev_hash, digest),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return digest


@dataclass
class Verification:
    """The result of walking a chain. `ok` is the only thing most callers read."""

    ok: bool
    checked: int
    tenants: list[str] = field(default_factory=list)
    broken_at: int | None = None
    reason: str | None = None

    def __str__(self) -> str:
        if self.ok:
            return f"chain intact: {self.checked} row(s) across {len(self.tenants)} tenant(s)"
        return f"chain broken at row {self.broken_at}: {self.reason}"


def verify(conn: sqlite3.Connection, tenant_id: str | None = None) -> Verification:
    """Walk each tenant's chain and recompute every hash.

    Reports the first row that does not agree with the record, distinguishing an
    edited row from a removed one, because the two call for different responses.
    """
    tenants = (
        [tenant_id]
        if tenant_id
        else [r["tenant_id"] for r in db.rows(conn, "SELECT DISTINCT tenant_id FROM audit ORDER BY tenant_id")]
    )
    checked = 0
    for tid in tenants:
        prev_hash: str | None = None
        for row in db.rows(conn, "SELECT * FROM audit WHERE tenant_id = ? ORDER BY seq", (tid,)):
            checked += 1
            if row["prev_hash"] != prev_hash:
                return Verification(
                    ok=False,
                    checked=checked,
                    tenants=tenants,
                    broken_at=row["seq"],
                    reason=(
                        f"row {row['seq']} chains from {row['prev_hash'] or 'the start'} but the row before it "
                        f"ends at {prev_hash or 'the start'}. A row has been removed or reordered."
                    ),
                )
            expected = _row_hash(
                row["prev_hash"],
                row["tenant_id"],
                row["at"],
                row["actor"],
                row["actor_kind"],
                row["stage"],
                row["action"],
                row["detail_json"],
            )
            if expected != row["row_hash"]:
                return Verification(
                    ok=False,
                    checked=checked,
                    tenants=tenants,
                    broken_at=row["seq"],
                    reason=f"row {row['seq']} does not match its own hash. Its contents have been changed since it was written.",
                )
            prev_hash = row["row_hash"]
    return Verification(ok=True, checked=checked, tenants=tenants)


def trail(conn: sqlite3.Connection, tenant_id: str, work_item_id: str) -> list[sqlite3.Row]:
    """Everything that happened to one work item, oldest first."""
    return db.rows(
        conn,
        "SELECT * FROM audit WHERE tenant_id = ? AND work_item_id = ? ORDER BY seq",
        (tenant_id, work_item_id),
    )


def count(conn: sqlite3.Connection, tenant_id: str | None = None) -> int:
    if tenant_id:
        return db.one(conn, "SELECT COUNT(*) AS n FROM audit WHERE tenant_id = ?", (tenant_id,))["n"]
    return db.one(conn, "SELECT COUNT(*) AS n FROM audit")["n"]
