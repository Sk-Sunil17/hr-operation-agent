"""The loop. This is the agent.

    receive -> understand -> decide -> retrieve -> draft -> verify -> act -> audit

Three things about the order are deliberate:

  * The payload is persisted before anything else happens, so a crash loses
    nothing and a replay repeats nothing.
  * The rule engine decides the mode, from facts, before any draft exists. No
    model and no document content has any influence over it.
  * The verify gate sits between the draft and every commit. A failure escalates
    to a person. It never degrades into sending.

The policy probe runs during `understand`, before the decision, because
`policy_found` and `policy_conflict` are facts the rules test. The probe is
read-only by construction: it cannot write, send, or change anything.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from . import audit, classify, db, draft, engine, facts as facts_mod, ics, interviews, policy, readiness, tools
from . import verify as verify_mod
from .packs.models import ResolvedConfig
from .policy import Citation
from .redact import Redactor


@dataclass
class Outcome:
    """Everything that happened, in a shape the CLI and the API can both render."""

    work_item_id: str
    status: str                       # answered | queued | handed_over | refused | suppressed
    mode: str
    rule_id: str
    rule_reason: str
    draft: str
    citations: list[Citation] = field(default_factory=list)
    checks: list[dict[str, str]] = field(default_factory=list)
    verify_passed: bool = True
    plans: list[dict[str, Any]] = field(default_factory=list)
    approval_id: str | None = None
    case_id: str | None = None
    decision_id: str | None = None
    classification: dict[str, Any] = field(default_factory=dict)
    facts: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    probe: dict[str, Any] = field(default_factory=dict)
    escalated_because: list[str] = field(default_factory=list)
    children: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "work_item_id": self.work_item_id,
            "status": self.status,
            "mode": self.mode,
            "rule_id": self.rule_id,
            "rule_reason": self.rule_reason,
            "draft": self.draft,
            "citations": [c.as_dict() for c in self.citations],
            "checks": self.checks,
            "verify_passed": self.verify_passed,
            "plans": [
                {k: v for k, v in p.items() if k in {"id", "adapter", "operation", "status", "preview_text", "idempotency_key"}}
                for p in self.plans
            ],
            "approval_id": self.approval_id,
            "case_id": self.case_id,
            "decision_id": self.decision_id,
            "classification": self.classification,
            "facts": self.facts,
            "context": self.context,
            "probe": self.probe,
            "escalated_because": self.escalated_because,
            "children": self.children,
        }


STATUS_BY_MODE = {"auto": "answered", "prepare": "queued", "hand_over": "handed_over", "refuse": "refused"}


def _availability_summary(found: interviews.Probe) -> str:
    """One readable line for the audit trail. The detail goes in the row's JSON."""
    if found.unreachable:
        return f"the calendar did not answer, so nobody's availability could be read ({found.unreachable})"
    if not found.has_calendar:
        return "no calendar is connected, so nobody's availability could be read"
    if found.request.action == "cancel":
        return (
            f"cancellation of {found.existing['id']}" if found.existing
            else f"cancellation matching {len(found.matches)} interview(s)"
        )
    if found.proposal is None:
        return "nothing to search for"
    if found.proposal.found:
        best = found.proposal.best
        return (
            f"{len(found.proposal.slots)} time(s) free across "
            f"{len(found.proposal.searched)} day(s), soonest {best.on.isoformat()} {best.start}"
        )
    return f"no time free across {len(found.proposal.searched)} day(s) searched"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def route_approval(cfg: ResolvedConfig, *, mode: str, intent: str, sensitivity: str) -> tuple[str, str | None]:
    """Who decides. Falls back to the company's default escalation role."""
    candidate = {"mode": mode, "intent": intent, "sensitivity": sensitivity}
    for route in cfg.approvals.matrix:
        if all(engine.test_predicate(expected, candidate.get(key)) for key, expected in route.when.items()):
            return route.role, route.then_role
    return cfg.manifest.escalation.default_role, None


def in_force_map(cfg: ResolvedConfig, *, person, on: date) -> dict[str, str]:
    """doc_id -> the one version quotable today. Used by the verify gate."""
    out: dict[str, str] = {}
    for doc in cfg.policies:
        versions = policy.in_force(doc, on=on, person=person)
        if len(versions) == 1:
            out[doc.id] = versions[0].version
    return out


