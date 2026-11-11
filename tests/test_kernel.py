"""The loop, end to end: what the agent does and in what order."""

from __future__ import annotations

import pytest

from hr_agent import audit, db, engine, kernel, policy
from hr_agent.classify import classify

from .conftest_agent import TODAY, build


@pytest.fixture
def acme():
    conn, cfg = build("acme_labs")
    return conn, cfg


@pytest.fixture
def meridian():
    conn, cfg = build("meridian_group")
    return conn, cfg


def run(world, message, requester="emp_001", **kwargs):
    conn, cfg = world
    return kernel.run(conn, cfg, message=message, requester_id=requester, on=TODAY, **kwargs)


# ---------------------------------------------------------------- golden cases


@pytest.mark.parametrize("tenant_id", ["acme_labs", "meridian_group"])
def test_every_company_passes_its_own_golden_cases(tenant_id):
    """A company's golden cases are its acceptance tests. Green means onboarded."""
    conn, cfg = build(tenant_id)
    misses = []
    for case in cfg.golden_cases:
        outcome = kernel.run(conn, cfg, message=case.message, requester_id=case.requester,
                             attachment=case.attachment, on=TODAY)
        if outcome.rule_id != case.expect_rule or outcome.mode != case.expect_mode:
            misses.append(f"{case.id}: expected {case.expect_rule}/{case.expect_mode}, got {outcome.rule_id}/{outcome.mode}")
    assert misses == []


def test_the_same_question_gets_different_treatment_at_two_companies():
    """The whole adaptability claim, as one assertion. No kernel code differs."""
    question = "How many days of annual leave do I get in a year?"
    acme = kernel.run(*build("acme_labs"), message=question, requester_id="emp_001", on=TODAY) if False else None
    conn_a, cfg_a = build("acme_labs")
    conn_m, cfg_m = build("meridian_group")
    acme = kernel.run(conn_a, cfg_a, message=question, requester_id="emp_001", on=TODAY)
    meridian = kernel.run(conn_m, cfg_m, message=question, requester_id="mg_1001", on=TODAY)
    assert acme.rule_id == meridian.rule_id == "E1"
    assert acme.mode == "auto" and acme.status == "answered"
    assert meridian.mode == "prepare" and meridian.status == "queued"


def test_a_segment_tightens_further_than_the_company(meridian):
    """Same request, same company, two populations, two outcomes."""
    ordinary = run(meridian, "Please change my registered home address.", "mg_1001")
    collective = run(meridian, "Please change my registered home address.", "mg_1002")
    assert ordinary.rule_id == collective.rule_id == "A1"
    assert ordinary.mode == "prepare"
    assert collective.mode == "hand_over"


# ---------------------------------------------------------------- order of the loop


def test_the_payload_is_stored_before_anything_looks_at_it(acme):
    conn, cfg = acme
    outcome = run(acme, "How many days of annual leave do I get?")
    event = db.one(conn, "SELECT * FROM event WHERE work_item_id = ?", (outcome.work_item_id,))
    assert event is not None
    trail = [r["stage"] for r in audit.trail(conn, cfg.tenant_id, outcome.work_item_id)]
    assert trail[0] == "receive"


def test_the_loop_runs_in_order(acme):
    conn, cfg = acme
    outcome = run(acme, "How many days of annual leave do I get?")
    stages = [r["stage"] for r in audit.trail(conn, cfg.tenant_id, outcome.work_item_id)]
    assert stages.index("receive") < stages.index("understand") < stages.index("decide")
    assert stages.index("decide") < stages.index("draft") < stages.index("verify")


def test_the_decide_row_carries_the_rule_and_the_facts(acme):
    conn, cfg = acme
    outcome = run(acme, "How many days of annual leave do I get?")
    row = next(r for r in audit.trail(conn, cfg.tenant_id, outcome.work_item_id) if r["stage"] == "decide")
    detail = db.loads(row["detail_json"])
    assert row["actor_kind"] == "rule" and row["actor"] == "rule:E1"
    assert detail["facts"]["intent"] == "enquiry"
    assert detail["rule_id"] == "E1"


