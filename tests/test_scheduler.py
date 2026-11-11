"""Scheduled jobs report and close nothing.

An agent that acts unattended is the one that will do something nobody
approved, and the audit row will say `scheduler` where a name should be. These
tests hold the line: every job goes through the kernel, the gate and the chain,
and none of them sends, files or chases on its own.
"""

from __future__ import annotations

from datetime import date

import pytest

from hr_agent import audit, db, kernel, scheduler

from .conftest_agent import build

TODAY = date(2026, 11, 2)


@pytest.fixture
def acme():
    return build("acme_labs")


def file_doc(conn, cfg, person_id, kind, expires_at):
    db.insert(conn, "filed_document", {
        "tenant_id": cfg.tenant_id, "person_id": person_id, "document_type": kind,
        "reference": f"DOC-{person_id}", "filed_at": db.now(), "expires_at": expires_at,
    })


# ---------------------------------------------------------------- expiry


def test_nothing_expiring_is_reported_as_nothing(acme):
    conn, cfg = acme
    result = scheduler.run_job(conn, cfg, "expiry_scan", on=TODAY)
    assert result.ran and result.found == 0
    assert "Nothing expires" in result.summary
    assert result.work_item_id is None, "a quiet job should not create work"


def test_an_expiring_document_is_found(acme):
    conn, cfg = acme
    file_doc(conn, cfg, "emp_003", "right_to_work", "2026-12-01")
    result = scheduler.run_job(conn, cfg, "expiry_scan", on=TODAY)
    assert result.found == 1
    assert "expires in 29 days" in result.detail


def test_an_already_expired_document_is_called_out(acme):
    conn, cfg = acme
    file_doc(conn, cfg, "emp_004", "right_to_work", "2026-10-01")
    result = scheduler.run_job(conn, cfg, "expiry_scan", on=TODAY)
    assert "already expired" in result.summary
    assert "expired 32 days ago" in result.detail


def test_a_distant_expiry_is_not_mentioned(acme):
    conn, cfg = acme
    file_doc(conn, cfg, "emp_003", "right_to_work", "2028-01-01")
    assert scheduler.run_job(conn, cfg, "expiry_scan", on=TODAY).found == 0


# ---------------------------------------------------------------- readiness


def test_readiness_runs_only_inside_the_lead_window(acme):
    conn, cfg = acme
    far = date(2026, 1, 1)      # Lena starts 2026-11-24, far outside the window
    assert scheduler.run_job(conn, cfg, "readiness", on=far).found == 0
    assert scheduler.run_job(conn, cfg, "readiness", on=TODAY).found == 1


def test_readiness_is_skipped_when_no_checklist_exists(acme):
    conn, cfg = acme
    cfg.readiness = []
    result = scheduler.run_job(conn, cfg, "readiness", on=TODAY)
    assert not result.ran
    assert "no checklist" in result.skipped_because


def test_readiness_closes_nothing(acme):
    conn, cfg = acme
    scheduler.run_job(conn, cfg, "readiness", on=TODAY)
    assert db.rows(conn, "SELECT * FROM adapter_record") == []
    assert db.rows(conn, "SELECT * FROM filed_document") == []


# ---------------------------------------------------------------- policy review


def test_policy_review_is_quiet_when_nothing_is_due(acme):
    conn, cfg = acme
    assert scheduler.run_job(conn, cfg, "policy_review", on=TODAY).found == 0


def test_an_overdue_policy_names_its_owner(acme):
    conn, cfg = acme
    result = scheduler.run_job(conn, cfg, "policy_review", on=date(2030, 1, 1))
    assert result.found
    assert "owner:" in result.detail


# ---------------------------------------------------------------- reminders


def test_the_reminder_cap_stops_automated_chasing(acme):
    """The cap is this company's, and it is counted rather than assumed."""
    conn, cfg = acme
    cap = cfg.manifest.sla.reminder_cap
    first = scheduler.run_job(conn, cfg, "reminders", on=TODAY)
    assert first.found > 0

    for _ in range(cap):
        scheduler.record_reminder(conn, cfg, subject_id="emp_005", about="bank_details")

    after = scheduler.run_job(conn, cfg, "reminders", on=TODAY)
    chaseable = [i for i in after.items if i["item_id"] == "bank_details"]
    assert chaseable and chaseable[0]["sent"] >= cap
    assert "at the cap" in after.summary
    assert "need a person, not another reminder" in after.detail


def test_counting_a_reminder_increments_it(acme):
    conn, cfg = acme
    assert scheduler.record_reminder(conn, cfg, subject_id="emp_005", about="bank_details") == 1
    assert scheduler.record_reminder(conn, cfg, subject_id="emp_005", about="bank_details") == 2


# ---------------------------------------------------------------- the guardrails