def run(
    conn: sqlite3.Connection,
    cfg: ResolvedConfig,
    *,
    message: str,
    requester_id: str,
    channel: str = "web",
    attachment: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
    on: date | None = None,
    _parent_id: str | None = None,
) -> Outcome:
    """Run one request end to end."""
    redactor = Redactor(cfg.redaction)
    today = on or date.today()
    tenant = cfg.tenant_id

    def write_audit(stage: str, action: str, *, actor: str = "agent", kind: str = "agent", detail: Any = None, item: str | None = None) -> None:
        audit.append(
            conn, tenant_id=tenant, actor=actor, actor_kind=kind, stage=stage, action=action,
            redactor=redactor, work_item_id=item, detail=detail, config_hash=cfg.config_hash,
        )

    # ---------------------------------------------------------------- receive
    # The payload is stored before anything looks at it, and a replayed delivery
    # is recognised here rather than acted on twice.
    #
    # The key identifies a DELIVERY, not a message. A channel passes the id its
    # provider gave the message (a Slack ts, an email Message-ID, a webhook
    # delivery id) and gets replay protection. With no id supplied, each call is
    # a distinct delivery -- an employee asking the same question twice is two
    # requests, not a replay, and keying on the text would silently refuse the
    # second one.
    key = dedupe_key or f"delivery:{uuid.uuid4().hex}"
    if existing := db.one(conn, "SELECT * FROM event WHERE tenant_id = ? AND dedupe_key = ?", (tenant, key)):
        write_audit("receive", "duplicate delivery suppressed", detail={"dedupe_key": key}, item=existing["work_item_id"])
        return Outcome(
            work_item_id=existing["work_item_id"], status="suppressed", mode="none", rule_id="none",
            rule_reason="This exact message was already received, so nothing was done again.",
            draft="", escalated_because=["duplicate delivery"],
        )

    work_item_id = _new_id("w")
    db.insert(conn, "work_item", {
        "id": work_item_id, "tenant_id": tenant, "parent_id": _parent_id, "channel": channel,
        "requester_id": requester_id, "status": "open", "config_hash": cfg.config_hash, "created_at": db.now(),
    })
    db.insert(conn, "event", {
        "id": _new_id("ev"), "tenant_id": tenant, "work_item_id": work_item_id, "kind": "message",
        "payload_json": json.dumps(redactor.mapping({"message": message, "attachment": attachment}), sort_keys=True),
        "dedupe_key": key, "received_at": db.now(),
    })
    db.insert(conn, "message_log", {
        "id": _new_id("msg"), "tenant_id": tenant, "work_item_id": work_item_id, "direction": "inbound",
        "channel": channel, "body": redactor.text(message), "idempotency_key": None, "suppressed": 0, "at": db.now(),
    })
    write_audit("receive", f"message received on {channel}", detail={"length": len(message)}, item=work_item_id)

    # ---------------------------------------------------------------- understand
    classification = classify.classify(
        message, cfg, requester_id=requester_id, channel=channel,
        has_attachment=attachment is not None,
    )
    write_audit("understand", f"read as {classification.intent}, {classification.sensitivity}",
                detail=classification.as_dict(), item=work_item_id)

    requester = facts_mod.person_by_id(cfg, requester_id)
    subject = facts_mod.subject_of(cfg, classification, requester)

    # A compound message becomes child work items, each with its own trail.
    if classification.compound and _parent_id is None and len(classification.clauses) > 1:
        write_audit("understand", f"split into {len(classification.clauses)} separate requests", item=work_item_id)
        children = [
            run(conn, cfg, message=clause, requester_id=requester_id, channel=channel,
                dedupe_key=f"{key}:{index}", on=today, _parent_id=work_item_id).work_item_id
            for index, clause in enumerate(classification.clauses)
        ]
        conn.execute("UPDATE work_item SET status = 'split', closed_at = ? WHERE id = ?", (db.now(), work_item_id))
        return Outcome(
            work_item_id=work_item_id, status="split", mode="none", rule_id="none",
            rule_reason="One message asked for several separate things, so each is handled on its own.",
            draft="", classification=classification.as_dict(), children=children,
        )

    # ---------------------------------------------------------------- probe (read-only)
    probe = policy.Probe(found=False, conflict=False)
    if classification.intent == "enquiry":
        probe = policy.search(conn, cfg, message, person=subject, on=today)
        write_audit("retrieve", f"policy probe: {'found' if probe.found else 'nothing in force covers this'}",
                    detail=probe.as_dict(), item=work_item_id)

    # The same pattern for scheduling: whether a time exists that satisfies this
    # company's notice, buffer and daily limits is a fact the rules test, so it
    # has to be known before the decision. Free/busy is a read, and the registry
    # refuses to call a write as a read, so this cannot book anything.
    interview_probe = None
    if classification.intent == "interview":
        registry_for_probe = tools.Registry(conn, cfg)
        calendars = registry_for_probe.of_kind("calendar")
        reader = None
        if calendars:
            def reader(**args: Any) -> dict[str, Any]:
                return registry_for_probe.read(calendars[0].name, "free_busy", **args)
        try:
            interview_probe = interviews.probe(
                conn, cfg, message=message, requester=requester,
                named_ids=list(classification.entities.get("people") or []),
                read_free_busy=reader, today=today,
            )
        except Exception as exc:  # noqa: BLE001 - any calendar fault lands here on purpose
            # A probe runs BEFORE the act phase, so it is outside the wrapper
            # that catches adapter faults later on. Letting it escape would end
            # the request with a stack trace, an employee hearing nothing and a
            # trail that stops mid-sentence. A calendar that will not answer is
            # a calendar whose availability cannot be read, which is exactly
            # what rule I0 is for.
            interview_probe = interviews.probe(
                conn, cfg, message=message, requester=requester,
                named_ids=list(classification.entities.get("people") or []),
                read_free_busy=None, today=today,
            )
            interview_probe.unreachable = f"{type(exc).__name__}: {exc}"
        write_audit(
            "retrieve",
            f"availability probe: {_availability_summary(interview_probe)}",
            detail=interview_probe.as_dict(), item=work_item_id,
        )

    # ---------------------------------------------------------------- decide
    the_facts, context = facts_mod.gather(
        conn, cfg, classification=classification, requester_id=requester_id,
        probe=probe, interview=interview_probe, attachment=attachment, on=today,
    )
    segments = engine.segments_for(cfg, subject if subject else requester)
    verdict = engine.evaluate(cfg, the_facts, segment_ids=segments)
    write_audit("decide", f"{verdict.rule_id}: {verdict.mode}", actor=f"rule:{verdict.rule_id}", kind="rule",
                detail={"facts": the_facts, **verdict.as_dict()}, item=work_item_id)

    conn.execute(
        "UPDATE work_item SET intent = ?, sensitivity = ?, mode = ?, rule_id = ? WHERE id = ?",
        (classification.intent, classification.sensitivity, verdict.mode, verdict.rule_id, work_item_id),
    )

    case_id = None
    if verdict.opens_case:
        # Join the matter already open, rather than opening a second one for the
        # same person and kind. Three messages about one grievance are one case.
        existing = facts_mod.open_case(
            conn, tenant, subject.id if subject else None, verdict.opens_case
        )
        case_id = existing["id"] if existing else None
    if verdict.opens_case and case_id is None:
        case_id = _new_id("case")
        db.insert(conn, "hr_case", {
            "id": case_id, "tenant_id": tenant, "kind": verdict.opens_case,
            "subject_id": subject.id if subject else None,
            "owner_role": cfg.manifest.escalation.restricted_role
            if classification.sensitivity == "restricted" else cfg.manifest.escalation.default_role,
            "owner_id": None, "status": "open", "taken_over": 0, "opened_at": db.now(),
        })
    if case_id:
        conn.execute("UPDATE work_item SET case_id = ? WHERE id = ?", (case_id, work_item_id))
    # The draft stage needs the case this work belongs to: a booked interview
    # records it, so the case and the calendar entry can be joined up later.
    context["case_id_opened"] = case_id

    # ---------------------------------------------------------------- draft
    # Only an answering mode carries a citation. A rule may require one and then
    # be tightened into a handover by a segment overlay; the citation goes with
    # the answer, not with the rule.
    citations = (
        policy.best_citations(probe)
        if verdict.needs_citation and probe.found and verdict.mode in verify_mod.ANSWERING_MODES
        else []
    )
    registry = tools.Registry(conn, cfg)

    # The decision is recorded now, while it is being made, not after the fact:
    # prepared actions reference it, and a crash between here and the end must
    # still leave a record of what was decided and why.
    decision_id = _new_id("dec")
    db.insert(conn, "decision", {
        "id": decision_id, "tenant_id": tenant, "work_item_id": work_item_id, "created_at": db.now(),
        "facts_json": json.dumps(the_facts, sort_keys=True), "rule_id": verdict.rule_id, "mode": verdict.mode,
        "needs_citation": int(verdict.needs_citation), "rule_reason": verdict.reason,
        "retrieval_json": json.dumps(probe.as_dict(), sort_keys=True),
        "provider": "template", "model_id": None, "model_params_json": "{}", "prompt_hash": None,
        "pack_hash": cfg.config_hash,
    })
    body, plans = _compose(
        conn, cfg, registry, verdict=verdict, classification=classification, citations=citations,
        context=context, work_item_id=work_item_id, decision_id=decision_id, subject=subject,
        today=today,
    )
    write_audit("draft", f"drafted {len(body.split())} words, {len(citations)} citation(s)",
                detail={"citations": [c.as_dict() for c in citations], "plans": len(plans)}, item=work_item_id)

    # ---------------------------------------------------------------- verify
    result = verify_mod.verify(
        draft=body, mode=verdict.mode, sensitivity=classification.sensitivity,
        needs_citation=verdict.needs_citation, citations=citations, plans=plans,
        redactor=redactor, in_force_versions=in_force_map(cfg, person=subject, on=today),
        # What the person supplied. A value they wrote on the form they sent is
        # as much theirs as one they typed in the message.
        source=message + "\n" + str((attachment or {}).get("text") or ""),
        own_addresses=registry.declared_addresses(),
    )
    write_audit("verify", str(result), detail={"checks": result.as_list()}, item=work_item_id)

    mode = verdict.mode
    escalated: list[str] = []
    if not result.passed:
        # Nothing prepared may survive a failed verification.
        for plan in plans:
            registry.cancel(plan["id"], "verification failed")
        escalated = [c.detail for c in result.failures]
        role = cfg.manifest.escalation.verification_failure_role
        body = draft.handover(cfg, reason="an internal check on the drafted reply did not pass", role=role)
        citations = []
        mode = "hand_over"
        plans = [_notify(conn, cfg, registry, work_item_id, decision_id, body, to=role, tag="verify-failed")]
        write_audit("handover", f"verification failed, escalated to {role}",
                    detail={"failures": escalated}, item=work_item_id)

    # ---------------------------------------------------------------- act
    #
    # Everything from here can touch a real system, so everything from here can
    # fail. A failure must be recorded and handed to a person: an employee who
    # hears nothing because a adapter timed out has been failed silently, and a
    # trail that stops mid-sentence is worse than no trail.
    approval_id = None
    failure: str | None = None
    try:
        if mode in {"auto", "hand_over", "refuse"}:
            for plan in plans:
                if plan["status"] == "prepared":
                    registry.commit(plan["id"], actor="agent", authorised=True)
        else:
            # prepare: the reply is queued, and the requester is told so immediately.
            approval_id = _queue(
                conn, cfg, registry, work_item_id, decision_id, verdict, classification, requester_id
            )
    except Exception as exc:  # noqa: BLE001 - any adapter fault lands here on purpose
        failure = f"{type(exc).__name__}: {exc}"
        role = cfg.manifest.escalation.verification_failure_role
        write_audit(
            "act", f"a system did not respond, handed to {role}",
            detail={"error": failure, "mode": mode}, item=work_item_id,
        )
        for plan in registry.plans_for(work_item_id):
            if plan["status"] == "prepared":
                registry.cancel(plan["id"], "a system did not respond")
        escalated = [*escalated, f"a connected system did not respond ({failure})"]
        mode = "hand_over"
        body = draft.system_failure(cfg, role=role)
        citations = []

    plans = registry.plans_for(work_item_id)
    status = "failed" if failure else ("split" if mode == "none" else STATUS_BY_MODE[mode])
    conn.execute(
        "UPDATE work_item SET status = ?, mode = ?, closed_at = ? WHERE id = ?",
        (status, mode, None if status == "queued" else db.now(), work_item_id),
    )
    write_audit("act" if mode != "hand_over" else "handover", f"{status}: {verdict.rule_id}", item=work_item_id)

    # ---------------------------------------------------------------- record
    conn.execute(
        "UPDATE decision SET mode = ?, draft_text = ?, verify_json = ?, verify_passed = ? WHERE id = ?",
        (mode, body, json.dumps(result.as_list(), sort_keys=True), int(result.passed), decision_id),
    )
    for citation in citations:
        db.insert(conn, "citation", {
            "id": _new_id("cit"), "tenant_id": tenant, "work_item_id": work_item_id, "decision_id": decision_id,
            "doc_id": citation.doc_id, "version": citation.version, "section_id": citation.section_id,
            "quoted_text": citation.quoted_text,
        })

    return Outcome(
        work_item_id=work_item_id, status=status, mode=mode, rule_id=verdict.rule_id,
        rule_reason=verdict.reason, draft=body, citations=citations, checks=result.as_list(),
        verify_passed=result.passed, plans=plans, approval_id=approval_id, case_id=case_id,
        decision_id=decision_id, classification=classification.as_dict(), facts=the_facts,
        context=context, probe=probe.as_dict(), escalated_because=escalated,
    )


