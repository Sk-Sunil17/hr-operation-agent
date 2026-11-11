"""The approval queue: where "prepared" becomes "done", and only there.

The rules this enforces are the ones a company is actually audited on:

  * Only the assigned role may decide, or a role the pack says may deputise.
  * Nobody decides their own request.
  * A citation is re-checked at the moment of approval. A draft written last week
    against a policy that has since changed is blocked, not sent.
  * Approving commits every prepared action, once, under its key. Sending back
    cancels them. Taking over cancels them and stops the agent on that case.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any

from . import audit, db, kernel, policy, tools
from .packs.models import ResolvedConfig
from .policy import Citation
from .redact import Redactor

OUTCOMES = ("approved", "sent_back", "taken_over")

# Why a draft was rejected, as a short fixed list rather than free text.
#
# Free text is unreadable in bulk: nobody will ever read a thousand notes, so
# the reason a draft was wrong is lost the moment it is written. A code turns
# every rejection into a labelled error class, which is what makes the
# human-verdict columns usable -- and it tells you WHICH part of the agent is
# wrong, which free text almost never does.
REASON_CODES: dict[str, str] = {
    "wrong_policy": "Cited the wrong policy, or the wrong section",
    "wrong_person": "About the wrong person, or the wrong record",
    "missing_information": "The reply left out something it needed to say",
    "factually_wrong": "Says something that is not true",
    "tone": "Accurate, but badly put for this situation",
    "needs_a_conversation": "Correct, but this should not be answered in writing",
    "not_needed": "Nothing needed to be sent at all",
    "other": "Something else (explain in the note)",
}

# Which part of the agent each code points at. Reported in metrics, so a pattern
# of rejections leads somewhere rather than just accumulating.
REASON_POINTS_AT: dict[str, str] = {
    "wrong_policy": "retrieval",
    "wrong_person": "facts",
    "missing_information": "drafting",
    "factually_wrong": "retrieval",
    "tone": "drafting",
    "needs_a_conversation": "the rules: this case may need to be tightened",
    "not_needed": "the rules: this case may not need the agent at all",
    "other": "unclassified",
}


class ApprovalError(RuntimeError):
    """The decision cannot be made as asked."""


@dataclass
class Decided:
    approval_id: str
    outcome: str
    committed: list[str]
    cancelled: list[str]
    note: str | None
    blocked: list[str]
    reason_code: str | None = None


def queue(conn: sqlite3.Connection, cfg: ResolvedConfig, *, role: str | None = None, actor_id: str | None = None) -> list[dict[str, Any]]:
    """Open approvals, filtered to what this person may actually decide."""
    rows = db.rows(
        conn,
        "SELECT a.*, w.intent, w.sensitivity, w.rule_id, w.requester_id, w.created_at AS asked_at, "
        "d.draft_text, d.rule_reason, d.verify_passed "
        "FROM approval a JOIN work_item w ON w.id = a.work_item_id "
        "JOIN decision d ON d.id = a.decision_id "
        "WHERE a.tenant_id = ? AND a.status = 'open' ORDER BY a.opened_at",
        (cfg.tenant_id,),
    )
    out = []
    for row in rows:
        item = dict(row)
        item["citations"] = [
            dict(c) for c in db.rows(
                conn, "SELECT * FROM citation WHERE tenant_id = ? AND decision_id = ?",
                (cfg.tenant_id, row["decision_id"]),
            )
        ]
        item["plans"] = [
            dict(p) for p in db.rows(
                conn, "SELECT * FROM action_plan WHERE tenant_id = ? AND work_item_id = ? AND status = 'prepared'",
                (cfg.tenant_id, row["work_item_id"]),
            )
        ]
        item["mine"] = can_decide(cfg, role=role, actor_id=actor_id, approval=item) is None
        out.append(item)

    if role is None and actor_id is None:
        return out
    # Other roles' work is listed but not opened: a queue that hides its size is
    # hard to reason about, and a queue that shows everything leaks drafts.
    for item in out:
        if not item["mine"]:
            item["draft_text"] = None
            item["citations"] = []
            item["plans"] = []
    return out


def roles_of(cfg: ResolvedConfig, actor_id: str | None) -> set[str]:
    person = next((p for p in cfg.people if p.id == actor_id), None)
    if person is None:
        return set()
    held = set(person.roles)
    # Delegation: a deputy may decide what the holder would have decided.
    for holder, deputies in cfg.approvals.delegation.items():
        if held & set(deputies):
            held.add(holder)
    return held


def can_decide(cfg: ResolvedConfig, *, role: str | None, actor_id: str | None, approval: dict[str, Any]) -> str | None:
    """None if allowed, otherwise the reason it is refused."""
    if approval.get("status") not in (None, "open"):
        return "this approval has already been decided"
    if actor_id and approval.get("requested_by") == actor_id:
        return "nobody decides their own request"
    held = roles_of(cfg, actor_id) if actor_id else ({role} if role else set())
    if not held:
        return "this account holds no role that can decide anything"
    if approval["assigned_role"] not in held:
        return f"this is assigned to {approval['assigned_role']}, which this account does not hold"
    return None


def decide(
    conn: sqlite3.Connection,
    cfg: ResolvedConfig,
    *,
    approval_id: str,
    actor_id: str,
    outcome: str,
    note: str | None = None,
    reason_code: str | None = None,
    on: date | None = None,
) -> Decided:
    """Approve, send back, or take over. Everything else is a refusal."""
    if outcome not in OUTCOMES:
        raise ApprovalError(f"unknown outcome {outcome!r}; expected one of {', '.join(OUTCOMES)}")
    if reason_code is not None and reason_code not in REASON_CODES:
        raise ApprovalError(
            f"unknown reason {reason_code!r}; expected one of {', '.join(REASON_CODES)}"
        )
    if outcome == "sent_back" and reason_code is None:
        raise ApprovalError(
            "sending something back needs a reason code as well as a note, so the pattern "
            f"can be counted later. Expected one of: {', '.join(REASON_CODES)}"
        )

    row = db.one(conn, "SELECT * FROM approval WHERE tenant_id = ? AND id = ?", (cfg.tenant_id, approval_id))
    if row is None:
        raise ApprovalError(f"no such approval {approval_id!r}")
    approval = dict(row)
    if approval["status"] != "open":
        raise ApprovalError(f"this approval was already {approval['outcome']}")
    if reason := can_decide(cfg, role=None, actor_id=actor_id, approval=approval):
        raise ApprovalError(reason)

    redactor = Redactor(cfg.redaction)
    registry = tools.Registry(conn, cfg)
    item = db.one(conn, "SELECT * FROM work_item WHERE id = ?", (approval["work_item_id"],))
    subject = next((p for p in cfg.people if p.id == item["requester_id"]), None)
    prepared = [
        dict(p) for p in db.rows(
            conn, "SELECT * FROM action_plan WHERE tenant_id = ? AND work_item_id = ? AND status = 'prepared'",
            (cfg.tenant_id, approval["work_item_id"]),
        )
    ]

    def write_audit(stage: str, action: str, detail: Any = None) -> None:
        audit.append(
            conn, tenant_id=cfg.tenant_id, actor=actor_id, actor_kind="person", stage=stage, action=action,
            redactor=redactor, work_item_id=approval["work_item_id"], detail=detail, config_hash=cfg.config_hash,
        )

    committed: list[str] = []
    cancelled: list[str] = []
    blocked: list[str] = []

    if outcome == "approved":
        # The citation is re-checked NOW. A policy may have changed since the draft.
        citations = [
            Citation(
                doc_id=c["doc_id"], doc_title=c["doc_id"], version=c["version"],
                section_id=c["section_id"], heading="", quoted_text=c["quoted_text"],
            )
            for c in db.rows(
                conn, "SELECT * FROM citation WHERE tenant_id = ? AND decision_id = ?",
                (cfg.tenant_id, approval["decision_id"]),
            )
        ]
        current = kernel.in_force_map(cfg, person=subject, on=on or date.today())
        stale = [c for c in citations if current.get(c.doc_id) != c.version]
        if stale:
            blocked = [f"{c.doc_id} v{c.version} is no longer the version in force" for c in stale]
            write_audit("verify", "approval blocked: the cited policy changed since this was drafted",
                        {"stale": blocked})
            raise ApprovalError(
                "the policy changed since this was drafted: " + "; ".join(blocked)
                + ". Run it again so the reply quotes the current version."
            )

        for plan in prepared:
            registry.commit(plan["id"], actor=actor_id, authorised=True)
            committed.append(plan["id"])
        conn.execute(
            "UPDATE work_item SET status = 'answered', closed_at = ? WHERE id = ?",
            (db.now(), approval["work_item_id"]),
        )
        write_audit("act", f"approved and sent: {len(committed)} action(s) committed", {"note": note})

    elif outcome == "sent_back":
        if not note:
            raise ApprovalError("sending something back needs a reason the requester can read")
        for plan in prepared:
            registry.cancel(plan["id"], f"sent back: {note}")
            cancelled.append(plan["id"])
        conn.execute("UPDATE work_item SET status = 'open', closed_at = NULL WHERE id = ?", (approval["work_item_id"],))
        write_audit(
            "act", f"sent back ({REASON_CODES[reason_code]}), nothing was committed",
            {"note": note, "reason_code": reason_code, "points_at": REASON_POINTS_AT[reason_code]},
        )

    else:  # taken_over
        for plan in prepared:
            registry.cancel(plan["id"], "a person took this over")
            cancelled.append(plan["id"])
        case_id = item["case_id"]
        if not case_id:
            case_id = kernel._new_id("case")
            db.insert(conn, "hr_case", {
                "id": case_id, "tenant_id": cfg.tenant_id, "kind": "unresolved",
                "subject_id": item["requester_id"], "owner_role": None, "owner_id": actor_id,
                "status": "open", "taken_over": 1, "opened_at": db.now(),
            })
            conn.execute("UPDATE work_item SET case_id = ? WHERE id = ?", (case_id, approval["work_item_id"]))
        else:
            conn.execute("UPDATE hr_case SET taken_over = 1, owner_id = ? WHERE id = ?", (actor_id, case_id))
        conn.execute(
            "UPDATE work_item SET status = 'handed_over', closed_at = ? WHERE id = ?",
            (db.now(), approval["work_item_id"]),
        )
        write_audit("handover", "taken over by a person; the agent stops on this case", {"note": note})

    conn.execute(
        "UPDATE approval SET status = 'decided', outcome = ?, note = ?, decided_at = ?, decided_by = ? WHERE id = ?",
        (outcome, note, db.now(), actor_id, approval_id),
    )
    conn.execute(
        "UPDATE decision SET human_verdict = ?, human_actor_id = ?, human_note = ?, "
        "human_reason_code = ?, decided_at = ? WHERE id = ?",
        (outcome, actor_id, note, reason_code, db.now(), approval["decision_id"]),
    )
    return Decided(approval_id, outcome, committed, cancelled, note, blocked, reason_code)


def record_edit(conn: sqlite3.Connection, cfg: ResolvedConfig, *, decision_id: str, edited: str, actor_id: str) -> None:
    """An approver changed the wording. This is the preference pair from the plan's
    flywheel: the draft was rejected, the edit was chosen."""
    row = db.one(conn, "SELECT draft_text FROM decision WHERE tenant_id = ? AND id = ?", (cfg.tenant_id, decision_id))
    if row is None:
        raise ApprovalError(f"no such decision {decision_id!r}")
    conn.execute(
        "UPDATE decision SET human_verdict = 'edited', human_actor_id = ?, edit_diff = ?, decided_at = ? WHERE id = ?",
        (actor_id, edited, db.now(), decision_id),
    )
