"""Scheduled jobs. Every one of them reports and closes nothing.

Four jobs, and a rule they all obey: a job finds things and tells a person. It
does not chase, file, remind or decide. That is not timidity — an agent that
acts unattended at three in the morning is the one that will do something
nobody approved, and the audit row will say `scheduler` where a name should be.

Reminders are the exception that proves it: the agent can propose a reminder,
but every company caps how many times one person may be chased about one thing,
and the cap is counted here rather than hoped for.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from . import db, interviews as interviews_mod, kernel, readiness
from .packs.models import ResolvedConfig

JOBS = ("expiry_scan", "readiness", "policy_review", "reminders", "interview_horizon")

EXPIRY_WINDOW_DAYS = 60     # how far ahead an expiry is worth mentioning
INTERVIEW_WINDOW_DAYS = 14  # far enough ahead that a gap can still be fixed


@dataclass
class JobResult:
    job: str
    ran: bool
    found: int = 0
    summary: str = ""
    detail: str = ""
    work_item_id: str | None = None
    skipped_because: str | None = None
    items: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "job": self.job, "ran": self.ran, "found": self.found, "summary": self.summary,
            "work_item_id": self.work_item_id, "skipped_because": self.skipped_because,
            "items": self.items,
        }


def _people(cfg: ResolvedConfig):
    return [p for p in cfg.people if p.active]


# ---------------------------------------------------------------- the jobs


def expiry_scan(conn: sqlite3.Connection, cfg: ResolvedConfig, *, on: date) -> JobResult:
    """Documents that run out soon, or already have."""
    horizon = (on + timedelta(days=EXPIRY_WINDOW_DAYS)).isoformat()
    rows = db.rows(
        conn,
        "SELECT * FROM filed_document WHERE tenant_id = ? AND expires_at IS NOT NULL "
        "AND expires_at <= ? ORDER BY expires_at",
        (cfg.tenant_id, horizon),
    )
    names = {p.id: p.name for p in cfg.people}
    items = []
    for row in rows:
        days = (date.fromisoformat(row["expires_at"]) - on).days
        items.append({
            "person_id": row["person_id"], "person": names.get(row["person_id"], row["person_id"]),
            "document": row["document_type"], "expires_at": row["expires_at"], "days": days,
            "already_expired": days < 0,
        })
    if not items:
        return JobResult("expiry_scan", ran=True, found=0,
                         summary=f"Nothing expires in the next {EXPIRY_WINDOW_DAYS} days.")

    expired = [i for i in items if i["already_expired"]]
    lines = [
        f"  - {i['person']}: {i['document']} "
        + (f"expired {abs(i['days'])} days ago" if i["already_expired"] else f"expires in {i['days']} days")
        + f" ({i['expires_at']})"
        for i in items
    ]
    return JobResult(
        "expiry_scan", ran=True, found=len(items),
        summary=f"{len(items)} document(s) expiring within {EXPIRY_WINDOW_DAYS} days"
                + (f", {len(expired)} already expired" if expired else "") + ".",
        detail="\n".join(lines), items=items,
    )


def readiness_scan(conn: sqlite3.Connection, cfg: ResolvedConfig, *, on: date) -> JobResult:
    """New starters inside the lead window, and what is outstanding for each.

    Runs only inside the window the company set. Outside it there is nothing
    useful to say, and saying it anyway trains people to ignore the report.
    """
    lead = cfg.manifest.sla.onboarding_lead_days
    if not cfg.readiness:
        return JobResult("readiness", ran=False, skipped_because="no checklist is configured for this company")

    items, lines = [], []
    for person in _people(cfg):
        if not person.start_date:
            continue
        days = (date.fromisoformat(person.start_date) - on).days
        if not 0 <= days <= lead:
            continue
        report = readiness.check(conn, cfg, person_id=person.id)
        outstanding = report.outstanding
        items.append({
            "person_id": person.id, "person": person.name, "days": days,
            "outstanding": [f.item.label for f in outstanding],
            "blocking": [f.item.label for f in report.blocking],
        })
        if outstanding:
            lines.append(f"  - {person.name}, starts in {days} days: " + ", ".join(f.item.label for f in outstanding))
        else:
            lines.append(f"  - {person.name}, starts in {days} days: nothing outstanding")

    if not items:
        return JobResult("readiness", ran=True, found=0,
                         summary=f"No new starters within the next {lead} days.")
    behind = [i for i in items if i["blocking"]]
    return JobResult(
        "readiness", ran=True, found=len(items),
        summary=f"{len(items)} new starter(s) within {lead} days"
                + (f", {len(behind)} with something that blocks a start date" if behind else "") + ".",
        detail="\n".join(lines), items=items,
    )


def policy_review(conn: sqlite3.Connection, cfg: ResolvedConfig, *, on: date) -> JobResult:
    """Policies past their own review date. Notifies the owner; changes nothing."""
    items, lines = [], []
    for doc in cfg.policies:
        if not doc.review_months:
            continue
        live = [v for v in doc.versions if v.status == "in_force" and v.effective_from]
        if not live:
            continue
        newest = max(live, key=lambda v: v.effective_from)
        due = kernel._add_months(date.fromisoformat(newest.effective_from), doc.review_months)
        if due > on:
            continue
        items.append({"policy": doc.id, "title": doc.title, "owner": doc.owner_role,
                      "version": newest.version, "due": due.isoformat()})
        lines.append(f"  - {doc.title} v{newest.version}: review was due {due.isoformat()} "
                     f"(owner: {doc.owner_role or 'nobody named'})")
    if not items:
        return JobResult("policy_review", ran=True, found=0, summary="Every policy is within its review period.")
    return JobResult("policy_review", ran=True, found=len(items),
                     summary=f"{len(items)} policy review(s) overdue.", detail="\n".join(lines), items=items)


def reminders(conn: sqlite3.Connection, cfg: ResolvedConfig, *, on: date) -> JobResult:
    """What could be chased, and what has already been chased too often.

    The cap is this company's, and it is counted rather than assumed. Proposing
    a reminder is as far as this goes: sending it is still an approval.
    """
    cap = cfg.manifest.sla.reminder_cap
    if not cfg.readiness:
        return JobResult("reminders", ran=False, skipped_because="no checklist, so there is nothing to chase about")

    sent = {
        (r["subject_id"], r["about"]): r
        for r in db.rows(conn, "SELECT * FROM reminder WHERE tenant_id = ?", (cfg.tenant_id,))
    }
    due, capped = [], []
    for person in _people(cfg):
        if not person.start_date:
            continue
        days = (date.fromisoformat(person.start_date) - on).days
        if not 0 <= days <= cfg.manifest.sla.onboarding_lead_days:
            continue
        for finding in readiness.check(conn, cfg, person_id=person.id).outstanding:
            record = sent.get((person.id, finding.item.id))
            already = record["sent_count"] if record else 0
            entry = {"person_id": person.id, "person": person.name, "about": finding.item.label,
                     "item_id": finding.item.id, "sent": already}
            (capped if already >= cap else due).append(entry)

    lines = []
    if due:
        lines += [f"Could be chased (cap is {cap} per person per item):"]
        lines += [f"  - {d['person']}: {d['about']} (chased {d['sent']} time(s) so far)" for d in due]
    if capped:
        lines += ["", f"Already chased {cap} time(s), so the agent will not chase again:"]
        lines += [f"  - {c['person']}: {c['about']}" for c in capped]
        lines += ["  These need a person, not another reminder."]
    if not due and not capped:
        return JobResult("reminders", ran=True, found=0, summary="Nothing is outstanding to chase.")
    return JobResult(
        "reminders", ran=True, found=len(due),
        summary=f"{len(due)} item(s) could be chased, {len(capped)} are at the cap.",
        detail="\n".join(lines), items=due + capped,
    )


def interview_horizon(conn: sqlite3.Connection, cfg: ResolvedConfig, *, on: date) -> JobResult:
    """Interviews coming up, and anything about them a person needs to fix.

    An interview is the one piece of HR work with an outside party who will
    simply turn up. The expensive failures are quiet ones -- no address for the
    candidate, an interviewer who has since left, a panel below the company's
    own minimum -- and all three are invisible until the morning it matters.

    Like every job here it reports and closes nothing. It does not chase the
    recruiter, it does not email the candidate, and it does not quietly add
    somebody to a panel to make the number up.
    """
    upcoming = interviews_mod.horizon(conn, cfg, on=on, days=INTERVIEW_WINDOW_DAYS)
    if not upcoming:
        return JobResult("interview_horizon", ran=True, found=0,
                         summary=f"No interviews are booked in the next {INTERVIEW_WINDOW_DAYS} days.")

    needing = [i for i in upcoming if i["concerns"]]
    lines = []
    for entry in upcoming:
        when = f"{entry['on_date']} {entry['start_time']}"
        who = entry["candidate_name"] or "candidate not recorded"
        head = f"  - {when}: {entry['stage_label'] or 'Interview'}, {who}"
        if entry["role_title"]:
            head += f" ({entry['role_title']})"
        head += f", in {entry['days_away']} day(s)"
        lines.append(head)
        lines += [f"      needs a person: {c}" for c in entry["concerns"]]

    return JobResult(
        "interview_horizon", ran=True, found=len(upcoming),
        summary=f"{len(upcoming)} interview(s) booked in the next {INTERVIEW_WINDOW_DAYS} days"
                + (f", {len(needing)} with something outstanding" if needing else ", none with anything outstanding")
                + ".",
        detail="\n".join(lines),
        items=[
            {
                "interview_id": i["id"], "on_date": i["on_date"], "start_time": i["start_time"],
                "stage": i["stage"], "stage_label": i["stage_label"], "role_title": i["role_title"],
                "candidate": i["candidate_name"], "days_away": i["days_away"],
                "concerns": i["concerns"],
            }
            for i in upcoming
        ],
    )


RUNNERS = {
    "expiry_scan": expiry_scan,
    "readiness": readiness_scan,
    "policy_review": policy_review,
    "reminders": reminders,
    "interview_horizon": interview_horizon,
}


# ---------------------------------------------------------------- running


def run_job(conn: sqlite3.Connection, cfg: ResolvedConfig, job: str, *, on: date | None = None) -> JobResult:
    """Run one job. Anything it found goes through the kernel like any request."""
    if job not in RUNNERS:
        raise ValueError(f"unknown job {job!r}; expected one of {', '.join(JOBS)}")
    today = on or date.today()
    result = RUNNERS[job](conn, cfg, on=today)
    if result.ran and result.found:
        outcome = kernel.run_job(
            conn, cfg, job_kind=job, summary=result.summary, detail=result.detail, on=today
        )
        result.work_item_id = outcome.work_item_id
    return result


def run_all(conn: sqlite3.Connection, cfg: ResolvedConfig, *, on: date | None = None) -> list[JobResult]:
    """Every job, in order. A quiet job is still reported, as having been quiet."""
    return [run_job(conn, cfg, job, on=on) for job in JOBS]


def record_reminder(conn: sqlite3.Connection, cfg: ResolvedConfig, *, subject_id: str, about: str) -> int:
    """Count a reminder that a person approved. Returns the new count."""
    conn.execute(
        "INSERT INTO reminder (tenant_id, subject_id, about, sent_count, last_sent) VALUES (?,?,?,1,?) "
        "ON CONFLICT(tenant_id, subject_id, about) DO UPDATE SET "
        "sent_count = sent_count + 1, last_sent = excluded.last_sent",
        (cfg.tenant_id, subject_id, about, db.now()),
    )
    return db.one(
        conn, "SELECT sent_count FROM reminder WHERE tenant_id = ? AND subject_id = ? AND about = ?",
        (cfg.tenant_id, subject_id, about),
    )["sent_count"]