def run_job(
    conn: sqlite3.Connection,
    cfg: ResolvedConfig,
    *,
    job_kind: str,
    summary: str,
    detail: str,
    subject_id: str | None = None,
    on: date | None = None,
) -> Outcome:
    """A scheduled job, through the same pipeline as a message.

    A job is not a message, so there is nothing to classify -- but it still goes
    through the rule engine, the verify gate and the audit chain, because a
    scheduled thing that bypasses the guardrails is exactly the thing that will
    quietly do something nobody approved at three in the morning.
    """
    redactor = Redactor(cfg.redaction)
    today = on or date.today()
    tenant = cfg.tenant_id

    def write_audit(stage: str, action: str, *, actor: str = "scheduler", kind: str = "scheduler",
                    detail_obj: Any = None, item: str | None = None) -> None:
        audit.append(conn, tenant_id=tenant, actor=actor, actor_kind=kind, stage=stage, action=action,
                     redactor=redactor, work_item_id=item, detail=detail_obj, config_hash=cfg.config_hash)

    # A cron that fires twice is not two findings. The same job, for the same
    # subject, on the same day, is one piece of work.
    key = f"job:{job_kind}:{subject_id or '-'}:{today.isoformat()}"
    if existing := db.one(conn, "SELECT * FROM event WHERE tenant_id = ? AND dedupe_key = ?", (tenant, key)):
        write_audit("receive", f"{job_kind} already ran today, nothing repeated",
                    detail_obj={"dedupe_key": key}, item=existing["work_item_id"])
        return Outcome(
            work_item_id=existing["work_item_id"], status="suppressed", mode="none", rule_id="none",
            rule_reason="This job already ran today, so it was not run again.",
            draft="", escalated_because=["duplicate scheduled run"],
        )

    work_item_id = _new_id("w")
    db.insert(conn, "work_item", {
        "id": work_item_id, "tenant_id": tenant, "parent_id": None, "channel": "scheduler",
        "requester_id": None, "subject_id": subject_id, "intent": "job", "sensitivity": "routine",
        "status": "open", "config_hash": cfg.config_hash, "created_at": db.now(),
    })
    db.insert(conn, "event", {
        "id": _new_id("ev"), "tenant_id": tenant, "work_item_id": work_item_id, "kind": "job",
        "payload_json": json.dumps({"job": job_kind, "summary": summary}, sort_keys=True),
        "dedupe_key": key, "received_at": db.now(),
    })
    write_audit("receive", f"scheduled job {job_kind}", item=work_item_id)

    classification = classify.Classification(intent="job", sensitivity="routine", confidence=1.0)
    the_facts, context = facts_mod.gather(
        conn, cfg, classification=classification, requester_id=subject_id, job_kind=job_kind, on=today
    )
    verdict = engine.evaluate(cfg, the_facts)
    write_audit("decide", f"{verdict.rule_id}: {verdict.mode}", actor=f"rule:{verdict.rule_id}",
                kind="rule", detail_obj={"facts": the_facts, **verdict.as_dict()}, item=work_item_id)

    conn.execute(
        "UPDATE work_item SET mode = ?, rule_id = ? WHERE id = ?",
        (verdict.mode, verdict.rule_id, work_item_id),
    )

    registry = tools.Registry(conn, cfg)
    decision_id = _new_id("dec")
    db.insert(conn, "decision", {
        "id": decision_id, "tenant_id": tenant, "work_item_id": work_item_id, "created_at": db.now(),
        "facts_json": json.dumps(the_facts, sort_keys=True), "rule_id": verdict.rule_id,
        "mode": verdict.mode, "needs_citation": int(verdict.needs_citation),
        "rule_reason": verdict.reason, "retrieval_json": "{}", "provider": "template",
        "model_id": None, "model_params_json": "{}", "prompt_hash": None, "pack_hash": cfg.config_hash,
    })

    body = draft.job_report(cfg, job=job_kind, summary=summary, detail=detail)
    plans = [_notify(conn, cfg, registry, work_item_id, decision_id, body,
                     to=cfg.manifest.escalation.default_role, tag=f"job-{job_kind}")]
    write_audit("draft", f"{job_kind}: {summary}", item=work_item_id)

    result = verify_mod.verify(
        draft=body, mode=verdict.mode, sensitivity="routine", needs_citation=verdict.needs_citation,
        citations=[], plans=plans, redactor=redactor, source=detail,
        own_addresses=registry.declared_addresses(),
    )
    write_audit("verify", str(result), detail_obj={"checks": result.as_list()}, item=work_item_id)

    approval_id = None
    if result.passed and verdict.mode == "prepare":
        approval_id = _queue(conn, cfg, registry, work_item_id, decision_id, verdict, classification, None)
    elif result.passed:
        for plan in plans:
            if plan["status"] == "prepared":
                registry.commit(plan["id"], actor="scheduler", authorised=True)

    status = STATUS_BY_MODE.get(verdict.mode, "queued") if result.passed else "failed"
    conn.execute("UPDATE work_item SET status = ?, mode = ?, closed_at = ? WHERE id = ?",
                 (status, verdict.mode, None if status == "queued" else db.now(), work_item_id))
    conn.execute("UPDATE decision SET draft_text = ?, verify_json = ?, verify_passed = ? WHERE id = ?",
                 (body, json.dumps(result.as_list(), sort_keys=True), int(result.passed), decision_id))
    write_audit("act", f"{job_kind}: {status}", item=work_item_id)

    return Outcome(
        work_item_id=work_item_id, status=status, mode=verdict.mode, rule_id=verdict.rule_id,
        rule_reason=verdict.reason, draft=body, checks=result.as_list(),
        verify_passed=result.passed, plans=registry.plans_for(work_item_id),
        approval_id=approval_id, decision_id=decision_id, facts=the_facts, context=context,
    )


