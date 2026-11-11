"""Reading a form that arrived.

The distinctions here are the whole value of the feature. "Blank" and
"illegible" call for different actions from a person. "N/A" is not a value.
A restricted type is never read at all, and that is decided by the type rather
than by what the contents turn out to say.
"""

from __future__ import annotations

import pytest

from hr_agent import forms, kernel
from hr_agent.packs import loader

from .conftest_agent import TODAY, build

COMPLETE = """New starter form
Version: 2026-01
Full name: Lena Fischer
Start date: 24 November 2026
Personal email: lena.f@example.test
Phone: 07700 900321
Emergency contact: Mira Fischer 07700 900999
Bank details: provided separately
Dietary requirements: none
Page 1 of 2
"""


@pytest.fixture
def cfg():
    return loader.load_resolved("acme_labs")


@pytest.fixture
def acme():
    return build("acme_labs")


# ---------------------------------------------------------------- detection


def test_a_form_is_recognised_by_its_own_phrases(cfg):
    assert forms.detect("New starter form\nFull name: X", cfg).id == "new_starter"
    assert forms.detect("Right to work\nName: X", cfg).id == "right_to_work"


def test_detection_survives_case_and_surrounding_text(cfg):
    assert forms.detect("Hi, please find my NEW STARTER FORM attached, thanks", cfg).id == "new_starter"


def test_the_most_specific_phrase_wins(cfg):
    """A long phrase must beat a short one that happens to be contained in it."""
    assert forms.detect("flexible working request\nName: X", cfg).id == "flexible_request"


def test_an_unrecognised_page_is_not_a_form(cfg):
    assert forms.detect("Dear team, I will be late on Tuesday. Regards", cfg) is None
    assert not forms.read("Dear team, I will be late", cfg).recognised


# ---------------------------------------------------------------- reading


def test_a_complete_form_is_complete(cfg):
    reading = forms.read(COMPLETE, cfg)
    assert reading.complete
    assert reading.missing == [] and reading.illegible == []


def test_aliases_are_matched(cfg):
    reading = forms.read("New starter form\nName: Lena\nEmail: l@x.test\nMobile: 07700 900321", cfg)
    read_back = {f.id: f.value for f in reading.fields if f.satisfied}
    assert read_back["full_name"] == "Lena"
    assert read_back["personal_email"] == "l@x.test"
    assert read_back["phone"] == "07700 900321"


BLANKS = ["", "-", "N/A", "n/a", "nil", "none", "TBC", "?", "[blank]", "unknown", "not applicable"]


@pytest.mark.parametrize("written", BLANKS, ids=[b or "empty" for b in BLANKS])
def test_the_many_ways_a_person_writes_nothing(cfg, written):
    """Treating any of these as a value files "N/A" as somebody's bank account."""
    reading = forms.read(f"New starter form\nFull name: Lena\nBank details: {written}", cfg)
    bank = next(f for f in reading.fields if f.id == "bank_details")
    assert bank.state == "blank"
    assert bank.value is None


@pytest.mark.parametrize("written", ["[illegible]", "illegible", "unreadable", "[unclear]"])
def test_illegible_is_distinguished_from_blank(cfg, written):
    """These call for different actions: chase the person, or go back to the page."""
    reading = forms.read(f"New starter form\nFull name: Lena\nPhone: {written}", cfg)
    phone = next(f for f in reading.fields if f.id == "phone")
    assert phone.state == "illegible"
    assert phone not in reading.missing
    assert phone in reading.illegible


def test_a_field_that_is_not_on_the_page_at_all_is_missing(cfg):
    reading = forms.read("New starter form\nFull name: Lena", cfg)
    assert "Start date" in [f.label for f in reading.missing]


def test_an_optional_field_left_blank_does_not_block(cfg):
    without_dietary = COMPLETE.replace("Dietary requirements: none\n", "")
    assert forms.read(without_dietary, cfg).complete


def test_page_furniture_is_not_reported_as_a_field(cfg):
    reading = forms.read(COMPLETE + "\nFor office use only\nSigned: yes\nPage 2 of 2\n", cfg)
    assert reading.unexpected == []


def test_an_unexpected_label_is_surfaced(cfg):
    """A form with an extra field on it is usually a form that has changed."""
    reading = forms.read(COMPLETE + "Favourite colour: blue\n", cfg)
    assert reading.unexpected == ["Favourite colour"]