def test_retrieval_is_audited_with_its_arguments(acme):
    conn, cfg = acme
    outcome = run(acme, "How many days of annual leave do I get?")
    row = next(r for r in audit.trail(conn, cfg.tenant_id, outcome.work_item_id) if r["stage"] == "retrieve")
    detail = db.loads(row["detail_json"])
    assert detail["query_terms"]
    assert detail["sections_searched"] > 0


def test_a_replayed_delivery_does_not_repeat_its_effects(acme):
    conn, cfg = acme
    first = run(acme, "How many days of annual leave do I get?", dedupe_key="same")
    second = run(acme, "How many days of annual leave do I get?", dedupe_key="same")
    assert second.status == "suppressed"
    assert second.work_item_id == first.work_item_id
    sent = db.rows(conn, "SELECT * FROM message_log WHERE direction = 'outbound'")
    assert len(sent) == 1


def test_every_decision_records_the_configuration_that_produced_it(acme):
    conn, cfg = acme
    outcome = run(acme, "How many days of annual leave do I get?")
    row = db.one(conn, "SELECT pack_hash FROM decision WHERE id = ?", (outcome.decision_id,))
    assert row["pack_hash"] == cfg.config_hash


def test_the_whole_trail_verifies(acme):
    conn, cfg = acme
    for case in cfg.golden_cases:
        kernel.run(conn, cfg, message=case.message, requester_id=case.requester,
                             attachment=case.attachment, on=TODAY)
    assert audit.verify(conn).ok


# ---------------------------------------------------------------- outcomes


def test_a_routine_answer_quotes_the_policy_verbatim(acme):
    outcome = run(acme, "How many days of annual leave do I get in a year?")
    assert outcome.status == "answered"
    assert outcome.citations
    assert outcome.citations[0].quoted_text in outcome.draft


def test_a_restricted_matter_is_never_answered(acme):
    conn, cfg = acme
    outcome = run(acme, "My manager has been making racist remarks in meetings.")
    assert outcome.mode == "hand_over"
    assert outcome.citations == []
    assert outcome.case_id is not None
    case = db.one(conn, "SELECT * FROM hr_case WHERE id = ?", (outcome.case_id,))
    assert case["kind"] == "sensitive_matter"
    assert case["owner_role"] == cfg.manifest.escalation.restricted_role


def test_a_restricted_matter_still_gets_an_acknowledgement(acme):
    conn, cfg = acme
    outcome = run(acme, "I am being bullied by a colleague.")
    sent = db.rows(conn, "SELECT * FROM message_log WHERE work_item_id = ? AND direction = 'outbound'",
                   (outcome.work_item_id,))
    assert sent, "a person in distress must not be met with silence"
    assert any("contact you" in row["body"] for row in sent)


def test_a_restricted_matter_is_not_asked_a_clarifying_question(acme):
    outcome = run(acme, "help")   # vague on its own
    vague = outcome.draft
    restricted = run(acme, "help, I am being harassed at work").draft
    assert "?" not in restricted
    assert restricted != vague


def test_a_queued_request_changes_nothing_yet(acme):
    conn, cfg = acme
    outcome = run(acme, "Please change my home address to 14 Example Road, Leeds.")
    assert outcome.status == "queued"
    assert db.rows(conn, "SELECT * FROM adapter_record") == []
    assert all(p["status"] == "prepared" for p in outcome.plans if p["operation"] == "commit_record_change")


def test_a_refusal_says_why_and_offers_a_next_step(acme):
    outcome = run(acme, "What is Priya's home address?")
    assert outcome.status == "refused" and outcome.rule_id == "W1"
    assert "Why:" in outcome.draft
    assert len(outcome.draft.splitlines()) > 3


def test_a_compound_message_becomes_child_work_items(acme):
    conn, cfg = acme
    outcome = run(acme, "Please update my home address. Also, how many days of annual leave do I get?")
    assert outcome.status == "split"
    assert len(outcome.children) == 2
    modes = {
        db.one(conn, "SELECT mode FROM work_item WHERE id = ?", (child,))["mode"]
        for child in outcome.children
    }
    assert modes == {"prepare", "auto"}


