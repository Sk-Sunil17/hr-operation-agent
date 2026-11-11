"""Measurement, with the caveats attached.

Deflection rate is the number this market quotes and it is close to meaningless:
it counts answers given, not answers that were right. What this module reports
instead:

  * How much went out on its own, how much a person approved, how much a person
    took, and how much was refused.
  * Whether answers that were required to cite a policy actually did.
  * Review time as a COST against queued work, not as a saving.
  * Nothing at all about saved time unless the company supplied its own baseline.

Every result carries `caveats`, and that list is never empty. A number from this
module cannot be quoted without the reasons it might be wrong.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from . import audit, db
from .packs.models import ResolvedConfig


@dataclass
class Report:
    tenant_id: str
    total: int
    by_status: dict[str, int] = field(default_factory=dict)
    by_rule: dict[str, int] = field(default_factory=dict)
    citation_required: int = 0
    citation_given: int = 0
    verify_failures: int = 0
    duplicates_suppressed: int = 0
    queued_awaiting: int = 0
    human_verdicts: dict[str, int] = field(default_factory=dict)
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    rejections_point_at: dict[str, int] = field(default_factory=dict)
    minutes_saved: int | None = None
    minutes_reviewing: int = 0
    caveats: list[str] = field(default_factory=list)

    @property
    def citation_coverage(self) -> float | None:
        if not self.citation_required:
            return None
        return self.citation_given / self.citation_required

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "total": self.total,
            "by_status": self.by_status,
            "by_rule": self.by_rule,
            "citation_required": self.citation_required,
            "citation_given": self.citation_given,
            "citation_coverage": self.citation_coverage,
            "verify_failures": self.verify_failures,
            "duplicates_suppressed": self.duplicates_suppressed,
            "queued_awaiting": self.queued_awaiting,
            "human_verdicts": self.human_verdicts,
            "rejection_reasons": self.rejection_reasons,
            "rejections_point_at": self.rejections_point_at,
            "minutes_saved": self.minutes_saved,
            "minutes_reviewing": self.minutes_reviewing,
            "caveats": self.caveats,
        }


def compute(conn: sqlite3.Connection, cfg: ResolvedConfig) -> Report:
    tenant = cfg.tenant_id
    settings = cfg.manifest.metrics

    def count(sql: str, params: tuple = ()) -> int:
        return db.one(conn, sql, (tenant, *params))["n"]

    total = count("SELECT COUNT(*) AS n FROM work_item WHERE tenant_id = ?")
    report = Report(tenant_id=tenant, total=total)

    report.by_status = {
        r["status"]: r["n"]
        for r in db.rows(
            conn, "SELECT status, COUNT(*) AS n FROM work_item WHERE tenant_id = ? GROUP BY status", (tenant,)
        )
    }
    report.by_rule = {
        r["rule_id"]: r["n"]
        for r in db.rows(
            conn,
            "SELECT rule_id, COUNT(*) AS n FROM work_item WHERE tenant_id = ? AND rule_id IS NOT NULL "
            "GROUP BY rule_id ORDER BY n DESC",
            (tenant,),
        )
    }
    report.human_verdicts = {
        r["human_verdict"]: r["n"]
        for r in db.rows(
            conn,
            "SELECT human_verdict, COUNT(*) AS n FROM decision WHERE tenant_id = ? AND human_verdict IS NOT NULL "
            "GROUP BY human_verdict",
            (tenant,),
        )
    }

    # Why people rejected drafts, and which part of the agent that implicates.
    from .approvals import REASON_CODES, REASON_POINTS_AT

    for row in db.rows(
        conn,
        "SELECT human_reason_code AS code, COUNT(*) AS n FROM decision "
        "WHERE tenant_id = ? AND human_reason_code IS NOT NULL GROUP BY human_reason_code",
        (tenant,),
    ):
        label = REASON_CODES.get(row["code"], row["code"])
        report.rejection_reasons[label] = row["n"]
        points_at = REASON_POINTS_AT.get(row["code"], "unclassified")
        report.rejections_point_at[points_at] = report.rejections_point_at.get(points_at, 0) + row["n"]

    # Citation coverage counts only ANSWERS that were required to cite. Two
    # exclusions matter and both are deliberate:
    #
    #   * acknowledgements and refusals would flatter the number if counted
    #   * a rule can require a citation and then be tightened into a handover by
    #     a segment overlay. The handover answers nothing, so it has nothing to
    #     quote; counting it as a missing citation reports a fault that is not one
    answering = "AND mode IN ('auto', 'prepare')"
    report.citation_required = count(
        f"SELECT COUNT(*) AS n FROM decision WHERE tenant_id = ? AND needs_citation = 1 {answering}"
    )
    report.citation_given = count(
        "SELECT COUNT(DISTINCT d.id) AS n FROM decision d JOIN citation c ON c.decision_id = d.id "
        f"WHERE d.tenant_id = ? AND d.needs_citation = 1 {answering}"
    )
    report.verify_failures = count("SELECT COUNT(*) AS n FROM decision WHERE tenant_id = ? AND verify_passed = 0")
    report.duplicates_suppressed = max(
        0,
        count("SELECT COUNT(*) AS n FROM event WHERE tenant_id = ?")
        - count("SELECT COUNT(*) AS n FROM work_item WHERE tenant_id = ?"),
    )
    report.queued_awaiting = count("SELECT COUNT(*) AS n FROM approval WHERE tenant_id = ? AND status = 'open'")

    answered = report.by_status.get("answered", 0)
    queued = report.by_status.get("queued", 0)

    # Review time is a cost, always counted. Saved time is only ever claimed
    # against a baseline the company supplied itself.
    report.minutes_reviewing = (queued + report.queued_awaiting) * settings.review_minutes_per_queued_case
    if settings.baseline_minutes_per_case is None:
        report.caveats.append(
            "No saving is reported: this company has not supplied a baseline for how long "
            "one of these took a person before. Set metrics.baseline_minutes_per_case in the pack."
        )
    else:
        report.minutes_saved = answered * settings.baseline_minutes_per_case
        report.caveats.append(
            f"Saved time uses this company's own estimate of {settings.baseline_minutes_per_case} minutes "
            "per case. It is an estimate supplied by the company, not a measurement."
        )

    # Caveats that are always true, and one that is conditionally alarming.
    report.caveats.append(
        "Only cases the agent sent on its own count towards saving. Anything a person "
        "approved, took over or was handed counts review time instead."
    )
    if total < settings.small_sample_below:
        report.caveats.append(
            f"Small sample: {total} case(s), below the {settings.small_sample_below} this company "
            "set as meaningful. Treat every proportion here as indicative only."
        )
    if report.citation_required and report.citation_coverage is not None and report.citation_coverage < 1:
        report.caveats.append(
            f"{report.citation_required - report.citation_given} answer(s) that required a citation did not "
            "carry one. That should be zero; investigate before trusting anything else here."
        )
    if report.rejections_point_at:
        worst = max(report.rejections_point_at.items(), key=lambda kv: kv[1])
        if worst[1] >= 3:
            report.caveats.append(
                f"{worst[1]} rejected draft(s) point at {worst[0]}. A pattern in one place is worth "
                "looking at before reading anything else here as a quality signal."
            )

    chain = audit.verify(conn, tenant)
    if not chain.ok:
        report.caveats.append(
            f"The audit chain for this company is broken at row {chain.broken_at}. Until that is "
            "explained, no number on this page can be relied on."
        )
    return report
