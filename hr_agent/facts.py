"""Facts: what is true about a request, taken from systems rather than from the text.

The rule engine decides from these. That is why injected instructions in a
document cannot change what the agent is allowed to do: the text can influence
wording, but the facts come from the directory, the case table and a read-only
policy probe.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from typing import Any

from . import db, forms, interviews as interviews_mod, policy
from .classify import Classification
from .packs.models import Person, ResolvedConfig


def person_by_id(cfg: ResolvedConfig, person_id: str | None) -> Person | None:
    if not person_id:
        return None
    return next((p for p in cfg.people if p.id == person_id), None)


def subject_of(cfg: ResolvedConfig, classification: Classification, requester: Person | None) -> Person | None:
    """Whose record the request is about.

    A request names somebody else, or it is about the requester. The directory is
    the authority on who those names refer to.
    """
    named = classification.entities.get("people") or []
    for person_id in named:
        if candidate := person_by_id(cfg, person_id):
            return candidate
    return requester


def manages(cfg: ResolvedConfig, manager: Person | None, report: Person | None) -> bool:
    """Direct reports only. A skip-level manager is not automatically entitled."""
    if not manager or not report or manager.id == report.id:
        return False
    return report.manager_id == manager.id


def has_hr_role(cfg: ResolvedConfig, person: Person | None) -> bool:
    """Whether this company treats one of the person's roles as HR."""
    if not person:
        return False
    return bool(set(person.roles) & set(cfg.approvals.hr_roles))


def _owned_fields(cfg: ResolvedConfig) -> set[str]:
    return {f for adapter in cfg.adapters for f in adapter.owns_fields}


def case_kind_for(cfg: ResolvedConfig, classification: Classification) -> str | None:
    """Which case a request belongs to, if any.

    Derived from the RULES rather than from the case list, because the rules are
    what actually open cases -- so the two cannot disagree. Several case types
    can name the same intent (a sensitive matter and a works-council
    consultation are both enquiries), and matching on that would put an ordinary
    leave question into whichever happened to be declared first.

    A plain enquiry belongs to no case, because no rule opens one for it. That
    is the correct answer, not a gap: there is nothing for a take-over to apply
    to until a person opens something.
    """
    if classification.sensitivity == "restricted":
        return next(
            (r.opens_case for r in cfg.rules if r.opens_case and r.when.get("sensitivity") == "restricted"),
            None,
        )
    return next(
        (
            r.opens_case
            for r in cfg.rules
            # The catch-all opens a case too, but it matches everything, so it
            # must not be treated as this intent's case.
            if r.opens_case and r.when and r.when.get("intent") == classification.intent
        ),
        None,
    )


def open_case(
    conn: sqlite3.Connection, tenant_id: str, subject_id: str | None, kind: str | None
) -> sqlite3.Row | None:
    """The open case this request belongs to.

    Scoped to the KIND as well as the person. Without the kind, a grievance a
    person had taken over would silently stop the agent answering that
    employee's unrelated questions, for ever -- which is the opposite of what
    taking over a matter is supposed to mean.
    """
    if not subject_id or not kind:
        return None
    return db.one(
        conn,
        "SELECT * FROM hr_case WHERE tenant_id = ? AND subject_id = ? AND kind = ? AND status = 'open' "
        "ORDER BY opened_at DESC LIMIT 1",
        (tenant_id, subject_id, kind),
    )