def test_an_old_template_is_detected(cfg):
    reading = forms.read(COMPLETE.replace("Version: 2026-01", "Version: 2024-03"), cfg)
    assert reading.old_template
    assert reading.stated_version == "2024-03"


def test_a_form_with_no_version_is_not_assumed_old(cfg):
    reading = forms.read(COMPLETE.replace("Version: 2026-01\n", ""), cfg)
    assert not reading.old_template


# ---------------------------------------------------------------- restricted


def test_a_restricted_type_is_never_read(cfg):
    reading = forms.read("Medical certificate\nPatient: Lena\nDiagnosis: something private", cfg)
    assert reading.restricted
    assert reading.fields == []
    assert not reading.complete


def test_restricted_comes_from_the_type_not_the_contents(cfg):
    """An innocuous medical certificate is still a medical certificate."""
    reading = forms.read("Fit note\nEverything is completely fine", cfg)
    assert reading.restricted


def test_a_restricted_document_is_never_filed(acme):
    conn, cfg = acme
    outcome = kernel.run(conn, cfg, message="Attaching this.", requester_id="emp_005", on=TODAY,
                         attachment={"text": "Medical certificate\nDiagnosis: private"})
    assert outcome.rule_id == "D1"
    assert [p for p in outcome.plans if p["operation"] == "commit_file"] == []


def test_a_restricted_document_reaches_a_person(acme):
    conn, cfg = acme
    outcome = kernel.run(conn, cfg, message="Attaching this.", requester_id="emp_005", on=TODAY,
                         attachment={"text": "Medical certificate\nDiagnosis: private"})
    assert outcome.mode == "hand_over"
    assert "private" not in outcome.draft


# ---------------------------------------------------------------- end to end


def test_an_incomplete_form_names_what_is_missing_and_chases_nobody(acme):
    conn, cfg = acme
    outcome = kernel.run(conn, cfg, message="Here is my form.", requester_id="emp_005", on=TODAY,
                         attachment={"text": "New starter form\nFull name: Lena Fischer\nStart date:\nPhone: [illegible]"})
    assert outcome.rule_id == "D3"
    assert outcome.mode == "prepare"
    assert "Start date" in outcome.draft
    assert "Could not be read" in outcome.draft and "Phone" in outcome.draft
    assert "chased nobody" in outcome.draft
    assert [p for p in outcome.plans if p["operation"] == "commit_file"] == []


def test_a_complete_form_is_prepared_for_filing_but_not_filed(acme):
    conn, cfg = acme
    outcome = kernel.run(conn, cfg, message="Here is my form.", requester_id="emp_005", on=TODAY,
                         attachment={"text": COMPLETE})
    assert outcome.rule_id == "D4" and outcome.mode == "prepare"
    filing = [p for p in outcome.plans if p["operation"] == "commit_file"]
    assert filing and filing[0]["status"] == "prepared"
    from hr_agent import db

    assert db.rows(conn, "SELECT * FROM filed_document") == []


def test_a_sensitive_field_is_reported_as_present_not_quoted(acme):
    """An approver does not need bank details copied into a reply to confirm
    the form was filled in."""
    conn, cfg = acme
    outcome = kernel.run(conn, cfg, message="Here is my form.", requester_id="emp_005", on=TODAY,
                         attachment={"text": COMPLETE.replace("Bank details: provided separately",
                                                              "Bank details: 12345678 sort 062-000")})
    assert "12345678" not in outcome.draft
    assert "filled in (not repeated here)" in outcome.draft
    assert outcome.verify_passed


def test_an_expiry_is_taken_from_the_template(acme):
    """Right to work expires after 24 months at this company, by its own pack."""
    conn, cfg = acme
    outcome = kernel.run(conn, cfg, message="Attaching this.", requester_id="emp_005", on=TODAY,
                         attachment={"text": "Right to work\nFull name: Lena\nDocument seen: passport\n"
                                             "Checked by: Hana\nCheck date: 2 November 2026"})
    plan = next(p for p in outcome.plans if p["operation"] == "commit_file")
    assert "expiring 2028-11-02" in plan["preview_text"]