# ------------------------------------------------------------------ composing


def _add_months(start: date, months: int) -> date:
    """Calendar months, clamped to the end of a short month."""
    month = start.month - 1 + months
    year = start.year + month // 12
    month = month % 12 + 1
    last = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, min(start.day, last))


def _notify(conn, cfg, registry, work_item_id, decision_id, body, *, to=None, tag="reply") -> dict[str, Any]:
    """Prepare an outbound message. Nothing is sent until a commit."""
    notifier = registry.notifier()
    return registry.prepare(
        work_item_id=work_item_id, decision_id=decision_id, adapter=notifier.name,
        operation="prepare_message", idempotency_key=tools.idempotency_key(work_item_id, tag),
        to=to, body=body,
    )


def _mail(
    conn, cfg, registry, work_item_id, decision_id, *, to, subject, body, ics_text=None, tag="mail"
) -> dict[str, Any]:
    """Prepare an email to somebody outside the company.

    Separate from `_notify` because the destination is. An internal notifier is
    a channel the company controls; this is an address that arrived in a message,
    and it is written out in full in the preview an approver reads. Raises if no
    configured system can reach outside at all, which the caller turns into a
    stated gap rather than a silent omission.
    """
    mailer = registry.notifier(external=True)
    return registry.prepare(
        work_item_id=work_item_id, decision_id=decision_id, adapter=mailer.name,
        operation="prepare_message", idempotency_key=tools.idempotency_key(work_item_id, tag),
        to=to, subject=subject, body=body, ics=ics_text,
    )