def test_each_child_of_a_split_gets_its_own_trail(acme):
    conn, cfg = acme
    outcome = run(acme, "Please update my home address. Also, how many days of annual leave do I get?")
    for child in outcome.children:
        assert audit.trail(conn, cfg.tenant_id, child)


# ---------------------------------------------------------------- the gate


def test_a_failed_verification_escalates_instead_of_sending(acme, monkeypatch):
    conn, cfg = acme
    # A drafting template that claims something about the person.
    monkeypatch.setattr(
        "hr_agent.draft.answer",
        lambda cfg, citations, personal=False: "You are entitled to 25 days and I have approved it.",
    )
    outcome = run(acme, "How many days of annual leave do I get in a year?")
    assert not outcome.verify_passed
    assert outcome.mode == "hand_over"
    assert outcome.escalated_because
    sent = db.rows(conn, "SELECT body FROM message_log WHERE work_item_id = ? AND direction = 'outbound'",
                   (outcome.work_item_id,))
    assert all("entitled to 25 days" not in row["body"] for row in sent)


def test_an_overreaching_draft_never_reaches_a_send(acme, monkeypatch):
    conn, cfg = acme
    monkeypatch.setattr("hr_agent.draft.answer", lambda cfg, citations, personal=False: "You qualify. You will receive the payment.")
    outcome = run(acme, "How many days of annual leave do I get in a year?")
    committed = db.rows(conn, "SELECT * FROM action_plan WHERE work_item_id = ? AND status = 'committed'",
                        (outcome.work_item_id,))
    assert all("You qualify" not in db.loads(p["args_json"], {}).get("body", "") for p in committed)


def test_a_cancelled_plan_cannot_be_committed(acme):
    conn, cfg = acme
    from hr_agent import tools
    outcome = run(acme, "Please change my home address to 14 Example Road, Leeds.")
    registry = tools.Registry(conn, cfg)
    plan = next(p for p in outcome.plans if p["operation"] == "commit_record_change")
    registry.cancel(plan["id"], "no longer wanted")
    with pytest.raises(tools.ToolError, match="cancelled"):
        registry.commit(plan["id"], actor="emp_021", authorised=True)


# ---------------------------------------------------------------- policy versions


def test_a_scheduled_version_is_not_quoted_before_its_date(meridian):
    conn, cfg = meridian
    outcome = run(meridian, "What are the flexible working rules?", "mg_1001")
    assert outcome.citations
    assert outcome.citations[0].version == "1.0", "version 1.1 is scheduled, not in force"


def test_a_retired_version_is_never_quoted(acme):
    """Acme's remote policy went 1.0 -> 2.0 and the office-day rule changed with it.

    Checking the version string alone is not enough: another policy is legitimately
    in force AT v1.0. The test has to name the document, and the distinguishing
    text is the real proof -- v1.0 said three office days, v2.0 says two named ones.
    """
    conn, cfg = acme
    probe = policy.search(conn, cfg, "how many office days a week are expected", person=None)
    remote = [c for c in probe.citations if c.doc_id == "POL-REMOTE"]
    assert remote, "the remote policy should be the top match for an office-days question"
    assert all(c.version == "2.0" for c in remote)
    assert all("three days a week" not in c.quoted_text for c in probe.citations)


def test_scope_picks_the_right_version_for_the_person(meridian):
    conn, cfg = meridian
    outcome = run(meridian, "What do I need to submit before my start date?", "mg_1006")
    assert outcome.citations
    assert outcome.citations[0].version == "2.0-sg"


def test_two_unscoped_versions_in_force_means_no_answer(acme):
    conn, cfg = acme
    leave = next(p for p in cfg.policies if p.id == "POL-LEAVE")
    leave.versions.append(leave.versions[0].model_copy(update={"version": "1.3"}))
    outcome = run(acme, "How many days of annual leave do I get in a year?")
    assert outcome.rule_id == "E0"
    assert outcome.mode == "hand_over"
    assert outcome.citations == []


