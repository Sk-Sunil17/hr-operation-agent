"""Exit cases: the one thing a person starts that the agent then works against.

The agent never starts an exit. Someone in HR opens the case, with a reason,
and from then on the company's departure checklist can be prepared against it
(rule X2). Without the case the same request is declined (rule X1).
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import date
from typing import Any

from . import audit, db
from .packs.models import ResolvedConfig
from .redact import Redactor


class CaseError(RuntimeError):
    """The case cannot be opened or closed as asked."""


def _hr_roles(cfg: ResolvedConfig, actor_id: str | None) -> list[str]:
    person = next((p for p in cfg.people if p.id == actor_id and p.active), None)
    return [r for r in (person.roles if person else []) if r in cfg.approvals.hr_roles]


def open_exits(conn: sqlite3.Connection, cfg: ResolvedConfig) -> list[dict[str, Any]]:
    names = {p.id: p.name for p in cfg.people}
    rows = db.rows(
        conn,
        "SELECT * FROM hr_case WHERE tenant_id = ? AND kind = 'exit' AND status = 'open' ORDER BY opened_at DESC",
        (cfg.tenant_id,),
    )
    return [
        {**dict(r), "subject_name": names.get(r["subject_id"], r["subject_id"]),
         "owner_name": names.get(r["owner_id"], r["owner_id"])}
        for r in rows
    ]


def open_exit(
    conn: sqlite3.Connection,
    cfg: ResolvedConfig,
    *,
    subject_id: str,
    actor_id: str,
    last_day: date | None,
    note: str,
) -> dict[str, Any]:
    roles = _hr_roles(cfg, actor_id)
    if not roles:
        raise CaseError("Only someone in an HR role can start a leaver.")
    subject = next((p for p in cfg.people if p.id == subject_id), None)
    if subject is None or not subject.active:
        raise CaseError(f"There is no current employee {subject_id!r}.")
    if subject_id == actor_id:
        raise CaseError("Nobody opens their own exit case; another HR person does that.")
    note = (note or "").strip()
    if len(note) < 5:
        raise CaseError("Say why, in a few words: the note is what an auditor reads later.")
    existing = db.one(
        conn,
        "SELECT id FROM hr_case WHERE tenant_id = ? AND subject_id = ? AND kind = 'exit' AND status = 'open'",
        (cfg.tenant_id, subject_id),
    )
    if existing:
        raise CaseError(f"{subject.name} already has an open exit case.")

    case_id = "case_" + uuid.uuid4().hex[:12]
    db.insert(conn, "hr_case", {
        "id": case_id, "tenant_id": cfg.tenant_id, "kind": "exit", "subject_id": subject_id,
        "owner_role": roles[0], "owner_id": actor_id, "status": "open", "taken_over": 0,
        "last_day": last_day.isoformat() if last_day else None, "opened_at": db.now(),
    })
    audit.append(
        conn, tenant_id=cfg.tenant_id, actor=actor_id, actor_kind="person", stage="decide",
        action=f"exit case opened for {subject.name}",
        redactor=Redactor(cfg.redaction),
        detail={"case_id": case_id, "subject_id": subject_id,
                "last_day": last_day.isoformat() if last_day else None, "note": note},
        config_hash=cfg.config_hash,
    )
    return {"case_id": case_id, "subject_id": subject_id, "subject_name": subject.name}


def close_exit(conn: sqlite3.Connection, cfg: ResolvedConfig, *, case_id: str, actor_id: str, note: str) -> dict[str, Any]:
    if not _hr_roles(cfg, actor_id):
        raise CaseError("Only someone in an HR role can close an exit case.")
    note = (note or "").strip()
    if len(note) < 5:
        raise CaseError("Say why, in a few words: the note is what an auditor reads later.")
    closed = conn.execute(
        "UPDATE hr_case SET status = 'closed', closed_at = ? "
        "WHERE id = ? AND tenant_id = ? AND kind = 'exit' AND status = 'open'",
        (db.now(), case_id, cfg.tenant_id),
    ).rowcount
    if not closed:
        raise CaseError("That exit case is not open.")
    audit.append(
        conn, tenant_id=cfg.tenant_id, actor=actor_id, actor_kind="person", stage="decide",
        action="exit case closed", redactor=Redactor(cfg.redaction),
        detail={"case_id": case_id, "note": note}, config_hash=cfg.config_hash,
    )
    return {"case_id": case_id, "status": "closed"}