def _interview(
    conn, cfg, registry, *, work_item_id, decision_id, found, case_id=None
) -> tuple[str, list[dict[str, Any]]]:
    """Turn an availability probe into a proposal and the actions behind it.

    Everything here PREPARES. The calendar entry, the candidate's email and the
    panel's brief are three separate plans under three separate keys, and all
    three commit together when one person approves -- which is the point. An
    interview that lands in a calendar but whose candidate is never told is
    worse than one that was never arranged, and splitting the approval would
    make that outcome reachable.
    """
    if found is None or not found.has_calendar:
        return draft.needs_a_person(
            cfg, what="arranging this",
            reason=(
                "the calendar system did not answer, so I could not see when anybody is free"
                if found is not None and found.unreachable
                else "no calendar system is connected here"
            ),
        ), []

    request = found.request
    plans: list[dict[str, Any]] = []
    people = {p.id: p for p in cfg.people}
    panel_names = [people[p].name for p in request.panel_ids if p in people]
    calendar = registry.of_kind("calendar")[0]

    # ---------------------------------------------------------- calling one off
    if request.action == "cancel":
        if found.existing is None:
            if found.matches:
                return draft.interview_ambiguous(cfg, action="cancel", matches=found.matches), []
            return draft.interview_none_found(cfg, action="cancel"), []

        interview = found.existing
        reason = "cancelled by the people team"
        actions: list[str] = []
        if calendar.supports("prepare_cancel"):
            plan = registry.prepare(
                work_item_id=work_item_id, decision_id=decision_id, adapter=calendar.name,
                operation="prepare_cancel",
                idempotency_key=tools.idempotency_key(work_item_id, "cancel", interview["id"]),
                interview_id=interview["id"], date=interview["on_date"], start=interview["start_time"],
                stage_label=interview["stage_label"], reason=reason,
            )
            plans.append(plan)
            actions.append(
                f"take the {interview['on_date']} {interview['start_time']} entry out of "
                f"{calendar.name} and withdraw it from everyone who was invited"
            )

        if interview.get("candidate_email") and cfg.interviews.notify_candidate:
            subject_line, mail_body = draft.interview_cancellation_mail(
                cfg, interview=interview, reason=None
            )
            withdrawal = ics.invitation(
                uid=f"{interview['id']}@{cfg.tenant_id}",
                summary=subject_line,
                on=interview["on_date"], start=interview["start_time"], end=interview["end_time"],
                timezone=interview.get("timezone") or cfg.locale.timezone,
                # A revision, not a new entry: the candidate's calendar must
                # recognise this as the same event being withdrawn.
                sequence=1, cancelled=True,
            )
            plan = _mail(
                conn, cfg, registry, work_item_id, decision_id,
                to=interview["candidate_email"], subject=subject_line, body=mail_body,
                ics_text=withdrawal, tag=f"cancel-mail:{interview['id']}",
            )
            plans.append(plan)
            actions.append(
                "email the candidate at the address on file to say it is off, with a "
                "calendar cancellation so the entry leaves their calendar too"
            )

        return draft.interview_cancel_proposal(
            cfg, interview=interview, reason=None, actions=actions
        ), plans

    # ---------------------------------------------------------- moving one
    if request.action == "reschedule" and found.existing is None:
        if found.matches:
            return draft.interview_ambiguous(cfg, action="reschedule", matches=found.matches), []
        return draft.interview_none_found(cfg, action="reschedule"), []

    # ---------------------------------------------------------- arranging one
    proposal = found.proposal
    if proposal is None or not proposal.found:
        return draft.interview_no_time(
            cfg, request=request, proposal=proposal, panel_names=panel_names
        ), []

    slot = proposal.best
    attendees = interviews.attendee_list(cfg, request=request, organiser_id=request.organiser_id)
    interview_id = interviews.new_id()
    superseded = found.existing["id"] if request.action == "reschedule" and found.existing else None

    invite = registry.prepare(
        work_item_id=work_item_id, decision_id=decision_id, adapter=calendar.name,
        operation="prepare_invite",
        # Keyed on the effect, which is this interview at this time. A replayed
        # request produces the same key and therefore the same single booking.
        idempotency_key=tools.idempotency_key(work_item_id, "invite", slot.label),
        interview_id=interview_id, case_id=case_id,
        # person_ids is kept alongside the richer attendee list: an adapter that
        # only understands "who is coming" still works.
        person_ids=list(request.panel_ids), attendees=attendees,
        date=slot.on.isoformat(), start=slot.start, end=slot.end, minutes=request.minutes,
        timezone=cfg.locale.timezone, location=request.location,
        stage=request.stage.id if request.stage else None, stage_label=request.stage_label,
        role_title=request.role_title, supersedes=superseded,
    )
    plans.append(invite)
    actions = [
        f"put the {request.minutes}-minute {request.stage_label.lower()} in {calendar.name} on "
        f"{slot.on.isoformat()} at {slot.start}, and invite "
        + (f"{len(attendees)} people" if len(attendees) != 1 else "1 person")
    ]

    invitation = ics.invitation(
        uid=f"{interview_id}@{cfg.tenant_id}",
        summary=request.stage_label + (f" — {request.role_title}" if request.role_title else ""),
        on=slot.on, start=slot.start, end=slot.end, timezone=cfg.locale.timezone,
        location=request.location,
        attendees=[
            ics.Attendee(email=a["email"], name=a["name"]) for a in attendees if a.get("email")
        ],
        sequence=1 if superseded else 0,
    )

    # The candidate. The only message here addressed outside the company, and the
    # only address that came from the text rather than the directory.
    if cfg.interviews.notify_candidate and request.reachable:
        subject_line, mail_body = draft.interview_candidate_mail(
            cfg, request=request, slot=slot, panel_names=panel_names, location=request.location
        )
        try:
            plan = _mail(
                conn, cfg, registry, work_item_id, decision_id,
                to=request.candidate_email, subject=subject_line, body=mail_body,
                ics_text=invitation, tag=f"candidate-mail:{slot.label}",
            )
            plans.append(plan)
            whence = (
                "at the address the message gave" if request.email_source == "message"
                else "at the address held against the interview"
            )
            actions.append(
                f"email the candidate via {plan['adapter']}, {whence}, with the calendar "
                "invitation attached"
            )
        except tools.ToolError as exc:
            # No system here can reach somebody outside the company. Saying so is
            # the honest outcome; preparing an internal message that claims to
            # have told the candidate is not.
            request.extra_gaps.append(str(exc))

    # The panel. A calendar invitation says when to turn up; this says who they
    # are meeting and what is still open, which is the half that makes the hour
    # worth having.
    if cfg.interviews.notify_panel and panel_names:
        subject_line, brief = draft.interview_panel_brief(
            cfg, request=request, slot=slot, panel_names=panel_names, location=request.location
        )
        plans.append(
            _notify(
                conn, cfg, registry, work_item_id, decision_id, f"{subject_line}\n\n{brief}",
                to=", ".join(panel_names), tag=f"panel-brief:{slot.label}",
            )
        )
        actions.append(f"send the panel a brief: {', '.join(panel_names)}")

    if superseded:
        request.notes.append(
            f"This replaces interview {superseded}, which approving this will withdraw."
        )

    return draft.interview_proposal(
        cfg, request=request, proposal=proposal, panel_names=panel_names, actions=actions
    ), plans