def test_two_companies_read_their_own_forms():
    """Meridian's joiner pack is not Acme's new starter form."""
    acme = loader.load_resolved("acme_labs")
    meridian = loader.load_resolved("meridian_group")
    assert forms.detect("New joiner pack\nName: X", meridian).id == "joiner_pack"
    assert forms.detect("New joiner pack\nName: X", acme) is None
    assert forms.detect("New starter form\nName: X", meridian) is None


# ---------------------------------------------------------------- reachability
#
# Document intake is the largest piece of real HR work the agent does, and it was
# unreachable from the dashboard: `/api/chat` took a message and dropped any
# attachment, and no company's golden cases declared one, so rules D0-D4 never
# fired outside a unit test. Both halves are asserted here.


def test_a_document_that_arrives_decides_the_request_is_an_intake():
    """The wording is a reading of the text; the attachment is a fact.

    "Can you file this for me please." matches no document phrasing, so it was
    classified as unclear and answered with a clarifying question while the
    document itself went unread.
    """
    from hr_agent import classify
    from hr_agent.packs import loader

    cfg = loader.load_resolved("acme_labs")
    vague = "Can you file this for me please."
    assert classify.classify(vague, cfg, requester_id="emp_001").intent != "document"
    assert classify.classify(vague, cfg, requester_id="emp_001", has_attachment=True).intent == "document"

    # Sensitivity is untouched, so a restricted matter with something attached is
    # still decided by R1 before any document rule.
    restricted = classify.classify(
        "I am being bullied, here is a screenshot.", cfg, requester_id="emp_001", has_attachment=True
    )
    assert restricted.sensitivity == "restricted"


def test_the_api_passes_an_attachment_through_to_the_kernel(tmp_path):
    from fastapi.testclient import TestClient

    from hr_agent import db, policy, web
    from hr_agent.packs import loader

    # A real file, seeded like `hrctl init` does: the route writes a work item,
    # and that has foreign keys to a tenant and a directory.
    path = tmp_path / "web.db"
    cfg = loader.load_resolved("acme_labs")
    conn = db.open_db(path)
    db.insert(conn, "tenant", {
        "id": cfg.tenant_id, "name": cfg.manifest.name, "pack_path": "packs",
        "config_hash": cfg.config_hash, "active": 1, "created_at": db.now(),
    })
    for person in cfg.people:
        db.insert(conn, "employee", {
            "tenant_id": cfg.tenant_id, "id": person.id, "name": person.name,
            "email": person.email, "manager_id": person.manager_id, "entity": person.entity,
            "employment_type": person.employment_type, "segment_keys": person.segment_keys,
            "roles": person.roles, "timezone": person.timezone,
            "start_date": person.start_date, "end_date": person.end_date,
            "active": int(person.active),
        })
    policy.load_corpus(conn, cfg)
    conn.close()

    client = TestClient(web.create_app(db_path=str(path)))
    body = {
        "requester": "emp_005",
        "message": "Here is my completed new starter form.",
        "attachment": {"text": "NEW STARTER FORM\nFull name: Lena Fischer\nStart date: 2026-11-24"},
    }
    reply = client.post("/api/chat?tenant=acme_labs", json=body)
    assert reply.status_code == 200
    decision = reply.json()["decision"]
    # Incomplete here, which is still a document rule rather than a shrug.
    assert decision["rule_id"].startswith("D"), decision["rule_id"]
    assert reply.json()["item"]["intent"] == "document"

    bad = client.post("/api/chat?tenant=acme_labs", json={**body, "attachment": "not an object"})
    assert bad.status_code == 400


@pytest.mark.parametrize("tenant_id", ["acme_labs", "meridian_group", "harbour_clinic"])
def test_every_company_exercises_document_intake_in_its_own_cases(tenant_id):
    """A shipped company that cannot demonstrate forms cannot demonstrate the
    feature the plan named as the differentiator."""
    from hr_agent.packs import loader

    cfg = loader.load_resolved(tenant_id)
    with_docs = [c for c in cfg.golden_cases if c.attachment and c.attachment.get("text")]
    assert with_docs, f"{tenant_id} declares no golden case with a document"
    reached = {c.expect_rule for c in with_docs}
    assert reached & {"D1", "D3", "D4"}, f"{tenant_id} never exercises a document outcome: {reached}"