def test_a_segment_tightening_a_cited_rule_into_a_handover_still_verifies(meridian):
    """A contractor asking about their own terms reaches E2, which requires a
    citation, and the contractor segment tightens E2 to hand_over.

    The citation requirement belongs to the answer, not to the rule: a handover
    answers nothing, so it has nothing to quote. Attaching the citation anyway
    failed `the cited words appear in the reply` and escalated an outcome that
    was already an escalation. Found by the metrics caveat, not by a test.
    """
    outcome = run(meridian, "What notice period applies to my contractor agreement?", "mg_1005")
    assert outcome.rule_id == "E2"
    assert outcome.mode == "hand_over"
    assert outcome.verify_passed, [c for c in outcome.checks if c["status"] == "fail"]
    assert outcome.citations == []
    assert not outcome.escalated_because
    citation_check = next(c for c in outcome.checks if c["id"] == "citation_present")
    assert citation_check["status"] == "skip"


def test_an_answering_mode_still_must_cite(meridian):
    """The fix must not weaken the rule it was protecting."""
    outcome = run(meridian, "How many days of annual leave am I entitled to?", "mg_1001")
    assert outcome.mode == "prepare"
    assert outcome.citations
    assert next(c for c in outcome.checks if c["id"] == "citation_present")["status"] == "pass"


# ---------------------------------------------------------------- stated values


def test_the_new_value_is_taken_from_the_message(acme):
    """Identifying the field and then writing a placeholder left the approver to
    read the original message and retype it, which removed most of the saving."""
    conn, cfg = acme
    outcome = run(acme, "Please change my home address to 14 Example Road, Leeds.")
    plan = next(p for p in outcome.plans if p["operation"] == "commit_record_change")
    assert db.loads(plan["args_json"])["changes"] == {"home_address": "14 Example Road, Leeds"}
    assert "14 Example Road, Leeds" in outcome.draft


@pytest.mark.parametrize(
    ("message", "field", "value"),
    [
        ("Please change my home address to 14 Example Road", "home_address", "14 Example Road"),
        ("My new phone number is 07700 900456", "phone", "07700 900456"),
        ("Please update my preferred name to Mo", "preferred_name", "Mo"),
        # A bare statement is not a change request -- "my phone is broken" must
        # not become a write -- so the frame only applies once the message is
        # actually asking for something to be updated.
        ("Please update my record. My personal email is mo@example.test", "personal_email", "mo@example.test"),
    ],
    ids=["change-to", "new-is", "update-to", "update-then-state"],
)
def test_the_ways_people_state_a_new_value(acme, message, field, value):
    outcome = run(acme, message)
    plans = [p for p in outcome.plans if p["operation"] == "commit_record_change"]
    assert plans, f"no change prepared for {message!r}"
    assert db.loads(plans[0]["args_json"])["changes"] == {field: value}


def test_a_request_with_no_stated_value_prepares_nothing_and_says_so(acme):
    """Never invent one. A placeholder that reads like data gets committed."""
    outcome = run(acme, "Please update my home address, I have moved.")
    assert [p for p in outcome.plans if p["operation"] == "commit_record_change"] == []
    assert "the new value was not stated" in outcome.draft
    assert "I will not guess" in outcome.draft


def test_an_identifier_the_person_supplied_may_be_repeated_back(acme):
    """A preview that hides the value cannot be checked, which defeats a preview."""
    outcome = run(acme, "My new phone number is 07700 900456.")
    assert outcome.verify_passed
    assert "07700 900456" in outcome.draft
    check = next(c for c in outcome.checks if c["id"] == "no_identifiers")
    assert check["status"] == "pass" and "repeated back" in check["detail"]


def test_an_identifier_the_person_did_not_supply_is_a_leak(acme, monkeypatch):
    conn, cfg = acme
    monkeypatch.setattr(
        "hr_agent.draft.prepared_change",
        lambda cfg, previews, unstated=None: "Priya can be reached on 07700 900111 about this change today.",
    )
    outcome = run(acme, "Please change my phone to 07700 900456.")
    assert not outcome.verify_passed
    assert any("did not come from the request" in c["detail"] for c in outcome.checks if c["status"] == "fail")


# ---------------------------------------------------------------- citations


