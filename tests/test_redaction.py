"""Redaction. Nothing reaches a model, a message or the audit trail uncleaned.

Two failure modes matter and they are not symmetric. Letting an identifier
through is a breach. Masking too much makes the record unreadable for the person
reviewing it, which is a quality problem. Both are tested; the first is fatal.
"""

from __future__ import annotations

import pytest

from hr_agent import config, redact
from hr_agent.packs import loader

# Masked by the shipped generic pack, with the class each one must be recognised as.
MUST_MASK = [
    ("Reach me at maya@acme.test or call +44 7700 900123.", {"email", "phone"}),
    ("Call me on 07700 900123, any time.", {"phone"}),
    ("My mobile is 07700900123", {"phone"}),
    ("Tel: (020) 7946 0958", {"phone"}),
    ("contact number 555 1234", {"phone"}),
    ("account number is 12345678", {"account_number"}),
    ("My account no. 12345678.", {"account_number"}),
    ("sort code is 062-000", {"bank_identifier"}),
    ("tax number: AB123456C", {"national_id"}),
    ("identity number 1234567890", {"national_id"}),
    ("passport no. N1234567", {"passport"}),
    ("membership id ABC123456", {"member_number"}),
    ("my date of birth is 14/03/1991.", {"date_of_birth"}),
    ("DOB: 2 March 1988", {"date_of_birth"}),
    ("I live at 42 Wallaby Street.", {"street_address"}),
    ("card 4111 1111 1111 1111", {"account_number"}),
    ("IBAN GB29NWBK60161331926819 please", {"bank_identifier"}),
    ("I was diagnosed with a chronic condition and need time off.", {"medical_term"}),
]

# Ordinary HR text, which must survive untouched or the record becomes unreadable.
MUST_NOT_TOUCH = [
    "How many days of annual leave do I accrue as a part-time employee?",
    "The policy version is 2.1 and section 3 applies from 2026-01-01.",
    "I need 12 weeks of leave starting 3 June.",
    "My employee number is 4471 and I work in the London office.",
    "Section 4.5 says up to 5 days may be carried over.",
    "date of birth unknown",
    "Probation is six months, with a review at three months.",
    "Up to 250 towards a desk is available every three years.",
    "Everyone is expected in the office on Tuesdays and Wednesdays.",
]


@pytest.fixture
def redactor():
    return redact.for_tenant(loader.load_resolved("acme_labs"))


@pytest.mark.parametrize(("text", "classes"), MUST_MASK, ids=[t[:34] for t, _ in MUST_MASK])
def test_identifiers_are_masked(redactor, text, classes):
    assert classes <= {f.klass for f in redactor.findings(text)}
    assert redactor.text(text) != text


@pytest.mark.parametrize("text", MUST_NOT_TOUCH, ids=[t[:34] for t in MUST_NOT_TOUCH])
def test_ordinary_hr_text_is_untouched(redactor, text):
    assert redactor.text(text) == text
    assert redactor.is_clean(text)


def test_the_class_label_survives_so_a_reviewer_can_still_read_it(redactor):
    out = redactor.text("Hi, I am Maya (maya@acme.test, 07700 900123). My DOB is 14/03/1991.")
    assert "[email]" in out and "[phone]" in out and "[date of birth]" in out
    assert "Hi, I am Maya" in out  # the sentence is still a sentence


def test_redaction_is_idempotent(redactor):
    for text, _ in MUST_MASK:
        once = redactor.text(text)
        assert redactor.text(once) == once


def test_a_specific_pattern_runs_before_a_general_one(redactor):
    """phone would otherwise eat twelve digits of a card and leave four in the clear."""
    assert redactor.text("card 4111 1111 1111 1111") == "card [card number]"


def test_a_company_adds_its_own_local_formats(redactor):
    """The only place a jurisdiction-specific identifier is ever named."""
    assert redactor.text("My payroll ref is PR-456123.") == "My payroll ref is [payroll reference]."


def test_one_company_does_not_get_another_company_s_patterns():
    acme = redact.for_tenant(loader.load_resolved("acme_labs"))
    meridian = redact.for_tenant(loader.load_resolved("meridian_group"))
    assert meridian.text("staff id: A1234567B") != "staff id: A1234567B"
    assert acme.text("staff id: A1234567B") == "staff id: A1234567B"
    assert acme.text("PR-456123") != "PR-456123"
    assert meridian.text("PR-456123") == "PR-456123"


