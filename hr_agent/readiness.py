"""Is a new starter actually ready?

Every item is answered by asking a configured system, never by assuming. An
earlier version of this returned three hardcoded strings for every new starter
at every company, which reported findings about named people that nobody had
checked. In a product whose argument is that it does not invent things, that was
the worst defect in the codebase.

Where a company has supplied no checklist, this reports exactly that, and the
agent hands over rather than producing an empty clean bill of health.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from . import db
from .packs.models import ReadinessItem, ResolvedConfig
from .tools import Registry


@dataclass
class Finding:
    item: ReadinessItem
    present: bool
    detail: str

    @property
    def outstanding(self) -> bool:
        return not self.present


@dataclass
class Report:
    configured: bool
    findings: list[Finding] = field(default_factory=list)
    unanswerable: list[str] = field(default_factory=list)

    @property
    def outstanding(self) -> list[Finding]:
        return [f for f in self.findings if f.outstanding]

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.outstanding if f.item.blocking]

    @property
    def ready(self) -> bool:
        return self.configured and not self.outstanding

    def as_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "ready": self.ready,
            "outstanding": [
                {"id": f.item.id, "label": f.item.label, "blocking": f.item.blocking, "detail": f.detail}
                for f in self.outstanding
            ],
            "satisfied": [{"id": f.item.id, "label": f.item.label} for f in self.findings if f.present],
            "unanswerable": self.unanswerable,
        }


def _filed_documents(conn: sqlite3.Connection, tenant_id: str, person_id: str) -> set[str]:
    """Document types actually filed for this person."""
    return {
        row["document_type"]
        for row in db.rows(
            conn,
            "SELECT document_type FROM filed_document WHERE tenant_id = ? AND person_id = ?",
            (tenant_id, person_id),
        )
    }


def check(conn: sqlite3.Connection, cfg: ResolvedConfig, *, person_id: str, registry: Registry | None = None) -> Report:
    """Answer every item on the company's checklist against its own systems."""
    if not cfg.readiness:
        return Report(configured=False)

    registry = registry or Registry(conn, cfg)
    report = Report(configured=True)
    filed = _filed_documents(conn, cfg.tenant_id, person_id)

    for item in cfg.readiness:
        if item.field:
            owner = registry.owning(item.field)
            if owner is None:
                # Should be impossible: the pack validator rejects this. Report
                # it rather than counting it as satisfied or as outstanding.
                report.unanswerable.append(f"{item.label}: no system owns {item.field}")
                continue
            held = owner.read("read_record", person_id=person_id)
            value = held.get(item.field)
            report.findings.append(
                Finding(item, bool(value), f"{owner.name} holds a value" if value else f"not set in {owner.name}")
            )
        else:
            present = item.document in filed
            report.findings.append(
                Finding(item, present, "filed" if present else "not received")
            )
    return report