def gather(
    conn: sqlite3.Connection,
    cfg: ResolvedConfig,
    *,
    classification: Classification,
    requester_id: str | None,
    probe: policy.Probe | None = None,
    interview: interviews_mod.Probe | None = None,
    attachment: dict[str, Any] | None = None,
    job_kind: str | None = None,
    on: date | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (facts, context).

    `facts` is exactly what the rule engine may test, and nothing else.
    `context` is everything else the later stages need, kept separate so it is
    obvious that it played no part in the decision.
    """
    today = on or datetime.now(UTC).date()
    requester = person_by_id(cfg, requester_id)
    subject = subject_of(cfg, classification, requester)

    owned = _owned_fields(cfg)
    wanted_fields: list[str] = list(classification.entities.get("fields") or [])
    unmapped: list[str] = list(classification.entities.get("unmapped_fields") or [])

    this_kind = case_kind_for(cfg, classification)
    current_case = open_case(conn, cfg.tenant_id, subject.id if subject else None, this_kind)
    taken_over = bool(current_case["taken_over"]) if current_case else False
    exit_case = open_case(conn, cfg.tenant_id, subject.id if subject else None, "exit")

    within_lead = None
    if subject and subject.start_date:
        days = (date.fromisoformat(subject.start_date) - today).days
        within_lead = 0 <= days <= cfg.manifest.sla.onboarding_lead_days

    facts: dict[str, Any] = {
        "intent": classification.intent,
        "sensitivity": classification.sensitivity,
        "has_requester": requester is not None,
        "owns_record": bool(requester and subject and requester.id == subject.id),
        "is_manager_of_subject": manages(cfg, requester, subject),
        "has_hr_role": has_hr_role(cfg, requester),
        "compound": classification.compound,
        "confidence_low": classification.confidence < 0.5,
        "has_open_case": current_case is not None,
        "case_taken_over": taken_over,
        "has_exit_case": exit_case is not None,
        "job_kind": job_kind,
    }

    # Only state a fact the request actually raises. A fact left unset cannot
    # satisfy a predicate, so an unasked question never opens a gate.
    if classification.intent == "enquiry" and probe is not None:
        facts["policy_found"] = probe.found
        facts["policy_conflict"] = probe.conflict
    if classification.intent == "admin":
        facts["writes_owned_field"] = bool(wanted_fields) and all(f in owned for f in wanted_fields)
    if classification.intent == "onboarding":
        facts["within_lead_window"] = within_lead
    if classification.intent == "interview" and interview is not None:
        # Whether a time exists is a fact, found by a read-only look at the
        # calendars before any rule is evaluated. Same shape as the policy probe,
        # and for the same reason.
        facts.update(interview.facts())
    reading = None
    if attachment:
        if attachment.get("text"):
            # Read it against this company's own templates. The classification
            # (known / unknown / restricted) comes from the TYPE, never from
            # what the contents turn out to say.
            reading = forms.read(attachment["text"], cfg)
            if not reading.recognised:
                facts["document_type"] = "unknown"
            elif reading.restricted:
                facts["document_type"] = "restricted"
            else:
                facts["document_type"] = "known"
            facts["document_complete"] = reading.complete
        else:
            facts["document_type"] = attachment.get("document_type", "unknown")
            facts["document_complete"] = attachment.get("complete")
    elif classification.intent == "document":
        # Said there was an attachment; nothing arrived. That is a fact worth
        # stating, not an absence to fall through on.
        facts["document_type"] = "missing"

    context = {
        "requester": requester.id if requester else None,
        "requester_name": requester.name if requester else None,
        "subject": subject.id if subject else None,
        "subject_name": subject.name if subject else None,
        "fields": wanted_fields,
        "values": classification.entities.get("values") or {},
        "unmapped_fields": unmapped,
        "owned_fields": sorted(owned),
        "open_case_id": current_case["id"] if current_case else None,
        "case_kind": this_kind,
        "start_date": subject.start_date if subject else None,
        "days_until_start": (date.fromisoformat(subject.start_date) - today).days
        if subject and subject.start_date
        else None,
        "entities": classification.entities,
        # What actually arrived, as distinct from what the message claimed.
        "document_type": (reading.template.id if reading and reading.template else (attachment or {}).get("document_type")),
        "document_complete": reading.complete if reading else (attachment or {}).get("complete"),
        "reading": reading.as_dict() if reading else None,
        "_reading": reading,
        "interview": interview.as_dict() if interview else None,
        "_interview": interview,
    }
    return facts, context
