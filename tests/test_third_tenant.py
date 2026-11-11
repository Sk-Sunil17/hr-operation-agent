"""The adaptability claim, as a test rather than an assertion.

A third company was brought up entirely as configuration. If expressing it had
needed kernel code, the abstraction would have failed -- and it is far cheaper
to find that at customer three than at customer thirty.

It did surface one defect: retrieval counted a section as an answer on a single
common term, so "What is the parking arrangement?" matched a leave policy on the
word "arranged". That was a pre-existing bug affecting every company, not
something Harbour needed special handling for, and fixing it improved Acme too.
Finding exactly that kind of thing is what a third company is for.
"""

from __future__ import annotations

from datetime import date

import pytest

from hr_agent import engine, kernel, policy
from hr_agent.packs import loader
from hr_agent.packs.validate import validate

from .conftest_agent import build

TODAY = date(2026, 11, 2)
ALL = ["acme_labs", "meridian_group", "harbour_clinic"]


def test_the_third_company_is_configuration_only():
    """Nothing about Harbour appears anywhere in the kernel."""
    from pathlib import Path

    for path in Path("hr_agent").rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for word in ("harbour", "clinic", "charge_nurse", "practice_manager", "people_officer"):
            assert word not in text, f"{path} mentions {word!r}"


@pytest.mark.parametrize("tenant_id", ALL)
def test_every_company_validates_clean(tenant_id):
    cfg = loader.load_resolved(tenant_id)
    assert [p for p in validate(cfg) if p.severity == "error"] == []


@pytest.mark.parametrize("tenant_id", ALL)
def test_every_company_passes_its_own_golden_cases(tenant_id):
    conn, cfg = build(tenant_id)
    misses = []
    for case in cfg.golden_cases:
        outcome = kernel.run(conn, cfg, message=case.message, requester_id=case.requester,
                             attachment=case.attachment, on=TODAY)
        if outcome.rule_id != case.expect_rule or outcome.mode != case.expect_mode:
            misses.append(f"{case.id}: wanted {case.expect_rule}/{case.expect_mode}, got {outcome.rule_id}/{outcome.mode}")
    assert misses == []


def test_the_three_companies_are_genuinely_different():
    """If the seed companies drift together they stop testing anything."""
    configs = [loader.load_resolved(t) for t in ALL]
    assert len({c.config_hash for c in configs}) == 3
    assert len({c.locale.timezone for c in configs}) == 3
    assert len({frozenset(c.approvals.roles) for c in configs}) == 3
    assert len({c.manifest.tone for c in configs}) >= 2
    # No role name is shared by all three: nothing in the kernel can assume one.
    shared = set.intersection(*[set(c.approvals.roles) for c in configs])
    assert shared == set(), f"all three share role(s): {shared}"


def test_the_same_question_reaches_three_different_places():
    question = "How much annual leave do I get?"
    seen = {}
    for tenant_id, who in (("acme_labs", "emp_001"), ("meridian_group", "mg_1001"), ("harbour_clinic", "hc_101")):
        conn, cfg = build(tenant_id)
        outcome = kernel.run(conn, cfg, message=question, requester_id=who, on=TODAY)
        seen[tenant_id] = (outcome.rule_id, outcome.mode)
    assert seen["acme_labs"] == ("E1", "auto")
    assert seen["meridian_group"] == ("E1", "prepare")
    assert seen["harbour_clinic"] == ("E1", "prepare")


def test_harbour_lets_nothing_act_alone():
    """This company tightened every rule that could answer by itself."""
    cfg = loader.load_resolved("harbour_clinic")
    base = loader.load_base()
    acting_alone = [r.id for r in cfg.rules if r.mode == "auto"]
    assert acting_alone == [], f"expected nothing to act alone, found {acting_alone}"
    assert any(r.mode == "auto" for r in base.rules), "the base pack does allow it"


def test_harbour_segments_tighten_on_top():
    cfg = loader.load_resolved("harbour_clinic")
    union = engine.segments_for(cfg, next(p for p in cfg.people if p.id == "hc_101"))
    assert "union_members" in union
    tightened = loader.rules_for_segment(cfg, union)
    assert next(r for r in tightened if r.id == "A1").mode == "hand_over"
    assert next(r for r in cfg.rules if r.id == "A1").mode == "prepare"