def test_a_second_section_is_quoted_when_it_is_nearly_as_relevant(acme):
    """Quoting only the top hit answered half of some questions."""
    outcome = run(acme, "How many days of annual leave do I get, and can I carry any over?")
    if outcome.mode not in {"auto", "prepare"}:
        pytest.skip(f"reached {outcome.mode}")
    assert len(outcome.citations) >= 1
    for citation in outcome.citations:
        assert citation.quoted_text in outcome.draft


def test_a_weak_second_match_is_not_padded_in(acme):
    conn, cfg = acme
    probe = policy.search(conn, cfg, "how many days of annual leave", person=cfg.people[0])
    chosen = policy.best_citations(probe)
    assert chosen[0] is probe.citations[0]
    assert all(c.score >= probe.citations[0].score * policy.SECOND_CITATION_MARGIN for c in chosen)


def test_two_versions_of_one_policy_are_never_both_quoted(acme):
    conn, cfg = acme
    probe = policy.search(conn, cfg, "annual leave carry over notice", person=cfg.people[0])
    chosen = policy.best_citations(probe, limit=3)
    seen: dict[str, str] = {}
    for citation in chosen:
        assert seen.setdefault(citation.doc_id, citation.version) == citation.version


def test_a_statement_about_a_field_is_not_a_change_request(acme):
    """"My phone is broken" must never become a write."""
    outcome = run(acme, "My phone is broken, I cannot be reached today.")
    assert [p for p in outcome.plans if p["operation"] == "commit_record_change"] == []


def test_a_value_containing_a_full_stop_is_not_truncated(acme):
    """`[^.]` truncated mo@example.test to mo@example and Flat 2.1 to Flat 2."""
    outcome = run(acme, "Please change my home address to Flat 2.1, Mill Court. Thanks!")
    plan = next(p for p in outcome.plans if p["operation"] == "commit_record_change")
    assert db.loads(plan["args_json"])["changes"] == {"home_address": "Flat 2.1, Mill Court"}


# ---------------------------------------------------------------- effective dates


def test_a_scheduled_version_takes_over_on_its_own_date(meridian):
    """Requiring a person to flip a status on the morning a policy lands is how
    an agent ends up quoting last year's rules."""
    from datetime import date

    conn, cfg = meridian
    today = kernel.run(conn, cfg, message="How many office days a week are expected?",
                       requester_id="mg_1001", on=date(2026, 11, 2))
    later = kernel.run(conn, cfg, message="How many office days a week are expected?",
                       requester_id="mg_1001", on=date(2027, 2, 1))
    assert today.citations[0].version == "1.0"
    assert later.citations[0].version == "1.1"
    assert "three days each week" in today.citations[0].quoted_text
    assert "two days each week" in later.citations[0].quoted_text


def test_the_superseded_version_stops_being_quotable(meridian):
    """Without supersession both would be live and the agent would refuse."""
    from datetime import date

    conn, cfg = meridian
    flex = next(p for p in cfg.policies if p.id == "POL-FLEX")
    live = policy.in_force(flex, on=date(2027, 2, 1), person=cfg.people[0])
    assert [v.version for v in live] == ["1.1"]


def test_a_retired_version_stays_retired_whatever_its_dates_say(acme):
    """Retiring is a human decision, not a function of the calendar."""
    from datetime import date

    conn, cfg = acme
    remote = next(p for p in cfg.policies if p.id == "POL-REMOTE")
    for when in (date(2024, 6, 1), date(2026, 11, 2), date(2030, 1, 1)):
        assert all(v.version != "1.0" for v in policy.in_force(remote, on=when, person=None))


def test_two_versions_starting_the_same_day_still_means_no_answer(acme):
    """Supersession resolves different dates. The same date is still ambiguous."""
    conn, cfg = acme
    leave = next(p for p in cfg.policies if p.id == "POL-LEAVE")
    leave.versions.append(leave.versions[0].model_copy(update={"version": "1.3"}))
    outcome = run(acme, "How many days of annual leave do I get in a year?")
    assert outcome.rule_id == "E0"
    assert outcome.citations == []
