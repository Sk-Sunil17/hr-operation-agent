"""The words an outcome uses must match the outcome it is.

And: the act phase can fail, because it talks to other systems. A failure has
to be recorded and handed to a person, not raised out of the kernel leaving a
work item half-finished and an employee hearing nothing.
"""

from __future__ import annotations

import pytest

from hr_agent import audit, db, kernel, tools

from .conftest_agent import TODAY, build

# Phrasing that says a person ALREADY has the matter. Correct for a handover,
# a lie in a queue item that is waiting for someone to pick it up.
HANDOVER_LANGUAGE = ["passing this to", "has been notified", "will contact you"]


@pytest.fixture
def acme():
    return build("acme_labs")


def run(world, message, requester="emp_001", **kw):
    conn, cfg = world
    return kernel.run(conn, cfg, message=message, requester_id=requester, on=TODAY, **kw)


# ---------------------------------------------------------------- F4


EVERY_SHAPE = [
    ("How many days of annual leave do I get in a year?", "emp_001", {}),
    ("Am I entitled to carry over my unused leave?", "emp_001", {}),
    ("Please update my home address, I have moved.", "emp_001", {}),
    ("Please change Sam's phone number.", "emp_011", {}),
    ("Lena starts in three weeks, is she ready to go?", "emp_021", {}),
    ("Please schedule the interview panel for the Head of Finance role.", "emp_021", {}),
    ("Arrange a 30 minute screening call with Nils Berger (nils@example.test) next Thursday.", "emp_021", {}),
    ("Please reschedule the screening call with Nils Berger.", "emp_021", {}),
    ("Please cancel the screening call with Nils Berger.", "emp_021", {}),
    ("Here is my signed contract.", "emp_005", {"attachment": {"document_type": "contract", "complete": True}}),
    ("Here is my form.", "emp_005", {"attachment": {"document_type": "contract", "complete": False}}),
]


@pytest.mark.parametrize(("message", "who", "kw"), EVERY_SHAPE, ids=[m[:34] for m, _, _ in EVERY_SHAPE])
def test_a_prepare_outcome_is_never_worded_as_a_handover(acme, message, who, kw):
    """A queue item saying somebody already has it is a small lie that costs
    trust in everything else on the screen."""
    outcome = run(acme, message, who, **kw)
    if outcome.mode != "prepare":
        pytest.skip(f"this one reaches {outcome.mode}")
    lowered = outcome.draft.lower()
    assert not [p for p in HANDOVER_LANGUAGE if p in lowered], outcome.draft


def test_every_intent_that_prepares_has_its_own_words(acme):
    """Not the generic fallback: a prepared outcome should say what it prepared."""
    scheduling = run(acme, "Please schedule the interview panel for the Head of Finance role.", "emp_021")
    assert scheduling.mode == "prepare"
    assert "calendar" in scheduling.draft and "nothing is booked" in scheduling.draft

    document = run(acme, "Here is my signed contract.", "emp_005",
                   attachment={"document_type": "contract", "complete": True})
    assert document.mode == "prepare"
    assert "prepared it for filing" in document.draft
    assert "confirms the reading before anything is stored" in document.draft


def test_scheduling_proposes_and_books_nothing(acme):
    conn, cfg = acme
    outcome = run(acme, "Please schedule the interview panel for the Head of Finance role.", "emp_021")
    invites = [p for p in outcome.plans if p["operation"] == "commit_invite"]
    assert invites, "a time was proposed, so a hold should be prepared"
    assert all(p["status"] == "prepared" for p in invites)
    assert "not in anyone's calendar until a person approves" in outcome.draft


def test_a_document_that_did_not_arrive_says_so(acme):
    """Reaching the catch-all here tells the person 'nothing matched', when the
    one thing they can fix is that they forgot the attachment."""
    outcome = run(acme, "I have attached my completed new starter form.", "emp_005")
    assert outcome.rule_id == "D0"
    assert "no document arrived" in outcome.draft


