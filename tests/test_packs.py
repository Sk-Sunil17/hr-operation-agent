"""Packs: a company is configuration, and the configuration cannot lie.

The guarantee these tests exist to protect: a tenant pack may tighten what the
base pack allows and may never loosen it. Everything else here is about failing
loudly at load time instead of quietly at runtime.
"""

from __future__ import annotations

import pytest

from hr_agent.packs import loader
from hr_agent.packs.loader import Loosened, PackError
from hr_agent.packs.models import restriction
from hr_agent.packs.validate import validate
from pydantic import ValidationError

from .conftest import TENANTS, edit_yaml


def resolved(packs, tenant_id: str):
    return loader.load_resolved(tenant_id, base_dir=packs / "base", tenants_dir=packs / "tenants")


def errors(cfg):
    return [p for p in validate(cfg) if p.severity == "error"]


def find(items: list, **match):
    """Locate a pack entry by its fields. Tests must never index by position:
    a positional test keeps passing while silently checking the wrong row."""
    for index, item in enumerate(items):
        if all(item.get(k) == v for k, v in match.items()):
            return index, item
    raise AssertionError(f"no entry matching {match}")


def edit_entry(path, items_key: str, match: dict, mutate) -> None:
    from .conftest import edit_yaml

    def apply(doc):
        _, entry = find(doc[items_key], **match)
        mutate(entry)

    edit_yaml(path, apply)


# ---------------------------------------------------------------- the shipped packs


@pytest.mark.parametrize("tenant_id", TENANTS)
def test_every_shipped_pack_loads_and_validates(packs, tenant_id):
    assert errors(resolved(packs, tenant_id)) == []


def test_the_two_shipped_companies_are_genuinely_different(packs):
    """If the seed companies drift towards each other they stop testing anything."""
    acme, meridian = (resolved(packs, t) for t in TENANTS)
    assert acme.config_hash != meridian.config_hash
    assert acme.locale.timezone != meridian.locale.timezone
    assert set(acme.approvals.roles) != set(meridian.approvals.roles)
    assert len(meridian.segments) > len(acme.segments) == 0
    assert {a.kind for a in meridian.adapters} > {a.kind for a in acme.adapters}
    # and they do not reach the same outcome for the same question
    assert acme.rule("E1").mode == "auto"
    assert meridian.rule("E1").mode == "prepare"


def test_the_config_hash_is_stable_for_the_same_pack(packs):
    assert resolved(packs, "acme_labs").config_hash == resolved(packs, "acme_labs").config_hash


def test_changing_a_pack_changes_the_config_hash(packs):
    before = resolved(packs, "acme_labs").config_hash
    edit_yaml(packs / "tenants/acme_labs/manifest.yaml", lambda d: d["sla"].update(reminder_cap=1))
    assert resolved(packs, "acme_labs").config_hash != before


# ---------------------------------------------------------------- tighten, never loosen


def test_a_tenant_may_tighten_a_rule(packs):
    (packs / "tenants/acme_labs/rules.yaml").write_text(
        "rules:\n"
        "  - id: E1\n"
        "    mode: hand_over\n"
        '    reason: "This company wants a person on every answer, however routine."\n',
        encoding="utf-8",
    )
    cfg = resolved(packs, "acme_labs")
    assert cfg.rule("E1").mode == "hand_over"
    assert cfg.provenance["rules.E1.mode"] == "tenant"


def test_a_tenant_cannot_loosen_a_rule(packs):
    (packs / "tenants/meridian_group/rules.yaml").write_text(
        "rules:\n"
        "  - id: E2\n"
        "    mode: auto\n"
        '    reason: "We would like the agent to answer personal questions by itself."\n',
        encoding="utf-8",
    )
    with pytest.raises(Loosened, match="may only tighten"):
        resolved(packs, "meridian_group")


def test_a_tenant_cannot_unlock_a_refusal(packs):
    (packs / "tenants/acme_labs/rules.yaml").write_text(
        "rules:\n"
        "  - id: P1\n"
        "    mode: prepare\n"
        '    reason: "We would like the agent to publish policies for us."\n',
        encoding="utf-8",
    )
    with pytest.raises(Loosened):
        resolved(packs, "acme_labs")


def test_a_tenant_cannot_let_a_restricted_matter_be_answered(packs):
    (packs / "tenants/acme_labs/rules.yaml").write_text(
        "rules:\n"
        "  - id: R1\n"
        "    mode: auto\n"
        '    reason: "We would like the agent to answer grievances directly."\n',
        encoding="utf-8",
    )
    with pytest.raises(Loosened):
        resolved(packs, "acme_labs")


def test_a_tenant_may_add_a_citation_requirement(packs):
    (packs / "tenants/acme_labs/rules.yaml").write_text(
        "rules:\n"
        "  - id: E4\n"
        "    needs_citation: true\n"
        '    reason: "An unclear request still goes to a person with the policy attached."\n',
        encoding="utf-8",
    )
    cfg = resolved(packs, "acme_labs")
    assert cfg.rule("E4").needs_citation is True
    assert cfg.provenance["rules.E4.needs_citation"] == "tenant"