def test_assert_clean_raises_on_a_leak(redactor):
    with pytest.raises(redact.Leak, match="email"):
        redactor.assert_clean("write to maya@acme.test", where="outbound reply")
    redactor.assert_clean("write to the people team")


# ---------------------------------------------------------------- structures


def test_sensitive_fields_are_dropped_by_name(redactor):
    out = redactor.mapping({"bank_details": {"iban": "GB29NWBK60161331926819"}, "employee_salary": 50000})
    assert out["bank_details"] == redact.DROPPED
    assert out["employee_salary"] == redact.DROPPED


@pytest.mark.parametrize(
    "key", ["bank_details", "bankAccount", "Bank-Details", "BANK DETAILS", "employee_bank_account", "bank_details_v2"]
)
def test_a_field_name_cannot_hide_behind_its_spelling(redactor, key):
    assert redactor.mapping({key: "x"})[key] == redact.DROPPED


@pytest.mark.parametrize("key", ["bank_holiday", "tokenization_policy", "doberman_name", "job_title", "start_date"])
def test_an_innocent_field_with_a_similar_name_survives(redactor, key):
    assert redactor.mapping({key: "x"})[key] == "x"


def test_contact_fields_are_masked_rather_than_dropped(redactor):
    """Dropping them whole would make the audit trail useless to read."""
    out = redactor.mapping({"note": "call me on 07700 900123", "email": "maya@acme.test"})
    assert out["note"] == "call me on [phone]"
    assert out["email"] == "[email]"


def test_strings_inside_nested_structures_are_masked(redactor):
    out = redactor.mapping({"messages": [{"body": "maya@acme.test"}, {"body": "fine"}]})
    assert out["messages"][0]["body"] == "[email]"
    assert out["messages"][1]["body"] == "fine"


def test_deep_nesting_is_truncated_rather_than_followed_forever(redactor):
    deep: dict = {}
    node = deep
    for _ in range(40):
        node["next"] = {}
        node = node["next"]
    out = redactor.mapping(deep)
    depth = 0
    while isinstance(out, dict) and "next" in out:
        out, depth = out["next"], depth + 1
    assert depth < 40


def test_a_self_referential_structure_does_not_hang(redactor):
    loop: dict = {"name": "x"}
    loop["self"] = loop
    assert redactor.mapping(loop)  # returns rather than recursing forever


def test_non_string_values_are_left_as_they_are(redactor):
    out = redactor.mapping({"days": 25, "active": True, "ratio": 0.5, "nothing": None})
    assert out == {"days": 25, "active": True, "ratio": 0.5, "nothing": None}


# ---------------------------------------------------------------- the guard


def test_nothing_runs_with_redaction_disabled(redactor, monkeypatch):
    monkeypatch.setattr(config, "REDACTION_ENABLED", False)
    with pytest.raises(redact.RedactionDisabled):
        redactor.text("maya@acme.test")
    with pytest.raises(redact.RedactionDisabled):
        redactor.mapping({"a": "b"})
    with pytest.raises(redact.RedactionDisabled):
        redact.guard()


# ---------------------------------------------------------------- the pack itself
#
# An editing accident silently deleted four patterns from the generic pack once.
# Every must-mask case for those classes still "passed" as vacuously true until
# a manual check caught it. These two tests make that failure impossible to miss.

BASE_PATTERNS = [
    "email",
    "iban",
    "card_number",
    "account_number_keyed",
    "bank_identifier_keyed",
    "national_id_keyed",
    "passport_keyed",
    "member_number_keyed",
    "date_of_birth_keyed",
    "phone_keyed",
    "phone",
    "street_address",
    "diagnosis_phrase",
]


def test_the_generic_pack_holds_exactly_the_expected_patterns_in_order():
    """Order is part of the meaning: specific shapes must precede general ones."""
    base = loader.load_base()
    assert [p.name for p in base.patterns_in_order()] == BASE_PATTERNS


def test_every_class_the_pack_claims_to_cover_is_actually_covered():
    base = loader.load_base()
    classes = {p.klass for p in base.redaction.patterns}
    assert classes == {
        "email",
        "phone",
        "national_id",
        "passport",
        "bank_identifier",
        "account_number",
        "date_of_birth",
        "street_address",
        "member_number",
        "medical_term",
    }