def test_a_restricted_document_is_never_read(acme):
    outcome = run(acme, "Here is my medical certificate.", "emp_005",
                  attachment={"document_type": "restricted", "complete": True})
    assert outcome.rule_id == "D1"
    assert outcome.mode == "hand_over"
    assert not [p for p in outcome.plans if p["operation"] == "commit_file"]


# ---------------------------------------------------------------- F5


class Boom(RuntimeError):
    pass


def test_a_system_that_fails_is_handed_over_not_swallowed(acme, monkeypatch):
    conn, cfg = acme
    monkeypatch.setattr(
        tools.Registry, "commit", lambda *a, **k: (_ for _ in ()).throw(Boom("the HR system timed out"))
    )
    outcome = run(acme, "How many days of annual leave do I get in a year?")
    assert outcome.status == "failed"
    assert outcome.mode == "hand_over"
    assert any("did not respond" in reason for reason in outcome.escalated_because)
    assert "could not finish this one" in outcome.draft.lower()
    assert "nothing has been changed" in outcome.draft.lower()


def test_a_failure_is_written_to_the_trail(acme, monkeypatch):
    """A trail that stops mid-sentence is worse than no trail."""
    conn, cfg = acme
    monkeypatch.setattr(tools.Registry, "commit", lambda *a, **k: (_ for _ in ()).throw(Boom("timeout")))
    outcome = run(acme, "How many days of annual leave do I get in a year?")
    stages = [r["stage"] for r in audit.trail(conn, cfg.tenant_id, outcome.work_item_id)]
    assert "act" in stages, "the act stage must be recorded even when it failed"
    row = next(r for r in audit.trail(conn, cfg.tenant_id, outcome.work_item_id) if r["stage"] == "act")
    assert "did not respond" in row["action"]
    assert audit.verify(conn).ok


def test_a_failure_leaves_nothing_prepared_behind(acme, monkeypatch):
    conn, cfg = acme
    monkeypatch.setattr(tools.Registry, "commit", lambda *a, **k: (_ for _ in ()).throw(Boom("timeout")))
    outcome = run(acme, "Please update my home address, I have moved.")
    left = [p for p in outcome.plans if p["status"] == "prepared"]
    assert left == [], "a half-finished request must not leave actions waiting to fire"
    assert db.rows(conn, "SELECT * FROM adapter_record") == []


def test_a_failure_does_not_look_like_a_refusal(acme, monkeypatch):
    """The employee must be able to tell 'we could not' from 'we will not'."""
    conn, cfg = acme
    refusal = run(acme, "What is Priya's home address?").draft.lower()
    monkeypatch.setattr(tools.Registry, "commit", lambda *a, **k: (_ for _ in ()).throw(Boom("timeout")))
    failure = run(acme, "How many days of annual leave do I get in a year?").draft.lower()
    assert "not able to do this" in refusal
    assert "not able to do this" not in failure
    assert "did not respond" in failure


# ---------------------------------------------------------------- unclear requests


def test_an_unclear_request_arrives_with_a_question_ready_to_send(acme):
    """Previously these fell to the catch-all and a person got a shrug."""
    outcome = run(acme, "something about leave")
    assert outcome.rule_id == "E4"
    assert outcome.mode == "prepare"
    assert "A question I could send, if you approve it" in outcome.draft
    assert "leave" in outcome.draft


def test_the_agent_does_not_ask_the_question_itself(acme):
    """Asking is an outbound message. Letting a low-confidence path send one on
    its own would widen what the agent may do without a person."""
    conn, cfg = acme
    outcome = run(acme, "something about leave")
    sent = [p for p in outcome.plans if p["status"] == "committed"]
    assert all("question I could send" not in db.loads(p["args_json"], {}).get("body", "") for p in sent)


def test_an_unclear_restricted_message_is_still_never_asked_a_question(acme):
    """Restricted is decided before clarity. Asking a distressed person to
    explain themselves to a machine is its own small cruelty."""
    outcome = run(acme, "help, something happened with my manager and I am being bullied")
    assert outcome.rule_id == "R1"
    assert "?" not in outcome.draft


def test_an_unclear_request_about_someone_else_is_still_refused(acme):
    outcome = run(acme, "something about Priya", "emp_001")
    assert outcome.rule_id == "W1"