def test_harbour_reads_its_own_forms_and_nobody_elses():
    from hr_agent import forms

    harbour = loader.load_resolved("harbour_clinic")
    acme = loader.load_resolved("acme_labs")
    assert forms.detect("Clinical reference pack\nName: X", harbour).id == "reference_pack"
    assert forms.detect("Clinical reference pack\nName: X", acme) is None
    assert forms.detect("New starter form\nName: X", harbour) is None


def test_harbour_masks_its_own_identifier_formats():
    from hr_agent import redact

    harbour = redact.Redactor(loader.load_resolved("harbour_clinic").redaction)
    acme = redact.Redactor(loader.load_resolved("acme_labs").redaction)
    assert harbour.text("REG-123456-NS") == "[registration number]"
    assert acme.text("REG-123456-NS") == "REG-123456-NS"
    assert acme.text("PR-456123") == "[payroll reference]"
    assert harbour.text("PR-456123") == "PR-456123"


# ---------------------------------------------------------------- the bug it found


@pytest.mark.parametrize("tenant_id", ALL)
def test_one_common_word_is_not_an_answer(tenant_id):
    """Relevance is decided in retrieval, because the verify gate cannot.

    The gate checks a citation is real, current and quoted accurately. It has
    no way to know the passage does not answer the question.
    """
    conn, cfg = build(tenant_id)
    for question in (
        "What is the parking arrangement at the north site?",
        "What is the gym reimbursement here?",
        "Do we sponsor conference tickets?",
    ):
        probe = policy.search(conn, cfg, question, person=cfg.people[0])
        assert not probe.found, f"{tenant_id} answered {question!r} from {probe.citations[0].doc_id if probe.citations else '?'}"


def test_a_real_question_still_finds_its_policy():
    """The precision fix must not have made retrieval useless."""
    for tenant_id, question in (
        ("acme_labs", "How many days of annual leave do I get in a year?"),
        ("meridian_group", "How many days of annual leave am I entitled to?"),
        ("harbour_clinic", "How much annual leave do I accrue each month?"),
    ):
        conn, cfg = build(tenant_id)
        probe = policy.search(conn, cfg, question, person=cfg.people[0])
        assert probe.found and len(probe.citations[0].matched_terms) >= policy.MIN_TERMS


@pytest.mark.parametrize("tenant_id", ALL)
def test_a_golden_case_that_prepares_a_record_change_actually_prepares_one(tenant_id):
    """A case whose mode is `prepare` must have something to approve.

    The agent will not invent a value, so a request that never says what to
    change the field TO correctly prepares nothing. That is right, and it made
    six of the seven shipped record-change cases silently stop producing a
    preview -- including one whose own note claimed "a real write: prepared with
    a preview". The demo's central moment was a presenter saying "watch the
    record change" over a queue item that would change nothing.
    """
    conn, cfg = build(tenant_id)
    empty = []
    for case in cfg.golden_cases:
        if case.expect_mode != "prepare":
            continue
        outcome = kernel.run(conn, cfg, message=case.message, requester_id=case.requester,
                             attachment=case.attachment, on=TODAY)
        if outcome.rule_id not in {"A1", "A3", "A4"}:
            continue
        if not [p for p in outcome.plans if "record" in p["operation"]]:
            empty.append(f"{case.id} ({case.message!r}) prepares no record change")
    assert empty == []


@pytest.mark.parametrize("tenant_id", ALL)
def test_every_golden_case_passes_its_own_verify_gate(tenant_id):
    """A seeded demo must not contain an item that failed verification.

    A company's own acceptance cases are what fills the screens before anyone
    speaks. One of them escalating because the gate tripped is a defect the
    presenter finds in front of the room.
    """
    conn, cfg = build(tenant_id)
    failed = []
    for case in cfg.golden_cases:
        outcome = kernel.run(conn, cfg, message=case.message, requester_id=case.requester,
                             attachment=case.attachment, on=TODAY)
        if not outcome.verify_passed:
            detail = "; ".join(c["detail"] for c in outcome.checks if c["status"] == "fail")
            failed.append(f"{case.id}: {detail}")
    assert failed == []