def test_a_tenant_cannot_drop_a_citation_requirement(packs):
    (packs / "tenants/acme_labs/rules.yaml").write_text(
        "rules:\n"
        "  - id: E1\n"
        "    needs_citation: false\n"
        '    reason: "We would rather answer without quoting the policy every time."\n',
        encoding="utf-8",
    )
    with pytest.raises(Loosened, match="only be added"):
        resolved(packs, "acme_labs")


def test_a_segment_may_only_tighten(packs):
    edit_entry(
        packs / "tenants/meridian_group/segments.yaml",
        "segments",
        {"id": "collective_agreement"},
        lambda s: s.update(rules=[{"id": "W1", "mode": "auto", "reason": "This population may read anyone's record."}]),
    )
    cfg = resolved(packs, "meridian_group")  # resolving is fine; the segment applies later
    with pytest.raises(Loosened):
        loader.rules_for_segment(cfg, ["collective_agreement"])


def test_the_modes_are_ordered_from_free_to_restricted():
    assert restriction("auto") < restriction("prepare") < restriction("hand_over") < restriction("refuse")


# ---------------------------------------------------------------- loud failure


def test_an_overlay_for_an_unknown_rule_is_an_error(packs):
    (packs / "tenants/acme_labs/rules.yaml").write_text(
        'rules:\n  - id: NOPE\n    mode: refuse\n    reason: "A rule that does not exist anywhere."\n',
        encoding="utf-8",
    )
    with pytest.raises(PackError, match="unknown rule"):
        resolved(packs, "acme_labs")


def test_a_manifest_id_must_match_its_folder(packs):
    edit_yaml(packs / "tenants/acme_labs/manifest.yaml", lambda d: d.update(id="something_else"))
    with pytest.raises(PackError, match="folder"):
        resolved(packs, "acme_labs")


def test_a_policy_naming_a_missing_file_is_an_error(packs):
    (packs / "tenants/acme_labs/policies/leave-v1.2.md").unlink()
    with pytest.raises(PackError, match="missing file"):
        resolved(packs, "acme_labs")


def test_an_unknown_key_in_a_pack_file_is_an_error(packs):
    edit_yaml(packs / "tenants/acme_labs/manifest.yaml", lambda d: d.update(autoapprove_everything=True))
    with pytest.raises(ValidationError, match="autoapprove_everything"):
        resolved(packs, "acme_labs")


def test_a_tenant_redaction_pattern_cannot_shadow_a_base_one(packs):
    edit_yaml(
        packs / "tenants/acme_labs/redaction.yaml",
        lambda d: d["patterns"].append({"name": "email", "class": "email", "regex": "x", "replacement": "[x]"}),
    )
    with pytest.raises(PackError, match="reuse base names"):
        resolved(packs, "acme_labs")


def test_no_adapter_may_own_a_pay_or_access_field(packs):
    edit_entry(
        packs / "tenants/acme_labs/adapters.yaml", "adapters", {"name": "people_system"}, lambda a: a["owns_fields"].append("salary")
    )
    with pytest.raises(ValidationError, match="pay or access"):
        resolved(packs, "acme_labs")


# ---------------------------------------------------------------- coherence


def test_a_rule_testing_an_unknown_fact_is_reported(packs):
    edit_entry(packs / "base/rules.yaml", "rules", {"id": "E1"}, lambda r: r["when"].update(mood="cheerful"))
    assert any("unknown fact" in p.message for p in errors(resolved(packs, "acme_labs")))


def test_a_rule_testing_an_impossible_value_is_reported(packs):
    edit_entry(
        packs / "base/rules.yaml", "rules", {"id": "E1"}, lambda r: r["when"].update(sensitivity="mildly_awkward")
    )
    assert any("cannot be" in p.message for p in errors(resolved(packs, "acme_labs")))


def test_an_auto_answer_without_a_citation_is_reported(packs):
    edit_entry(packs / "base/rules.yaml", "rules", {"id": "E1"}, lambda r: r.update(needs_citation=False))
    problems = errors(resolved(packs, "acme_labs"))
    assert any("does not require a citation" in p.message for p in problems)


def test_escalating_to_a_role_nobody_holds_is_reported(packs):
    edit_entry(
        packs / "tenants/acme_labs/people.yaml", "people", {"roles": ["hr_lead"]}, lambda p: p.update(roles=["employee"])
    )
    assert any("no active person holds" in p.message for p in errors(resolved(packs, "acme_labs")))


def test_escalating_to_a_role_that_does_not_exist_is_reported(packs):
    edit_yaml(
        packs / "tenants/acme_labs/manifest.yaml",
        lambda d: d["escalation"].update(restricted_role="chief_vibes_officer"),
    )
    assert any("unknown role" in p.message for p in errors(resolved(packs, "acme_labs")))


