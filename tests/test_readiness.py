"""Onboarding readiness reports what the systems say, not what we assumed.

This module exists because an earlier version returned three hardcoded strings
for every new starter at every company. It reported findings about named people
that nobody had checked, in a product whose whole argument is that it does not
invent things. These tests make that regression impossible.
"""

from __future__ import annotations

import pytest

from hr_agent import db, kernel, readiness, tools
from hr_agent.packs import loader

from .conftest_agent import TODAY, build

LENA = "emp_005"


@pytest.fixture
def acme():
    return build("acme_labs")


def satisfy_field(conn, cfg, person_id, field, value):
    registry = tools.Registry(conn, cfg)
    db.insert(conn, "work_item", {
        "id": f"w_{field}", "tenant_id": cfg.tenant_id, "channel": "web",
        "status": "open", "config_hash": cfg.config_hash, "created_at": db.now(),
    })
    plan = registry.prepare(
        work_item_id=f"w_{field}", decision_id=None, adapter=registry.owning(field).name,
        operation="prepare_record_change", idempotency_key=f"key-{field}-{person_id}",
        person_id=person_id, changes={field: value},
    )
    registry.commit(plan["id"], actor="emp_021", authorised=True)


def file_document(conn, cfg, person_id, document_type):
    registry = tools.Registry(conn, cfg)
    db.insert(conn, "work_item", {
        "id": f"w_{document_type}", "tenant_id": cfg.tenant_id, "channel": "web",
        "status": "open", "config_hash": cfg.config_hash, "created_at": db.now(),
    })
    plan = registry.prepare(
        work_item_id=f"w_{document_type}", decision_id=None, adapter="documents",
        operation="prepare_file", idempotency_key=f"key-{document_type}-{person_id}",
        person_id=person_id, document_type=document_type,
    )
    registry.commit(plan["id"], actor="emp_021", authorised=True)


def test_nothing_is_reported_that_was_not_checked(acme):
    conn, cfg = acme
    report = readiness.check(conn, cfg, person_id=LENA)
    assert report.configured
    assert {f.item.id for f in report.findings} == {i.id for i in cfg.readiness}


def test_everything_is_outstanding_when_nothing_has_been_done(acme):
    conn, cfg = acme
    report = readiness.check(conn, cfg, person_id=LENA)
    assert len(report.outstanding) == len(cfg.readiness)
    assert not report.ready


def test_a_field_the_system_holds_is_reported_as_satisfied(acme):
    conn, cfg = acme
    satisfy_field(conn, cfg, LENA, "emergency_contact", "Mira Fischer")
    report = readiness.check(conn, cfg, person_id=LENA)
    assert "emergency_contact" not in {f.item.id for f in report.outstanding}
    assert "emergency_contact" in {f.item.id for f in report.findings if f.present}


def test_a_filed_document_is_reported_as_satisfied(acme):
    conn, cfg = acme
    file_document(conn, cfg, LENA, "right_to_work")
    report = readiness.check(conn, cfg, person_id=LENA)
    assert "right_to_work" not in {f.item.id for f in report.outstanding}


def test_a_document_filed_for_somebody_else_does_not_count(acme):
    """The obvious way to get this wrong."""
    conn, cfg = acme
    file_document(conn, cfg, "emp_001", "right_to_work")
    report = readiness.check(conn, cfg, person_id=LENA)
    assert "right_to_work" in {f.item.id for f in report.outstanding}


def test_a_prepared_but_uncommitted_document_does_not_count(acme):
    """Preparing is not doing, here as everywhere else."""
    conn, cfg = acme
    registry = tools.Registry(conn, cfg)
    db.insert(conn, "work_item", {
        "id": "w_x", "tenant_id": cfg.tenant_id, "channel": "web", "status": "open",
        "config_hash": cfg.config_hash, "created_at": db.now(),
    })
    registry.prepare(
        work_item_id="w_x", decision_id=None, adapter="documents", operation="prepare_file",
        idempotency_key="k-prepared-only", person_id=LENA, document_type="contract",
    )
    report = readiness.check(conn, cfg, person_id=LENA)
    assert "contract" in {f.item.id for f in report.outstanding}


def test_everything_done_means_ready(acme):
    conn, cfg = acme
    for item in cfg.readiness:
        if item.field:
            satisfy_field(conn, cfg, LENA, item.field, "something")
        else:
            file_document(conn, cfg, LENA, item.document)
    report = readiness.check(conn, cfg, person_id=LENA)
    assert report.ready
    assert report.outstanding == []


def test_blocking_items_are_distinguished(acme):
    conn, cfg = acme
    report = readiness.check(conn, cfg, person_id=LENA)
    assert report.blocking
    assert all(f.item.blocking for f in report.blocking)
    assert len(report.blocking) < len(report.outstanding), "not everything should be blocking"


def test_a_company_with_no_checklist_says_so_rather_than_reporting_nothing(acme):
    """An empty checklist must not read as a clean bill of health."""
    conn, cfg = acme
    cfg.readiness = []
    report = readiness.check(conn, cfg, person_id=LENA)
    assert not report.configured
    assert not report.ready
    # Phrasing matters: "starts soon" is read as an enquiry, which is correct.
    # The onboarding path needs a phrase the taxonomy recognises.
    outcome = kernel.run(conn, cfg, message="Lena starts in three weeks, is she ready to go?",
                         requester_id="emp_021", on=TODAY)
    assert outcome.rule_id == "O1"
    assert "has not supplied a new starter checklist" in outcome.draft


def test_the_report_names_the_system_it_asked(acme):
    conn, cfg = acme
    report = readiness.check(conn, cfg, person_id=LENA)
    field_findings = [f for f in report.findings if f.item.field]
    assert all("people_system" in f.detail for f in field_findings)


def test_the_reply_lists_what_was_checked_not_just_what_is_missing(acme):
    conn, cfg = acme
    satisfy_field(conn, cfg, LENA, "emergency_contact", "Mira Fischer")
    outcome = kernel.run(conn, cfg, message="Lena starts in three weeks, is she ready to go?",
                         requester_id="emp_021", on=TODAY)
    assert "I checked 5 items" in outcome.draft
    assert "Already in place: Emergency contact" in outcome.draft
    assert "I have changed nothing" in outcome.draft


def test_no_finding_is_hardcoded_in_the_source():
    """The exact regression: findings came from a list literal in the kernel."""
    from pathlib import Path

    kernel_source = Path("hr_agent/kernel.py").read_text(encoding="utf-8")
    for invented in ("right to work documents not yet verified",
                     "bank details for payroll not yet supplied",
                     "emergency contact missing"):
        assert invented not in kernel_source


@pytest.mark.parametrize("tenant_id", ["acme_labs", "meridian_group"])
def test_every_shipped_company_has_an_answerable_checklist(tenant_id):
    cfg = loader.load_resolved(tenant_id)
    owned = {f for a in cfg.adapters for f in a.owns_fields}
    assert cfg.readiness, "a company with no checklist cannot report readiness"
    for item in cfg.readiness:
        assert (item.field in owned) if item.field else bool(item.document)
