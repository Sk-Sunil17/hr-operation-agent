"""Adapters: the only way anything reaches the world.

Every adapter declares, in the pack, which fields it owns and which operations
it supports. The kernel refuses anything else. Three rules hold everywhere:

    read      no side effects, ever
    prepare   writes a plan and a human-readable preview, and nothing else
    commit    acts exactly once, under an idempotency key

A second commit with the same key returns the first receipt rather than acting
again, so a retry, a double click and a replayed event are all harmless.

No adapter may write pay or grant access. That is checked when the pack loads
and again here at runtime, because a guarantee worth having is worth checking
twice.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any

from . import db, interviews
from .packs.models import Adapter as AdapterDecl
from .packs.models import ResolvedConfig

# What an address looks like in a pack setting. Deliberately loose on the local
# part and tight on the shape, because the only thing being decided is "did the
# company write an address here", not "is this address deliverable".
_ADDRESS = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"

# Never writable, on any path, including behind an approval.
FORBIDDEN_FIELDS = {
    "salary", "pay", "pay_rate", "base_pay", "bonus", "compensation",
    "access", "permissions", "roles", "entitlements", "grade",
}


class ToolError(RuntimeError):
    """A tool was asked for something it must not or cannot do."""


@dataclass
class Prepared:
    """What a person approving this will be shown."""

    preview: str
    args: dict[str, Any]


# ------------------------------------------------------------------ adapters


class BaseAdapter:
    kind = "none"
    READS: tuple[str, ...] = ()
    WRITES: tuple[str, ...] = ()

    # Whether this system can reach somebody who does not work here.
    #
    # A property of the implementation, not a company setting: Slack cannot
    # email a job candidate however it is configured, and letting a pack claim
    # otherwise would mean an interview invitation being "sent" into a channel
    # the candidate cannot see, with a committed plan saying it went out.
    REACHES_EXTERNAL = False

    def __init__(self, decl: AdapterDecl, cfg: ResolvedConfig, conn: sqlite3.Connection) -> None:
        self.decl = decl
        self.name = decl.name
        self.cfg = cfg
        self.conn = conn

    # -- capability questions the kernel asks before anything happens
    def supports(self, operation: str) -> bool:
        return operation in self.decl.operations

    def is_write(self, operation: str) -> bool:
        return operation in self.WRITES

    def owns(self, field: str) -> bool:
        return field in self.decl.owns_fields

    # -- the three verbs
    def read(self, operation: str, **args: Any) -> dict[str, Any]:
        raise ToolError(f"{self.name} has no read operation {operation!r}")

    def preview(self, operation: str, **args: Any) -> Prepared:
        raise ToolError(f"{self.name} cannot prepare {operation!r}")

    def act(self, operation: str, **args: Any) -> dict[str, Any]:
        raise ToolError(f"{self.name} cannot commit {operation!r}")

    # -- helpers
    def _person(self, person_id: str) -> dict[str, Any]:
        row = db.one(
            self.conn, "SELECT * FROM employee WHERE tenant_id = ? AND id = ?", (self.cfg.tenant_id, person_id)
        )
        if row is None:
            raise ToolError(f"no such person in {self.cfg.tenant_id}: {person_id!r}")
        return dict(row)


class MockHris(BaseAdapter):
    """Reads the directory; writes go to `adapter_record` so a committed change is visible."""

    kind = "hris"
    READS = ("read_person", "read_manager", "read_record")
    WRITES = ("commit_record_change",)

    def read(self, operation: str, **args: Any) -> dict[str, Any]:
        if operation == "read_person":
            person = self._person(args["person_id"])
            person["segment_keys"] = db.loads(person["segment_keys"], [])
            person["roles"] = db.loads(person["roles"], [])
            person["overrides"] = self._overrides(args["person_id"])
            return person
        if operation == "read_manager":
            person = self._person(args["person_id"])
            return self._person(person["manager_id"]) if person["manager_id"] else {}
        if operation == "read_record":
            return self._overrides(args["person_id"])
        return super().read(operation, **args)

    def _overrides(self, person_id: str) -> dict[str, Any]:
        rows = db.rows(
            self.conn,
            "SELECT field, value FROM adapter_record WHERE tenant_id = ? AND adapter = ? AND person_id = ?",
            (self.cfg.tenant_id, self.name, person_id),
        )
        return {r["field"]: r["value"] for r in rows}

    def preview(self, operation: str, **args: Any) -> Prepared:
        if operation != "prepare_record_change":
            return super().preview(operation, **args)
        person = self._person(args["person_id"])
        changes: dict[str, Any] = args["changes"]
        self._check_fields(changes)  # refuses pay, access, and anything this adapter does not own
        current = self._overrides(args["person_id"])
        lines = [
            f"  {field}: {current.get(field) or '(not set)'}  ->  {value}"
            for field, value in sorted(changes.items())
        ]
        return Prepared(
            preview=f"Change {person['name']}'s record in {self.name}:\n" + "\n".join(lines),
            args={"person_id": args["person_id"], "changes": changes},
        )

    def act(self, operation: str, **args: Any) -> dict[str, Any]:
        if operation != "commit_record_change":
            return super().act(operation, **args)
        changes: dict[str, Any] = args["changes"]
        self._check_fields(changes)
        now = db.now()
        for field, value in changes.items():
            self.conn.execute(
                "INSERT INTO adapter_record (tenant_id, adapter, person_id, field, value, updated_at, updated_by) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(tenant_id, adapter, person_id, field) "
                "DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                (self.cfg.tenant_id, self.name, args["person_id"], field, str(value), now, args.get("actor")),
            )
        return {"updated": sorted(changes), "person_id": args["person_id"], "at": now}

    def _check_fields(self, changes: dict[str, Any]) -> None:
        for field in changes:
            if field in FORBIDDEN_FIELDS:
                raise ToolError(f"no tool may write {field!r}, on any path, including behind an approval")
            if not self.owns(field):
                raise ToolError(
                    f"{self.name} does not own {field!r} (it owns: {', '.join(self.decl.owns_fields) or 'nothing'})"
                )


class MockNotify(BaseAdapter):
    """The only way a message leaves. Committing writes the message log."""

    kind = "notify"
    READS = ()
    WRITES = ("commit_message",)

    def preview(self, operation: str, **args: Any) -> Prepared:
        if operation != "prepare_message":
            return super().preview(operation, **args)
        to = args.get("to") or self.decl.settings.get("default_channel", "the requester")
        body = args["body"]
        return Prepared(
            preview=f"Send to {to} via {self.name}:\n\n{body}",
            args={"to": to, "body": body, "work_item_id": args["work_item_id"]},
        )

    def act(self, operation: str, **args: Any) -> dict[str, Any]:
        if operation != "commit_message":
            return super().act(operation, **args)
        message_id = "msg_" + uuid.uuid4().hex[:12]
        db.insert(
            self.conn,
            "message_log",
            {
                "id": message_id,
                "tenant_id": self.cfg.tenant_id,
                "work_item_id": args["work_item_id"],
                "direction": "outbound",
                "channel": self.name,
                "body": args["body"],
                "idempotency_key": args.get("idempotency_key"),
                "suppressed": 0,
                "at": db.now(),
            },
        )
        return {"message_id": message_id, "to": args.get("to")}


class MockCalendar(BaseAdapter):
    """A stand-in calendar. Free/busy is deterministic, so a test can rely on it.

    Two things here are real rather than mocked, because the behaviour that
    depends on them is the behaviour worth testing:

      * Interviews this agent has already booked are reported as busy time. A
        mock that forgot them would let the agent propose the same hour twice
        and the double-booking would only show up in production.
      * Committing writes the interview and its attendees. That is what makes a
        reschedule, a cancellation and the daily cap possible at all.
    """

    kind = "calendar"
    READS = ("free_busy",)
    WRITES = ("commit_invite", "commit_cancel")

    def read(self, operation: str, **args: Any) -> dict[str, Any]:
        if operation != "free_busy":
            return super().read(operation, **args)
        hours = self.cfg.locale.working_hours
        start_hour = int(hours.start.split(":")[0])
        end_hour = int(hours.end.split(":")[0])
        buffer_minutes = int(self.decl.settings.get("buffer_minutes", self.cfg.interviews.buffer_minutes))

        windows: list[dict[str, str]] = []
        for person_id in args["person_ids"]:
            # Deterministic pseudo-busy: same person, same day, same answer.
            seed = int(hashlib.sha256(f"{person_id}{args['date']}".encode()).hexdigest()[:8], 16)
            busy_hour = start_hour + (seed % max(1, end_hour - start_hour - 1))
            windows.append({"person_id": person_id, "busy_from": f"{busy_hour:02d}:00", "busy_to": f"{busy_hour + 1:02d}:00"})

        # Interviews already in this calendar. Real busy time, from our own table.
        # An interview being rescheduled is excluded, or it would block the very
        # slot it is being moved out of.
        sql = (
            "SELECT i.start_time, i.end_time, a.person_id FROM interview i "
            "JOIN interview_attendee a ON a.interview_id = i.id AND a.tenant_id = i.tenant_id "
            "WHERE i.tenant_id = ? AND i.on_date = ? AND i.status = 'scheduled' "
            "AND a.person_id IS NOT NULL"
        )
        params: list[Any] = [self.cfg.tenant_id, args["date"]]
        if args.get("exclude_interview_id"):
            sql += " AND i.id != ?"
            params.append(args["exclude_interview_id"])
        for row in db.rows(self.conn, sql, params):
            if row["person_id"] in set(args["person_ids"]):
                windows.append({
                    "person_id": row["person_id"],
                    "busy_from": row["start_time"],
                    "busy_to": row["end_time"],
                    "reason": "an interview already booked",
                })

        busy = {w["busy_from"][:2] for w in windows}
        free = [
            f"{h:02d}:00"
            for h in range(start_hour, end_hour)
            if f"{h:02d}" not in busy and f"{h + 1:02d}" not in busy
        ]
        return {
            "date": args["date"],
            "busy": windows,
            "free_starts": free,
            "buffer_minutes": buffer_minutes,
            "within": f"{hours.start}-{hours.end} {self.cfg.locale.timezone}",
        }

    def preview(self, operation: str, **args: Any) -> Prepared:
        if operation == "prepare_invite":
            return self._preview_invite(**args)
        if operation == "prepare_cancel":
            return Prepared(
                preview=(
                    f"Withdraw the {args.get('stage_label') or 'interview'} on {args['date']} "
                    f"{args['start']} from {self.name}, and tell everyone who was invited.\n"
                    f"  Reason: {args.get('reason') or 'not given'}"
                ),
                args=dict(args),
            )
        return super().preview(operation, **args)

    def _preview_invite(self, **args: Any) -> Prepared:
        """What a person approving an interview is shown.

        The candidate's address is written out in full and labelled as coming
        from the message. It is the one address here that did not come from the
        directory, so it is the one an approver has to actually look at.
        """
        people = {p.id: p.name for p in self.cfg.people}
        attendees = args.get("attendees") or [{"person_id": p} for p in args.get("person_ids", [])]
        panel = [a for a in attendees if a.get("part") != "candidate"]
        candidate = next((a for a in attendees if a.get("part") == "candidate"), None)

        lines = [
            f"Put a {args.get('minutes', 60)}-minute {args.get('stage_label') or 'interview'} in "
            f"{self.name} on {args['date']} at {args['start']} "
            f"({args.get('timezone') or self.cfg.locale.timezone}).",
        ]
        if args.get("role_title"):
            lines.append(f"  For: {args['role_title']}")
        if args.get("location"):
            lines.append(f"  Where: {args['location']}")
        lines.append(
            "  Interviewers: "
            + (", ".join(people.get(a.get("person_id"), a.get("name") or a.get("person_id")) for a in panel) or "nobody")
        )
        if candidate:
            lines.append(
                f"  Candidate: {candidate.get('name') or 'not named'}"
                + (f" <{candidate['email']}>" if candidate.get("email") else " (no address given)")
                + "  — taken from the message, not from the directory"
            )
        else:
            lines.append("  Candidate: not identified, so this is a held slot and nobody outside the company is invited")
        if args.get("supersedes"):
            lines.append(f"  Replaces: interview {args['supersedes']}")
        return Prepared(preview="\n".join(lines), args=dict(args))

    def act(self, operation: str, **args: Any) -> dict[str, Any]:
        if operation == "commit_invite":
            return self._commit_invite(**args)
        if operation == "commit_cancel":
            return self._commit_cancel(**args)
        return super().act(operation, **args)

    def _commit_invite(self, **args: Any) -> dict[str, Any]:
        reference = "cal_" + uuid.uuid4().hex[:10]
        people = {p.id: p for p in self.cfg.people}
        attendees = args.get("attendees") or [
            {"person_id": p, "name": people[p].name if p in people else p,
             "email": people[p].email if p in people else None, "part": "panel"}
            for p in args.get("person_ids", [])
        ]
        interview_id = args.get("interview_id") or interviews.new_id()
        interviews.record(
            self.conn,
            tenant_id=self.cfg.tenant_id,
            interview_id=interview_id,
            work_item_id=args.get("work_item_id"),
            case_id=args.get("case_id"),
            stage=args.get("stage"),
            stage_label=args.get("stage_label"),
            role_title=args.get("role_title"),
            candidate_name=next((a.get("name") for a in attendees if a.get("part") == "candidate"), None),
            candidate_email=next((a.get("email") for a in attendees if a.get("part") == "candidate"), None),
            on_date=args["date"],
            start_time=args["start"],
            end_time=args["end"],
            minutes=int(args.get("minutes", 60)),
            timezone=args.get("timezone") or self.cfg.locale.timezone,
            location=args.get("location"),
            calendar_ref=reference,
            supersedes=args.get("supersedes"),
            created_by=args.get("actor"),
            attendees=attendees,
        )
        return {
            "interview_id": interview_id, "invite_id": reference, "calendar_ref": reference,
            "date": args["date"], "start": args["start"], "end": args["end"],
            "attendees": len(attendees),
        }

    def _commit_cancel(self, **args: Any) -> dict[str, Any]:
        cancelled = interviews.cancel(
            self.conn, tenant_id=self.cfg.tenant_id, interview_id=args["interview_id"],
            reason=args.get("reason") or "cancelled by the people team",
        )
        return {"cancelled": cancelled, "interview_id": args["interview_id"]}


class MockMail(BaseAdapter):
    """Email. The only adapter here that can reach somebody who does not work here.

    A job candidate has no account in any internal system, so an interview that
    cannot be emailed cannot be communicated at all. This writes the message log
    like any other notifier, and carries the calendar invitation with it so the
    recipient's own calendar picks it up rather than their having to retype a
    date out of a paragraph.
    """

    kind = "notify"
    READS = ()
    WRITES = ("commit_message",)
    REACHES_EXTERNAL = True

    def preview(self, operation: str, **args: Any) -> Prepared:
        if operation != "prepare_message":
            return super().preview(operation, **args)
        to = args.get("to") or self.decl.settings.get("default_channel")
        if not to:
            raise ToolError(f"{self.name} needs an address to send to, and none was given")
        sender = self.decl.settings.get("from_address", f"noreply@{self.cfg.tenant_id}.invalid")
        subject = args.get("subject") or "(no subject)"
        attached = "\n  With a calendar invitation attached (.ics)" if args.get("ics") else ""
        return Prepared(
            preview=f"Email from {sender} to {to}{attached}\n  Subject: {subject}\n\n{args['body']}",
            args={
                "to": to, "subject": subject, "body": args["body"], "ics": args.get("ics"),
                "work_item_id": args["work_item_id"],
            },
        )

    def act(self, operation: str, **args: Any) -> dict[str, Any]:
        if operation != "commit_message":
            return super().act(operation, **args)
        message_id = "mail_" + uuid.uuid4().hex[:12]
        subject = args.get("subject") or "(no subject)"
        db.insert(self.conn, "message_log", {
            "id": message_id,
            "tenant_id": self.cfg.tenant_id,
            "work_item_id": args["work_item_id"],
            "direction": "outbound",
            "channel": self.name,
            # The subject is part of the message, and a log that drops it cannot
            # answer "what did we actually send this candidate".
            "body": f"To: {args.get('to')}\nSubject: {subject}\n\n{args['body']}",
            "idempotency_key": args.get("idempotency_key"),
            "suppressed": 0,
            "at": db.now(),
        })
        return {
            "message_id": message_id, "to": args.get("to"), "subject": subject,
            "carried_invitation": bool(args.get("ics")),
        }


class MockItsm(BaseAdapter):
    """Raises a request in a service desk. It can request; it cannot decide."""

    kind = "itsm"
    READS = ()
    WRITES = ("commit_request",)

    ALLOWED = {"record_change", "document_intake", "onboarding", "exit", "scheduling", "sensitive_matter", "unresolved"}

    def preview(self, operation: str, **args: Any) -> Prepared:
        if operation != "prepare_request":
            return super().preview(operation, **args)
        if args["kind"] not in self.ALLOWED:
            raise ToolError(f"{self.name} does not raise {args['kind']!r} requests")
        queue = self.decl.settings.get("queue", "HR")
        return Prepared(preview=f"Raise a {args['kind']} request in {queue}: {args['summary']}", args=dict(args))

    def act(self, operation: str, **args: Any) -> dict[str, Any]:
        if operation != "commit_request":
            return super().act(operation, **args)
        return {"ticket": "REQ-" + uuid.uuid4().hex[:8].upper(), "queue": self.decl.settings.get("queue", "HR")}


class MockDocstore(BaseAdapter):
    kind = "docstore"
    READS = ("read_document",)
    WRITES = ("commit_file",)

    def read(self, operation: str, **args: Any) -> dict[str, Any]:
        if operation != "read_document":
            return super().read(operation, **args)
        return {"document_id": args["document_id"], "pages": 1}

    def preview(self, operation: str, **args: Any) -> Prepared:
        if operation != "prepare_file":
            return super().preview(operation, **args)
        expiry = f", expiring {args['expires_at']}" if args.get("expires_at") else ""
        return Prepared(
            preview=f"File {args['document_type']} for {args['person_id']} in {self.name}{expiry}",
            args={k: v for k, v in args.items() if k != "work_item_id"} | {"work_item_id": args["work_item_id"]},
        )

    def act(self, operation: str, **args: Any) -> dict[str, Any]:
        if operation != "commit_file":
            return super().act(operation, **args)
        reference = "DOC-" + uuid.uuid4().hex[:8].upper()
        self.conn.execute(
            "INSERT INTO filed_document (tenant_id, person_id, document_type, reference, filed_at, "
            "expires_at, filed_by, template_version) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(tenant_id, person_id, document_type) DO UPDATE SET "
            "reference = excluded.reference, filed_at = excluded.filed_at, "
            "expires_at = excluded.expires_at, filed_by = excluded.filed_by, "
            "template_version = excluded.template_version",
            (
                self.cfg.tenant_id, args["person_id"], args["document_type"], reference, db.now(),
                args.get("expires_at"), args.get("actor"), args.get("template_version"),
            ),
        )
        return {"filed": args["document_type"], "reference": reference, "expires_at": args.get("expires_at")}


class MockEsign(BaseAdapter):
    kind = "esign"
    READS = ()
    WRITES = ("commit_envelope",)

    def preview(self, operation: str, **args: Any) -> Prepared:
        if operation != "prepare_envelope":
            return super().preview(operation, **args)
        return Prepared(preview=f"Send {args['document']} for signature to {args['person_id']}", args=dict(args))

    def act(self, operation: str, **args: Any) -> dict[str, Any]:
        if operation != "commit_envelope":
            return super().act(operation, **args)
        return {"envelope": "ENV-" + uuid.uuid4().hex[:8].upper()}


IMPLEMENTATIONS: dict[str, type[BaseAdapter]] = {
    "mock_hris": MockHris,
    "mock_notify": MockNotify,
    "mock_calendar": MockCalendar,
    "mock_mail": MockMail,
    "mock_itsm": MockItsm,
    "mock_docstore": MockDocstore,
    "mock_esign": MockEsign,
}

# Implementations that talk to a real outside service, loaded only when a pack
# actually names one. Keeping them out of this module's imports means the core
# has no opinion about which vendors exist, and a company that uses none of them
# pays nothing for their existence.
EXTERNAL_MODULES = ("hr_agent.gworkspace",)


def implementation_for(name: str) -> type[BaseAdapter] | None:
    """Find an adapter implementation by the name a pack used."""
    if name in IMPLEMENTATIONS:
        return IMPLEMENTATIONS[name]
    for module_name in EXTERNAL_MODULES:
        try:
            module = importlib.import_module(module_name)
        except ImportError:  # pragma: no cover - a connector that is not installed
            continue
        found = getattr(module, "IMPLEMENTATIONS", {}).get(name)
        if found is not None:
            return found
    return None


# ------------------------------------------------------------------ registry


class Registry:
    """The adapters one company has configured, and the plan/commit machinery."""

    def __init__(self, conn: sqlite3.Connection, cfg: ResolvedConfig) -> None:
        self.conn = conn
        self.cfg = cfg
        self.adapters: dict[str, BaseAdapter] = {}
        for decl in cfg.adapters:
            implementation = implementation_for(decl.implementation)
            if implementation is None:
                raise ToolError(f"no implementation named {decl.implementation!r} for adapter {decl.name!r}")
            self.adapters[decl.name] = implementation(decl, cfg, conn)

    # -- lookups
    def get(self, name: str) -> BaseAdapter:
        if name not in self.adapters:
            raise ToolError(f"{self.cfg.tenant_id} has no adapter named {name!r}")
        return self.adapters[name]

    def of_kind(self, kind: str) -> list[BaseAdapter]:
        return [a for a in self.adapters.values() if a.kind == kind]

    def first_of_kind(self, kind: str) -> BaseAdapter:
        found = self.of_kind(kind)
        if not found:
            raise ToolError(f"{self.cfg.tenant_id} has no {kind} adapter configured")
        return found[0]

    def owning(self, field: str) -> BaseAdapter | None:
        return next((a for a in self.adapters.values() if a.owns(field)), None)

    def notifier(self, *, external: bool = False) -> BaseAdapter:
        """A way to send a message, to somebody here or to somebody outside.

        Asking for an external notifier and getting an internal one would mean a
        candidate's invitation going into a staff channel they cannot read, with
        a committed plan claiming it was delivered. So this refuses instead, and
        the kernel turns the refusal into something a person picks up.
        """
        found = [a for a in self.of_kind("notify") if a.REACHES_EXTERNAL or not external]
        if not found:
            raise ToolError(
                f"{self.cfg.tenant_id} has no notify adapter that can reach somebody "
                "outside the company, so a candidate cannot be told anything from here"
                if external
                else f"{self.cfg.tenant_id} has no notify adapter configured"
            )
        # Prefer one that reaches exactly as far as is needed: using an
        # external-capable system for internal traffic is not wrong, but it is
        # not what a company that configured both would expect.
        exact = [a for a in found if a.REACHES_EXTERNAL == external]
        return (exact or found)[0]

    def declared_addresses(self) -> set[str]:
        """Addresses this company wrote into its own adapter configuration.

        Any setting value that is an address is one the company published about
        itself -- a recruitment mailbox, a help channel, a reply-to. Matched by
        shape rather than by key name, so a pack that calls it `sender` instead
        of `from_address` is still recognised and nothing has to be kept in step.
        """
        found: set[str] = set()
        for adapter in self.adapters.values():
            for value in adapter.decl.settings.values():
                for item in (value if isinstance(value, list) else [value]):
                    if isinstance(item, str) and re.fullmatch(_ADDRESS, item.strip()):
                        found.add(item.strip().lower())
        return found

    def capabilities(self) -> list[dict[str, Any]]:
        return [
            {
                "name": a.name,
                "kind": a.kind,
                "implementation": a.decl.implementation,
                "owns": a.decl.owns_fields,
                "operations": a.decl.operations,
                "reads": [o for o in a.decl.operations if o in a.READS],
                "writes": [o for o in a.decl.operations if o in a.WRITES],
                "reaches_external": a.REACHES_EXTERNAL,
            }
            for a in self.adapters.values()
        ]

    # -- read: no side effects
    def read(self, adapter: str, operation: str, **args: Any) -> dict[str, Any]:
        tool = self.get(adapter)
        if not tool.supports(operation):
            raise ToolError(f"{adapter} does not support {operation!r}")
        if tool.is_write(operation):
            raise ToolError(f"{operation!r} is a write and cannot be called as a read")
        return tool.read(operation, **args)

    # -- prepare: writes a plan, nothing else
    def prepare(
        self,
        *,
        work_item_id: str,
        decision_id: str | None,
        adapter: str,
        operation: str,
        idempotency_key: str,
        **args: Any,
    ) -> dict[str, Any]:
        tool = self.get(adapter)
        if not tool.supports(operation):
            raise ToolError(f"{adapter} does not support {operation!r}")
        if not idempotency_key:
            raise ToolError("every prepared action needs an idempotency key")

        existing = db.one(
            self.conn,
            "SELECT * FROM action_plan WHERE tenant_id = ? AND idempotency_key = ?",
            (self.cfg.tenant_id, idempotency_key),
        )
        if existing:
            # A replayed request is not a second action. It is the same one.
            return {**dict(existing), "duplicate": True}

        # Every adapter is told the work item it is acting for: a notifier needs it
        # to write the message log, and an audit needs it to join things up.
        prepared = tool.preview(operation, work_item_id=work_item_id, **args)
        plan_id = "plan_" + uuid.uuid4().hex[:12]
        row = {
            "id": plan_id,
            "tenant_id": self.cfg.tenant_id,
            "work_item_id": work_item_id,
            "decision_id": decision_id,
            "adapter": adapter,
            "operation": operation.replace("prepare_", "commit_"),
            "args_json": json.dumps(prepared.args, sort_keys=True),
            "preview_text": prepared.preview,
            "idempotency_key": idempotency_key,
            "status": "prepared",
            "prepared_at": db.now(),
        }
        db.insert(self.conn, "action_plan", row)
        return {**row, "duplicate": False}

    # -- commit: acts once
    def commit(self, plan_id: str, *, actor: str, authorised: bool) -> dict[str, Any]:
        plan = db.one(self.conn, "SELECT * FROM action_plan WHERE tenant_id = ? AND id = ?", (self.cfg.tenant_id, plan_id))
        if plan is None:
            raise ToolError(f"no such plan {plan_id!r}")
        if plan["status"] == "committed":
            # Idempotent: the same key never acts twice, it reports the first result.
            return {"already": True, **db.loads(plan["receipt_json"], {})}
        if plan["status"] != "prepared":
            raise ToolError(f"plan {plan_id} is {plan['status']}, so it cannot be committed")
        if not authorised:
            raise ToolError("this action was prepared under a decision that requires a person to approve it")

        tool = self.get(plan["adapter"])
        args = db.loads(plan["args_json"], {})
        receipt = tool.act(plan["operation"], actor=actor, idempotency_key=plan["idempotency_key"], **args)

        self.conn.execute(
            "UPDATE action_plan SET status = 'committed', committed_at = ?, receipt_json = ? WHERE id = ?",
            (db.now(), json.dumps(receipt, sort_keys=True), plan_id),
        )
        self.conn.execute(
            "INSERT INTO idempotency_ledger (tenant_id, key, plan_id, first_seen_at, result_json) VALUES (?,?,?,?,?) "
            "ON CONFLICT(tenant_id, key) DO NOTHING",
            (self.cfg.tenant_id, plan["idempotency_key"], plan_id, db.now(), json.dumps(receipt, sort_keys=True)),
        )
        return {"already": False, **receipt}

    def cancel(self, plan_id: str, reason: str) -> None:
        self.conn.execute(
            "UPDATE action_plan SET status = 'cancelled', cancelled_at = ?, receipt_json = ? "
            "WHERE tenant_id = ? AND id = ? AND status = 'prepared'",
            (db.now(), json.dumps({"cancelled_because": reason}), self.cfg.tenant_id, plan_id),
        )

    def plans_for(self, work_item_id: str) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in db.rows(
                self.conn,
                "SELECT * FROM action_plan WHERE tenant_id = ? AND work_item_id = ? ORDER BY prepared_at",
                (self.cfg.tenant_id, work_item_id),
            )
        ]


def idempotency_key(*parts: Any) -> str:
    """A stable key for one intended effect.

    The same intended effect must produce the same key, so a retry collides with
    itself instead of acting twice.
    """
    payload = "|".join(str(p) for p in parts)
    return "idem_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