def test_two_versions_starting_on_the_same_day_is_reported(packs):
    """The genuine ambiguity. Nothing can decide between them, so the pack is wrong."""
    edit_entry(
        packs / "tenants/acme_labs/policies/versions.yaml",
        "policies",
        {"id": "POL-LEAVE"},
        lambda p: p["versions"].append(
            {"version": "1.3", "status": "in_force", "effective_from": "2026-01-01", "file": "leave-v1.2.md"}
        ),
    )
    assert any("nothing can decide which applies" in p.message for p in errors(resolved(packs, "acme_labs")))


def test_a_later_version_superseding_an_earlier_one_is_fine(packs):
    """Ordinary supersession. The later one takes over on its own date; nobody
    has to remember to set effective_to on the old one."""
    edit_entry(
        packs / "tenants/acme_labs/policies/versions.yaml",
        "policies",
        {"id": "POL-LEAVE"},
        lambda p: p["versions"].append(
            {"version": "1.3", "status": "scheduled", "effective_from": "2027-06-01", "file": "leave-v1.2.md"}
        ),
    )
    assert errors(resolved(packs, "acme_labs")) == []


def test_two_scoped_versions_in_force_is_fine(packs):
    """Meridian runs one onboarding policy per entity. That is not a conflict."""
    cfg = resolved(packs, "meridian_group")
    onboard = next(p for p in cfg.policies if p.id == "POL-ONBOARD")
    in_force = [v for v in onboard.versions if v.status == "in_force"]
    assert len(in_force) == 2
    assert all(v.scope for v in in_force)
    assert errors(cfg) == []


def test_one_field_cannot_be_owned_by_two_adapters(packs):
    edit_entry(
        packs / "tenants/meridian_group/adapters.yaml",
        "adapters",
        {"name": "core_hr"},
        lambda a: a["owns_fields"].append("bank_details"),
    )
    assert any("more than one adapter" in p.message for p in errors(resolved(packs, "meridian_group")))


def test_a_segment_matching_on_pay_is_reported(packs):
    edit_entry(
        packs / "tenants/meridian_group/segments.yaml", "segments", {"id": "entity_sg"}, lambda s: s.update(match={"salary": 100000})
    )
    assert any("matches on salary" in p.message for p in errors(resolved(packs, "meridian_group")))


def test_a_golden_case_naming_someone_who_does_not_exist_is_reported(packs):
    edit_entry(
        packs / "tenants/acme_labs/golden_cases.yaml", "cases", {"id": "AC-01"}, lambda c: c.update(requester="emp_999")
    )
    assert any("not in the directory" in p.message for p in errors(resolved(packs, "acme_labs")))


# ---------------------------------------------------------------- base pack shape


def test_the_last_rule_catches_everything_and_does_not_act_alone(packs):
    base = loader.load_base(packs / "base")
    assert base.rules[-1].when == {}
    assert base.rules[-1].mode != "auto"


def test_rule_ids_are_unique(packs):
    base = loader.load_base(packs / "base")
    ids = [r.id for r in base.rules]
    assert len(ids) == len(set(ids))


def test_every_rule_has_a_reason_a_person_can_read(packs):
    base = loader.load_base(packs / "base")
    assert all(len(r.reason.split()) >= 4 and r.reason.endswith(".") for r in base.rules)


def test_restricted_is_handled_before_record_ownership(packs):
    """Someone reporting a colleague's conduct must reach a person, not a refusal."""
    base = loader.load_base(packs / "base")
    order = [r.id for r in base.rules]
    assert order.index("R1") < order.index("W1")


def test_a_takeover_outranks_everything(packs):
    base = loader.load_base(packs / "base")
    assert base.rules[0].id == "T1"


def test_no_rule_lets_the_agent_publish_a_policy(packs):
    base = loader.load_base(packs / "base")
    policy_rules = [r for r in base.rules if r.when.get("intent") == "policy"]
    assert policy_rules and all(r.mode == "refuse" for r in policy_rules)


def test_the_kernel_contains_no_jurisdiction_specific_identifier(packs):
    """Local identifier formats belong in a company's pack, never in the base pack."""
    text = (packs / "base/redaction/generic.yaml").read_text(encoding="utf-8").lower()
    for word in ("tfn", "aadhaar", "ssn", "medicare", "abn", "bsb", "nino", "pan card"):
        assert word not in text, f"base redaction pack names a local scheme: {word}"


# ---------------------------------------------------------------- provenance


def test_provenance_says_which_layer_set_each_thing(packs):
    cfg = resolved(packs, "meridian_group")
    assert cfg.provenance["rules.E1.mode"] == "tenant"
    assert cfg.provenance["rules.R1.mode"] == "base"
    assert cfg.provenance["taxonomy"] == "base"
    assert cfg.provenance["redaction"] == "base+tenant"


def test_a_company_without_overlays_runs_at_the_base_ceiling(packs):
    base = loader.load_base(packs / "base")
    cfg = resolved(packs, "acme_labs")
    assert [(r.id, r.mode) for r in cfg.rules] == [(r.id, r.mode) for r in base.rules]