def test_a_job_goes_through_the_rule_engine_like_anything_else(acme):
    conn, cfg = acme
    file_doc(conn, cfg, "emp_003", "right_to_work", "2026-12-01")
    result = scheduler.run_job(conn, cfg, "expiry_scan", on=TODAY)
    item = db.one(conn, "SELECT * FROM work_item WHERE id = ?", (result.work_item_id,))
    assert item["rule_id"] == "J1"
    assert item["mode"] == "prepare"
    assert item["status"] == "queued"


def test_a_job_is_audited_and_the_chain_still_verifies(acme):
    conn, cfg = acme
    file_doc(conn, cfg, "emp_003", "right_to_work", "2026-12-01")
    result = scheduler.run_job(conn, cfg, "expiry_scan", on=TODAY)
    stages = [r["stage"] for r in audit.trail(conn, cfg.tenant_id, result.work_item_id)]
    assert stages[0] == "receive" and "decide" in stages and "verify" in stages
    row = next(r for r in audit.trail(conn, cfg.tenant_id, result.work_item_id) if r["stage"] == "receive")
    assert row["actor_kind"] == "scheduler"
    assert audit.verify(conn).ok


def test_a_job_sends_nothing_on_its_own(acme):
    conn, cfg = acme
    file_doc(conn, cfg, "emp_003", "right_to_work", "2026-12-01")
    result = scheduler.run_job(conn, cfg, "expiry_scan", on=TODAY)
    plans = db.rows(conn, "SELECT * FROM action_plan WHERE work_item_id = ?", (result.work_item_id,))
    reports = [p for p in plans if "job-" in p["idempotency_key"] or p["operation"] == "commit_message"]
    assert any(p["status"] == "prepared" for p in reports), "the report itself waits for a person"


def test_a_job_report_says_it_changed_nothing(acme):
    conn, cfg = acme
    file_doc(conn, cfg, "emp_003", "right_to_work", "2026-12-01")
    result = scheduler.run_job(conn, cfg, "expiry_scan", on=TODAY)
    decision = db.one(conn, "SELECT draft_text FROM decision WHERE work_item_id = ?", (result.work_item_id,))
    assert "Nothing has been sent, chased or changed" in decision["draft_text"]


def test_run_all_returns_a_result_per_job(acme):
    conn, cfg = acme
    results = scheduler.run_all(conn, cfg, on=TODAY)
    assert [r.job for r in results] == list(scheduler.JOBS)


def test_an_unknown_job_is_refused(acme):
    conn, cfg = acme
    with pytest.raises(ValueError, match="unknown job"):
        scheduler.run_job(conn, cfg, "delete_everything", on=TODAY)


# ---------------------------------------------------------------- exit checklist


def test_the_exit_checklist_comes_from_the_company(acme):
    conn, cfg = acme
    db.insert(conn, "hr_case", {
        "id": "case_exit", "tenant_id": cfg.tenant_id, "kind": "exit", "subject_id": "emp_003",
        "owner_role": "hr_partner", "status": "open", "taken_over": 0, "opened_at": db.now(),
    })
    outcome = kernel.run(conn, cfg, message="Priya is leaving at the end of the month, what happens next?",
                         requester_id="emp_021", on=TODAY)
    assert outcome.rule_id == "X2" and outcome.mode == "prepare"
    for item in cfg.exit_checklist:
        assert item.label in outcome.draft
    assert "Head of People" in outcome.draft, "each item names the role that owns it"


def test_the_exit_checklist_says_what_it_cannot_answer(acme):
    conn, cfg = acme
    db.insert(conn, "hr_case", {
        "id": "case_exit", "tenant_id": cfg.tenant_id, "kind": "exit", "subject_id": "emp_003",
        "owner_role": "hr_partner", "status": "open", "taken_over": 0, "opened_at": db.now(),
    })
    outcome = kernel.run(conn, cfg, message="Priya is leaving, what happens next?",
                         requester_id="emp_021", on=TODAY)
    assert "No system can answer these" in outcome.draft
    assert "Nothing in this system can revoke access" in outcome.draft


def test_a_returned_leaver_form_moves_to_done(acme):
    conn, cfg = acme
    db.insert(conn, "hr_case", {
        "id": "case_exit", "tenant_id": cfg.tenant_id, "kind": "exit", "subject_id": "emp_003",
        "owner_role": "hr_partner", "status": "open", "taken_over": 0, "opened_at": db.now(),
    })
    file_doc(conn, cfg, "emp_003", "leaver_form", None)
    outcome = kernel.run(conn, cfg, message="Priya is leaving, what happens next?",
                         requester_id="emp_021", on=TODAY)
    assert "Already done: Leaver form returned" in outcome.draft


def test_the_agent_still_cannot_start_an_exit(acme):
    """No exit case means nothing to prepare against, whatever the checklist says."""
    conn, cfg = acme
    outcome = kernel.run(conn, cfg, message="Priya is leaving at the end of the month.",
                         requester_id="emp_021", on=TODAY)
    assert outcome.rule_id == "X1" and outcome.mode == "refuse"