def _compose(
    conn, cfg, registry, *, verdict, classification, citations, context, work_item_id, decision_id,
    subject, today: date
) -> tuple[str, list[dict[str, Any]]]:
    """Build the reply and prepare whatever it would do. Commits nothing."""
    intent = classification.intent
    plans: list[dict[str, Any]] = []

    if classification.sensitivity == "restricted":
        body = draft.acknowledge_restricted(cfg, requester_name=context.get("requester_name"))
        plans.append(_notify(conn, cfg, registry, work_item_id, decision_id, body, tag="ack"))
        plans.append(_notify(conn, cfg, registry, work_item_id, decision_id,
                             f"A restricted matter has been raised and needs an owner. Work item {work_item_id}.",
                             to=cfg.manifest.escalation.restricted_role, tag="escalate"))
        return body, plans

    if verdict.mode == "refuse":
        body = draft.refusal(cfg, reason=verdict.reason, alternative=draft.alternative_for(verdict.rule_id, cfg))
        plans.append(_notify(conn, cfg, registry, work_item_id, decision_id, body, tag="refusal"))
        return body, plans

    if verdict.mode == "hand_over":
        role = cfg.manifest.escalation.default_role
        body = draft.handover(cfg, reason=verdict.reason, role=role)
        plans.append(_notify(conn, cfg, registry, work_item_id, decision_id, body, tag="handover"))
        plans.append(_notify(conn, cfg, registry, work_item_id, decision_id,
                             f"Handed over: {verdict.reason} Work item {work_item_id}.", to=role, tag="escalate"))
        return body, plans

    # auto or prepare
    if classification.confidence < 0.5 and classification.sensitivity != "restricted":
        # Too unclear to act on. Propose the question rather than shrugging:
        # a person can approve it in one click, and the agent still asks nothing
        # on its own.
        body = draft.clarifying_question(
            cfg, message=classification.clauses[0] if classification.clauses else "",
            topics=list(classification.entities.get("topics") or []),
        )
    elif intent == "enquiry" and citations:
        body = draft.answer(cfg, citations, personal=classification.sensitivity == "personal")
    elif intent == "admin" and context.get("fields"):
        previews, unstated = [], []
        stated = context.get("values") or {}
        for field_name in context["fields"]:
            owner = registry.owning(field_name)
            if owner is None:
                continue
            value = stated.get(field_name)
            if value is None:
                # Do not invent one. An approver shown a placeholder that looks
                # like data may not notice it is not data.
                unstated.append(field_name)
                continue
            plan = registry.prepare(
                work_item_id=work_item_id, decision_id=decision_id, adapter=owner.name,
                operation="prepare_record_change",
                idempotency_key=tools.idempotency_key(work_item_id, owner.name, field_name),
                person_id=(subject.id if subject else context.get("requester")),
                changes={field_name: value},
            )
            plans.append(plan)
            previews.append(plan["preview_text"])
        body = draft.prepared_change(cfg, previews, unstated=unstated)
    elif intent == "onboarding":
        report = readiness.check(conn, cfg, person_id=(subject.id if subject else context["requester"]), registry=registry)
        if not report.configured:
            # No checklist means nothing can be reported. Saying so is the only
            # honest outcome; inventing a clean bill of health is not.
            body = draft.handover(
                cfg,
                reason="this company has not supplied a new starter checklist, so there is nothing to check against",
                role=cfg.manifest.escalation.default_role,
            )
        else:
            body = draft.readiness(
                cfg,
                name=context.get("subject_name") or "The new starter",
                days=context.get("days_until_start") or 0,
                report=report,
                citations=citations,
            )
    elif intent == "interview":
        body, scheduling_plans = _interview(
            conn, cfg, registry, work_item_id=work_item_id, decision_id=decision_id,
            found=context.get("_interview"), case_id=context.get("case_id_opened"),
        )
        plans.extend(scheduling_plans)

    elif intent == "document":
        docstores = registry.of_kind("docstore")
        reading = context.get("_reading")
        kind = context.get("document_type") or "an unidentified document"
        if not docstores:
            body = draft.needs_a_person(cfg, what="filing this", reason="no document store is connected")
        elif reading is not None and reading.recognised and not reading.complete:
            # Incomplete: say exactly what is missing. Nothing is filed, and
            # nobody is chased -- a person decides whether to.
            body = draft.incomplete_form(cfg, reading=reading, who=context.get("subject_name"))
        else:
            expires = None
            if reading is not None and reading.template and reading.template.expires_after_months:
                months = reading.template.expires_after_months
                expires = _add_months(today, months).isoformat()
            plan = registry.prepare(
                work_item_id=work_item_id, decision_id=decision_id, adapter=docstores[0].name,
                operation="prepare_file", idempotency_key=tools.idempotency_key(work_item_id, "file"),
                person_id=(subject.id if subject else context.get("requester")), document_type=kind,
                expires_at=expires,
                template_version=(reading.template.version if reading and reading.template else None),
            )
            plans.append(plan)
            body = draft.complete_form(
                cfg, reading=reading, preview=plan["preview_text"], document_type=kind,
                sensitive={k for k in cfg.redaction.sensitive_keys},
            )

    elif intent == "offboarding":
        person_id = subject.id if subject else context.get("requester")
        filed = {
            row["document_type"]
            for row in db.rows(
                conn, "SELECT document_type FROM filed_document WHERE tenant_id = ? AND person_id = ?",
                (cfg.tenant_id, person_id),
            )
        }
        done = [i for i in cfg.exit_checklist if i.document and i.document in filed]
        outstanding = [i for i in cfg.exit_checklist if i.document and i.document not in filed]
        manual = [i for i in cfg.exit_checklist if not i.document]
        body = draft.exit_checklist(
            cfg, name=context.get("subject_name") or "the leaver",
            done=done, outstanding=outstanding, manual=manual,
        )

    elif intent == "status":
        body = (
            "Here is where your request stands. It was received, classified, and "
            "checked against this company's rules. Nothing has been changed in any "
            "system. The people team can tell you more if you need it."
        )
    else:
        # A mode that prepares must not be worded as a handover: the approver
        # would open a queue item whose text says somebody else already has it.
        body = draft.needs_a_person(cfg, what="this request", reason=verdict.reason)

    plans.append(_notify(conn, cfg, registry, work_item_id, decision_id, body, tag="reply"))
    return body, plans


def _queue(conn, cfg, registry, work_item_id, decision_id, verdict, classification, requester_id) -> str:
    """Create the approval, and tell the requester it is waiting on a person."""
    role, then_role = route_approval(
        cfg, mode=verdict.mode, intent=classification.intent, sensitivity=classification.sensitivity
    )
    approval_id = _new_id("appr")
    db.insert(conn, "approval", {
        "id": approval_id, "tenant_id": cfg.tenant_id, "work_item_id": work_item_id, "decision_id": decision_id,
        "assigned_role": role, "assigned_to": None, "requested_by": requester_id, "status": "open",
        "opened_at": db.now(),
    })
    ack = draft.acknowledge_queued(cfg)
    plan = _notify(conn, cfg, registry, work_item_id, decision_id, ack, tag="queued-ack")
    registry.commit(plan["id"], actor="agent", authorised=True)
    return approval_id
