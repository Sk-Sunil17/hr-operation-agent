"""The JSON API. The dashboard is a separate client and talks only to this.

Everything a screen needs is here, and nothing else. Three rules:

  * Every route is scoped to one tenant, taken from the request, and no query
    omits it. There is no route that can return two companies' rows.
  * The signed-in person is chosen from the company's own directory. This is a
    demo convenience, so the server REFUSES to bind to a public interface while
    it is on -- an unauthenticated role switcher on a public port is not a demo,
    it is an incident.
  * The API never decides anything. It calls the kernel, the rule engine and the
    approvals module, which hold every rule.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import approvals, audit, auth, classify, config, db, engine, facts as facts_mod
from . import interviews, kernel, metrics, policy, redact, scheduler, tools
from .packs import loader
from .packs.models import ResolvedConfig
from .packs.validate import validate as validate_pack

# Role switching is the demo convenience. It is on only while authentication is
# off, and the server refuses a public bind in that state.
DEMO_ROLE_SWITCHING = not auth.enabled()


def create_app(*, db_path: str | None = None) -> FastAPI:
    app = FastAPI(title="HR Operations Agent", version="0.1.0", docs_url="/api/docs")

    # The dashboard is served by Vite in development, on another port.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    cache: dict[str, ResolvedConfig] = {}

    def who(tenant: str, actor: str | None, authorization: str | None) -> str | None:
        """Which person this request is acting as.

        With authentication on, the only answer is the signed-in person: a
        query parameter must never be able to choose. With it off, the demo
        role switcher decides, and the server will not listen on anything but
        localhost.
        """
        if not auth.enabled():
            return actor
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "sign in first")
        try:
            session = auth.read(authorization.split(" ", 1)[1].strip())
        except auth.AuthError as exc:
            raise HTTPException(401, str(exc)) from exc
        if session.tenant_id != tenant:
            raise HTTPException(403, "that session belongs to another company")
        return session.person_id

    def connect() -> sqlite3.Connection:
        return db.open_db(db_path)

    def tenant_config(tenant: str) -> ResolvedConfig:
        if tenant not in cache:
            if tenant not in loader.tenant_ids():
                raise HTTPException(404, f"no such company: {tenant}")
            cache[tenant] = loader.load_resolved(tenant)
        return cache[tenant]

    def person_or_404(cfg: ResolvedConfig, person_id: str):
        person = facts_mod.person_by_id(cfg, person_id)
        if person is None:
            raise HTTPException(404, f"no such person in {cfg.tenant_id}: {person_id}")
        return person

    # ---------------------------------------------------------------- shell

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        """What is switched on. The dashboard shows this, so it cannot be hidden."""
        return {
            "ok": True,
            "guardrails": {
                "redaction": config.REDACTION_ENABLED,
                "rule_engine_decides_mode": True,
                "verify_gate": True,
                "audit_chained": True,
                "model_provider": "none: templates and retrieval only",
                "trains_on_tenant_data": False,
            },
            "auth": {"enabled": auth.enabled()},
            "demo": {
                "role_switching": DEMO_ROLE_SWITCHING,
                "note": "Anyone can act as anyone from the directory. That is a demo "
                        "convenience and the server refuses a public bind while it is on.",
            },
            "tenants": loader.tenant_ids(),
        }

    @app.post("/api/auth/login")
    def login(tenant: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        if not auth.enabled():
            raise HTTPException(400, "authentication is off; this build uses the demo role switcher")
        tenant_config(tenant)
        with connect() as conn:
            try:
                token = auth.sign_in(
                    conn, tenant_id=tenant,
                    email=str(body.get("email", "")), password=str(body.get("password", "")),
                )
            except auth.AuthError as exc:
                return JSONResponse(status_code=401, content={"error": str(exc)})
            return {"token": token, "expires_in": auth.SESSION_SECONDS}

    @app.get("/api/auth/me")
    def me(tenant: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        cfg = tenant_config(tenant)
        actor = who(tenant, None, authorization) if auth.enabled() else None
        if actor is None:
            return {"authenticated": False, "role_switching": DEMO_ROLE_SWITCHING}
        person = person_or_404(cfg, actor)
        return {
            "authenticated": True, "id": person.id, "name": person.name, "roles": person.roles,
            "role_labels": [cfg.approvals.roles.get(r, r) for r in person.roles],
            "role_switching": False,
        }

    @app.get("/api/tenants")
    def tenants() -> dict[str, Any]:
        out = []
        for tenant_id in loader.tenant_ids():
            cfg = tenant_config(tenant_id)
            problems = validate_pack(cfg)
            out.append({
                "id": tenant_id,
                "name": cfg.manifest.name,
                "config_hash": cfg.config_hash,
                "people": len(cfg.people),
                "policies": len(cfg.policies),
                "segments": len(cfg.segments),
                "tone": cfg.manifest.tone,
                "timezone": cfg.locale.timezone,
                "errors": sum(1 for p in problems if p.severity == "error"),
                "warnings": sum(1 for p in problems if p.severity == "warning"),
            })
        return {"items": out}

    @app.get("/api/people")
    def people(tenant: str) -> dict[str, Any]:
        cfg = tenant_config(tenant)
        return {
            # Which role decisions route to here. The dashboard uses it to pick
            # a sensible person to act as: landing on somebody who holds an HR
            # role but is not in any approval route shows an empty queue beside
            # a list of six things, which reads as a fault rather than as the
            # access rule it is.
            "default_role": cfg.manifest.escalation.default_role,
            "items": [
            {
                "id": p.id, "name": p.name, "roles": p.roles, "entity": p.entity,
                "employment_type": p.employment_type, "manager_id": p.manager_id,
                "segments": engine.segments_for(cfg, p),
                "role_labels": [cfg.approvals.roles.get(r, r) for r in p.roles],
                "is_hr": bool(set(p.roles) & set(cfg.approvals.hr_roles)),
            }
            for p in cfg.people if p.active
        ],
        }

    @app.get("/api/config")
    def configuration(tenant: str) -> dict[str, Any]:
        cfg = tenant_config(tenant)
        problems = validate_pack(cfg)
        return {
            "tenant_id": cfg.tenant_id,
            "name": cfg.manifest.name,
            "config_hash": cfg.config_hash,
            "manifest": cfg.manifest.model_dump(mode="json"),
            "locale": cfg.locale.model_dump(mode="json"),
            "roles": cfg.approvals.roles,
            "hr_roles": cfg.approvals.hr_roles,
            "delegation": cfg.approvals.delegation,
            "segments": [s.model_dump(mode="json") for s in cfg.segments],
            "provenance": cfg.provenance,
            "problems": [{"severity": p.severity, "where": p.where, "message": p.message} for p in problems],
            "taxonomy": {"intents": cfg.taxonomy.intents, "sensitivities": cfg.taxonomy.sensitivities},
        }

    @app.get("/api/rules")
    def rules(tenant: str) -> dict[str, Any]:
        cfg = tenant_config(tenant)
        base = loader.load_base()
        return {"items": [
            {
                "id": rule.id,
                "when": rule.when,
                "mode": rule.mode,
                "base_mode": (base.rule(rule.id).mode if base.rule(rule.id) else rule.mode),
                "tightened": bool(base.rule(rule.id) and base.rule(rule.id).mode != rule.mode),
                "needs_citation": rule.needs_citation,
                "opens_case": rule.opens_case,
                "reason": rule.reason,
                "set_by": cfg.provenance.get(f"rules.{rule.id}.mode", "base"),
            }
            for rule in cfg.rules
        ], "segment_overlays": [
            {"segment": s.id, "label": s.label, "rules": [{"id": r.id, "mode": r.mode, "reason": r.reason} for r in s.rules]}
            for s in cfg.segments if s.rules
        ]}

    @app.get("/api/policies")
    def policies(tenant: str) -> dict[str, Any]:
        cfg = tenant_config(tenant)
        today = date.today()
        out = []
        for doc in cfg.policies:
            live = {v.version for v in policy.in_force(doc, on=today, person=None)}
            out.append({
                "id": doc.id, "title": doc.title, "owner_role": doc.owner_role,
                "review_months": doc.review_months,
                "versions": [
                    {
                        "version": v.version, "status": v.status, "effective_from": v.effective_from,
                        "effective_to": v.effective_to, "scope": v.scope,
                        "quotable_today": v.version in live,
                    }
                    for v in doc.versions
                ],
            })
        return {"items": out}

    @app.get("/api/policies/search")
    def policy_search(tenant: str, q: str, as_person: str | None = None) -> dict[str, Any]:
        cfg = tenant_config(tenant)
        with connect() as conn:
            person = facts_mod.person_by_id(cfg, as_person) if as_person else None
            return policy.search(conn, cfg, q, person=person, limit=5).as_dict()

    @app.get("/api/tools")
    def tool_list(tenant: str) -> dict[str, Any]:
        cfg = tenant_config(tenant)
        with connect() as conn:
            return {
                "items": tools.Registry(conn, cfg).capabilities(),
                "never_writable": sorted(tools.FORBIDDEN_FIELDS),
            }

    @app.get("/api/redaction")
    def redaction(tenant: str) -> dict[str, Any]:
        cfg = tenant_config(tenant)
        return {
            "pack": cfg.redaction.id,
            "patterns": [{"name": p.name, "class": p.klass, "replacement": p.replacement} for p in cfg.redaction.patterns],
            "sensitive_keys": cfg.redaction.sensitive_keys,
        }

    @app.post("/api/redaction/try")
    def redaction_try(tenant: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        cfg = tenant_config(tenant)
        redactor = redact.Redactor(cfg.redaction)
        text = str(body.get("text", ""))
        return {
            "input": text,
            "output": redactor.text(text),
            "findings": [{"pattern": f.pattern, "class": f.klass, "count": f.count} for f in redactor.findings(text)],
        }

    # ---------------------------------------------------------------- the agent

    @app.post("/api/explain")
    def explain(tenant: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """What would happen, and why. Runs nothing and changes nothing."""
        cfg = tenant_config(tenant)
        message = str(body.get("message", "")).strip()
        if not message:
            raise HTTPException(400, "a message is required")
        requester_id = str(body.get("requester", ""))
        person_or_404(cfg, requester_id)

        with connect() as conn:
            classification = classify.classify(
                message, cfg, requester_id=requester_id,
                has_attachment=body.get("attachment") is not None,
            )
            requester = facts_mod.person_by_id(cfg, requester_id)
            subject = facts_mod.subject_of(cfg, classification, requester)
            probe = (
                policy.search(conn, cfg, message, person=subject)
                if classification.intent == "enquiry"
                else policy.Probe(found=False, conflict=False)
            )
            the_facts, context = facts_mod.gather(
                conn, cfg, classification=classification, requester_id=requester_id, probe=probe
            )
            segments = engine.segments_for(cfg, subject or requester)
            verdict = engine.evaluate(cfg, the_facts, segment_ids=segments)
        return {
            "classification": classification.as_dict(),
            "facts": the_facts,
            "context": context,
            "verdict": verdict.as_dict(),
            "probe": probe.as_dict(),
            "segments": segments,
        }

    @app.post("/api/chat")
    def chat(
        tenant: str, body: dict[str, Any] = Body(...), authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        """Run one request for real."""
        cfg = tenant_config(tenant)
        message = str(body.get("message", "")).strip()
        if not message:
            raise HTTPException(400, "a message is required")
        requester_id = who(tenant, str(body.get("requester", "")), authorization)
        person_or_404(cfg, requester_id or "")
        # A document arriving with the message. The kernel has always taken one;
        # this route did not pass it, which made document intake unreachable from
        # the dashboard however the message was worded.
        attachment = body.get("attachment")
        if attachment is not None and not isinstance(attachment, dict):
            raise HTTPException(400, "an attachment must be an object")
        with connect() as conn:
            outcome = kernel.run(
                conn, cfg, message=message, requester_id=requester_id,
                channel=str(body.get("channel", "web")), attachment=attachment,
            )
            return _item_detail(conn, cfg, outcome.work_item_id)

    @app.get("/api/work-items")
    def work_items(
        tenant: str,
        limit: int = Query(50, ge=1, le=200),
        status: str | None = None,
        before: str | None = Query(None, description="cursor from a previous page's nextCursor"),
    ) -> dict[str, Any]:
        """A page of work items, newest first.

        Keyset pagination on (created_at, id) rather than an offset: an offset
        skips or repeats rows when new work arrives mid-scroll, which on this
        list it constantly does.
        """
        cfg = tenant_config(tenant)
        sql = (
            "SELECT w.*, d.verify_passed, d.rule_reason FROM work_item w "
            "LEFT JOIN decision d ON d.work_item_id = w.id "
            "WHERE w.tenant_id = ?"
        )
        params: list[Any] = [cfg.tenant_id]
        if status:
            sql += " AND w.status = ?"
            params.append(status)
        if before:
            try:
                at, last_id = before.split("|", 1)
            except ValueError as exc:
                raise HTTPException(400, "that cursor is not readable") from exc
            sql += " AND (w.created_at < ? OR (w.created_at = ? AND w.id < ?))"
            params += [at, at, last_id]
        sql += " ORDER BY w.created_at DESC, w.id DESC LIMIT ?"
        params.append(limit + 1)       # one extra tells us whether there is more
        with connect() as conn:
            rows = db.rows(conn, sql, params)
            more = len(rows) > limit
            rows = rows[:limit]
            inbound = {
                r["work_item_id"]: r["body"]
                for r in db.rows(
                    conn,
                    "SELECT work_item_id, body FROM message_log WHERE tenant_id = ? AND direction = 'inbound'",
                    (cfg.tenant_id,),
                )
            }
            names = {p.id: p.name for p in cfg.people}
            return {
                "items": [
                    {
                        "id": r["id"], "status": r["status"], "mode": r["mode"], "rule_id": r["rule_id"],
                        "intent": r["intent"], "sensitivity": r["sensitivity"],
                        "requester": r["requester_id"],
                        "requester_name": names.get(r["requester_id"], r["requester_id"]),
                        "created_at": r["created_at"], "verify_passed": r["verify_passed"],
                        "message": inbound.get(r["id"], ""), "parent_id": r["parent_id"],
                    }
                    for r in rows
                ],
                "nextCursor": f"{rows[-1]['created_at']}|{rows[-1]['id']}" if more and rows else None,
            }

    def _item_detail(conn: sqlite3.Connection, cfg: ResolvedConfig, work_item_id: str) -> dict[str, Any]:
        item = db.one(
            conn, "SELECT * FROM work_item WHERE tenant_id = ? AND id = ?", (cfg.tenant_id, work_item_id)
        )
        if item is None:
            raise HTTPException(404, f"no such work item: {work_item_id}")
        decision = db.one(
            conn, "SELECT * FROM decision WHERE tenant_id = ? AND work_item_id = ? ORDER BY created_at DESC LIMIT 1",
            (cfg.tenant_id, work_item_id),
        )
        names = {p.id: p.name for p in cfg.people}
        trail = audit.trail(conn, cfg.tenant_id, work_item_id)
        return {
            "item": {
                **{k: item[k] for k in item.keys()},
                "requester_name": names.get(item["requester_id"], item["requester_id"]),
            },
            "decision": (
                {
                    "id": decision["id"], "rule_id": decision["rule_id"], "mode": decision["mode"],
                    "rule_reason": decision["rule_reason"], "needs_citation": bool(decision["needs_citation"]),
                    "draft": decision["draft_text"], "verify_passed": bool(decision["verify_passed"]),
                    "checks": db.loads(decision["verify_json"], []),
                    "facts": db.loads(decision["facts_json"], {}),
                    "retrieval": db.loads(decision["retrieval_json"], {}),
                    "provider": decision["provider"], "model_id": decision["model_id"],
                    "pack_hash": decision["pack_hash"],
                    "human_verdict": decision["human_verdict"], "human_note": decision["human_note"],
                    "human_actor": decision["human_actor_id"],
                }
                if decision else None
            ),
            "citations": [dict(c) for c in db.rows(
                conn, "SELECT * FROM citation WHERE tenant_id = ? AND work_item_id = ?", (cfg.tenant_id, work_item_id)
            )],
            "plans": [dict(p) for p in db.rows(
                conn, "SELECT * FROM action_plan WHERE tenant_id = ? AND work_item_id = ? ORDER BY prepared_at",
                (cfg.tenant_id, work_item_id),
            )],
            "messages": [dict(m) for m in db.rows(
                conn, "SELECT * FROM message_log WHERE tenant_id = ? AND work_item_id = ? ORDER BY at",
                (cfg.tenant_id, work_item_id),
            )],
            "approval": (lambda r: dict(r) if r else None)(db.one(
                conn, "SELECT * FROM approval WHERE tenant_id = ? AND work_item_id = ? ORDER BY opened_at DESC LIMIT 1",
                (cfg.tenant_id, work_item_id),
            )),
            "case": (lambda r: dict(r) if r else None)(db.one(
                conn, "SELECT * FROM hr_case WHERE tenant_id = ? AND id = ?", (cfg.tenant_id, item["case_id"] or ""),
            )),
            "trail": [
                {
                    "seq": r["seq"], "at": r["at"], "actor": r["actor"], "actor_kind": r["actor_kind"],
                    "stage": r["stage"], "action": r["action"], "detail": db.loads(r["detail_json"], {}),
                }
                for r in trail
            ],
            "children": [
                dict(c) for c in db.rows(
                    conn, "SELECT id, status, mode, rule_id FROM work_item WHERE tenant_id = ? AND parent_id = ?",
                    (cfg.tenant_id, work_item_id),
                )
            ],
        }

    @app.get("/api/work-items/{work_item_id}")
    def work_item(work_item_id: str, tenant: str) -> dict[str, Any]:
        cfg = tenant_config(tenant)
        with connect() as conn:
            return _item_detail(conn, cfg, work_item_id)

    # ---------------------------------------------------------------- queue

    @app.get("/api/reason-codes")
    def reason_codes() -> dict[str, Any]:
        """Why a draft can be sent back. A short fixed list, not free text."""
        return {
            "items": [
                {"code": code, "label": label, "points_at": approvals.REASON_POINTS_AT[code]}
                for code, label in approvals.REASON_CODES.items()
            ]
        }

    @app.get("/api/queue")
    def get_queue(
        tenant: str, actor: str | None = None, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        cfg = tenant_config(tenant)
        actor = who(tenant, actor, authorization)
        with connect() as conn:
            items = approvals.queue(conn, cfg, actor_id=actor)
            names = {p.id: p.name for p in cfg.people}
            for item in items:
                item["requester_name"] = names.get(item.get("requester_id"), item.get("requester_id"))
                item["role_label"] = cfg.approvals.roles.get(item["assigned_role"], item["assigned_role"])
            return {"items": items, "mine": sum(1 for i in items if i["mine"])}

    @app.post("/api/approvals/{approval_id}/decide")
    def decide(
        approval_id: str, tenant: str, body: dict[str, Any] = Body(...),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        cfg = tenant_config(tenant)
        # With authentication on, who decided is the signed-in person. A body
        # field must never be able to say otherwise -- approving as somebody
        # else is the one thing this queue exists to prevent.
        actor = who(tenant, str(body.get("actor", "")), authorization)
        person_or_404(cfg, actor or "")
        with connect() as conn:
            try:
                result = approvals.decide(
                    conn, cfg, approval_id=approval_id, actor_id=actor,
                    outcome=str(body.get("outcome", "")), note=body.get("note") or None,
                    reason_code=body.get("reason_code") or None,
                )
            except approvals.ApprovalError as exc:
                # A refusal is an expected outcome, not a server fault.
                return JSONResponse(status_code=409, content={"error": str(exc)})
            return {
                "outcome": result.outcome,
                "committed": result.committed,
                "cancelled": result.cancelled,
                "reason_code": result.reason_code,
                "work_item": approval_id,
            }

    # ---------------------------------------------------------------- interviews

    @app.get("/api/interviews")
    def interview_list(
        tenant: str,
        from_date: str | None = Query(None, alias="from"),
        to_date: str | None = Query(None, alias="to"),
        status: str | None = None,
    ) -> dict[str, Any]:
        """Interviews booked for this company, soonest first.

        Defaults to everything from today onwards: a list that opens on last
        month's interviews answers a question nobody asked.
        """
        cfg = tenant_config(tenant)
        with connect() as conn:
            rows = interviews.scheduled(
                conn, cfg,
                from_date=from_date if from_date is not None else date.today(),
                to_date=to_date, status=status,
            )
            stages = {s.id: s.label for s in cfg.interviews.stages}
            return {
                "items": [
                    {
                        **row,
                        "stage_label": row["stage_label"] or stages.get(row["stage"]) or "Interview",
                        "days_away": (date.fromisoformat(row["on_date"]) - date.today()).days,
                        "when_label": interviews.written_date(cfg, date.fromisoformat(row["on_date"])),
                    }
                    for row in rows
                ],
                "settings": cfg.interviews.model_dump(mode="json"),
                # The pack's own words for its roles. Every other screen shows
                # the label rather than the id, and a panel requirement reading
                # "hr_lead" is the kind of leak that makes config look like code.
                "roles": cfg.approvals.roles,
                "timezone": cfg.locale.timezone,
                "working_hours": cfg.locale.working_hours.model_dump(mode="json"),
            }

    @app.get("/api/interviews/availability")
    def interview_availability(
        tenant: str,
        panel: str = Query(..., description="comma-separated person ids"),
        on: str | None = None,
        minutes: int | None = None,
        stage: str | None = None,
    ) -> dict[str, Any]:
        """When these people could be interviewed together. Reads; books nothing.

        The same solver the agent uses, so the screen cannot show times the
        agent would not propose -- a preview that disagrees with the thing it
        previews is worse than no preview.
        """
        cfg = tenant_config(tenant)
        people = [p.strip() for p in panel.split(",") if p.strip()]
        for person_id in people:
            person_or_404(cfg, person_id)
        found = cfg.interviews.stage(stage) if stage else None
        if stage and found is None:
            raise HTTPException(404, f"{tenant} has no interview stage called {stage!r}")

        with connect() as conn:
            registry = tools.Registry(conn, cfg)
            calendars = registry.of_kind("calendar")
            if not calendars:
                raise HTTPException(409, f"{tenant} has no calendar system connected")
            request = interviews.Request(
                stage=found,
                stage_label=found.label if found else "Interview",
                minutes=minutes or cfg.interviews.minutes_for(found),
                panel_ids=people,
                on=date.fromisoformat(on) if on else None,
            )
            proposal = interviews.propose(
                conn, cfg, request=request, on=date.today(),
                read_free_busy=lambda **args: registry.read(calendars[0].name, "free_busy", **args),
            )
            return {
                "request": request.as_dict(),
                "proposal": proposal.as_dict(cfg),
                "calendar": calendars[0].name,
            }

    # ---------------------------------------------------------------- audit & metrics

    @app.get("/api/jobs")
    def jobs() -> dict[str, Any]:
        return {"items": [{"id": j, "label": j.replace("_", " ")} for j in scheduler.JOBS]}

    @app.post("/api/jobs/{job}")
    def run_job(job: str, tenant: str, on: str | None = None) -> dict[str, Any]:
        """Run one scheduled check, or all of them. They report and change nothing."""
        cfg = tenant_config(tenant)
        as_of = date.fromisoformat(on) if on else None
        with connect() as conn:
            try:
                results = (
                    scheduler.run_all(conn, cfg, on=as_of)
                    if job == "all"
                    else [scheduler.run_job(conn, cfg, job, on=as_of)]
                )
            except ValueError as exc:
                raise HTTPException(404, str(exc)) from exc
            return {"items": [r.as_dict() | {"detail": r.detail} for r in results]}

    @app.get("/api/audit")
    def audit_rows(tenant: str, work_item: str | None = None, limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
        cfg = tenant_config(tenant)
        with connect() as conn:
            chain = audit.verify(conn, cfg.tenant_id)
            sql = "SELECT * FROM audit WHERE tenant_id = ?"
            params: list[Any] = [cfg.tenant_id]
            if work_item:
                sql += " AND work_item_id = ?"
                params.append(work_item)
            sql += " ORDER BY seq DESC LIMIT ?"
            params.append(limit)
            return {
                "chain": {"ok": chain.ok, "checked": chain.checked, "broken_at": chain.broken_at, "reason": chain.reason},
                "limitation": "Truncating the end of a chain leaves a valid chain. Detecting that needs an "
                              "external anchor, which this build does not have yet.",
                "items": [
                    {
                        "seq": r["seq"], "at": r["at"], "actor": r["actor"], "actor_kind": r["actor_kind"],
                        "stage": r["stage"], "action": r["action"], "work_item_id": r["work_item_id"],
                        "config_hash": r["config_hash"], "detail": db.loads(r["detail_json"], {}),
                    }
                    for r in db.rows(conn, sql, params)
                ],
            }

    @app.get("/api/metrics")
    def metrics_report(tenant: str) -> dict[str, Any]:
        cfg = tenant_config(tenant)
        with connect() as conn:
            return metrics.compute(conn, cfg).as_dict()

    # ---------------------------------------------------------------- packs

    @app.get("/api/golden-cases")
    def golden_cases(tenant: str) -> dict[str, Any]:
        cfg = tenant_config(tenant)
        return {"items": [c.model_dump(mode="json") for c in cfg.golden_cases]}

    @app.post("/api/simulate")
    def simulate(tenant: str, on: str | None = None) -> dict[str, Any]:
        """Run this company's own golden cases in a throwaway database.

        `on` runs them as if it were that date. Because policy versions are
        effective-dated, a company can see what its own agent will say the day a
        new policy takes effect -- before it does.
        """
        cfg = tenant_config(tenant)
        conn = db.open_db(":memory:")
        db.insert(conn, "tenant", {
            "id": cfg.tenant_id, "name": cfg.manifest.name, "pack_path": "packs",
            "config_hash": cfg.config_hash, "active": 1, "created_at": db.now(),
        })
        for person in cfg.people:
            db.insert(conn, "employee", {
                "tenant_id": cfg.tenant_id, "id": person.id, "name": person.name, "email": person.email,
                "manager_id": person.manager_id, "entity": person.entity,
                "employment_type": person.employment_type, "segment_keys": person.segment_keys,
                "roles": person.roles, "timezone": person.timezone, "start_date": person.start_date,
                "end_date": person.end_date, "active": int(person.active),
            })
        policy.load_corpus(conn, cfg)

        as_of = date.fromisoformat(on) if on else None
        results, reached = [], set()
        for case in cfg.golden_cases:
            outcome = kernel.run(conn, cfg, message=case.message, requester_id=case.requester,
                                 attachment=case.attachment, on=as_of)
            reached.add(outcome.rule_id)
            results.append({
                "id": case.id, "message": case.message, "requester": case.requester,
                "expected": f"{case.expect_rule}/{case.expect_mode}",
                "got": f"{outcome.rule_id}/{outcome.mode}",
                "pass": outcome.rule_id == case.expect_rule and outcome.mode == case.expect_mode,
                "verified": outcome.verify_passed, "note": case.note,
                "cited": [f"{c.doc_id} v{c.version} s{c.section_id}" for c in outcome.citations],
            })
        passed = sum(1 for r in results if r["pass"])
        return {
            "tenant": cfg.tenant_id, "on": (as_of or date.today()).isoformat(),
            "passed": passed, "total": len(results), "results": results,
            "rules_reached": sorted(reached),
            "rules_never_reached": sorted({r.id for r in cfg.rules} - reached),
        }

    return app


app = create_app()


def serve(host: str = "127.0.0.1", port: int = 4000, db_path: str | None = None, reload: bool = False) -> None:
    """Start the API. Refuses a public bind while role switching is on.

    `reload` is on by default from the CLI: a long-running dev server that has
    quietly kept old code loaded reports stale answers, which is a confusing
    thing to debug and an easy thing to prevent.
    """
    import uvicorn

    if DEMO_ROLE_SWITCHING and host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit(
            f"refusing to bind {host}: anyone could act as anyone while role switching is on.\n"
            "Bind 127.0.0.1, or turn role switching off first."
        )
    if reload and db_path is None:
        # Reload needs an import string rather than a built app.
        uvicorn.run("hr_agent.web:app", host=host, port=port, log_level="warning", reload=True)
    else:
        uvicorn.run(create_app(db_path=db_path), host=host, port=port, log_level="warning")
