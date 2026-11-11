"""Coherence checks on a resolved configuration.

The Pydantic models prove a pack is well-formed. These checks prove it makes
sense: that rules test facts that exist, that an auto-answering rule cites
something, that escalation goes to a role that exists, that exactly one version
of each policy is in force.

`hrctl validate` runs this. Onboarding a company means getting it to come back
clean, which is what makes "adaptable" a test result rather than a claim.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from .models import ResolvedConfig

Severity = Literal["error", "warning"]

# Employee attributes a segment may match on. Pay and access are absent by design.
MATCHABLE = {"entity", "employment_type", "segment_keys", "roles", "timezone"}


@dataclass(frozen=True)
class Problem:
    severity: Severity
    where: str
    message: str

    def __str__(self) -> str:
        mark = "error" if self.severity == "error" else "warn "
        return f"{mark}  {self.where}: {self.message}"


def validate(resolved: ResolvedConfig) -> list[Problem]:
    problems: list[Problem] = []
    problems += _check_rules(resolved)
    problems += _check_escalation(resolved)
    problems += _check_adapters(resolved)
    problems += _check_policies(resolved)
    problems += _check_readiness(resolved)
    problems += _check_forms(resolved)
    problems += _check_exit_checklist(resolved)
    problems += _check_segments(resolved)
    problems += _check_people(resolved)
    problems += _check_golden_cases(resolved)
    return problems


def _check_rules(cfg: ResolvedConfig) -> list[Problem]:
    out: list[Problem] = []
    facts = cfg.taxonomy.facts
    case_ids = {c.id for c in cfg.cases}

    for rule in cfg.rules:
        for name, expected in rule.when.items():
            spec = facts.get(name)
            if spec is None:
                out.append(Problem("error", f"rules.{rule.id}", f"tests unknown fact {name!r}"))
                continue
            values = expected if isinstance(expected, list) else [expected]
            if spec.type == "enum":
                unknown = [v for v in values if v not in spec.values]
                if unknown:
                    out.append(
                        Problem(
                            "error",
                            f"rules.{rule.id}",
                            f"fact {name!r} cannot be {unknown!r}; allowed: {', '.join(spec.values)}",
                        )
                    )
            elif spec.type == "bool":
                if any(not isinstance(v, bool) for v in values):
                    out.append(Problem("error", f"rules.{rule.id}", f"fact {name!r} is a yes/no fact"))

        # The central invariant: nothing answers an employee on its own without a citation.
        if rule.mode == "auto" and _answers_a_person(rule) and not rule.needs_citation:
            out.append(
                Problem(
                    "error",
                    f"rules.{rule.id}",
                    "acts alone on an enquiry but does not require a citation",
                )
            )

        if rule.opens_case and rule.opens_case not in case_ids:
            out.append(Problem("error", f"rules.{rule.id}", f"opens unknown case type {rule.opens_case!r}"))

        if rule.when.get("sensitivity") == "restricted" and rule.mode in {"auto", "prepare"}:
            out.append(
                Problem(
                    "error",
                    f"rules.{rule.id}",
                    "a restricted matter may not be answered or drafted; it must hand over or refuse",
                )
            )

    if not any(r.when.get("sensitivity") == "restricted" for r in cfg.rules):
        out.append(Problem("error", "rules", "no rule handles restricted matters"))

    return out


def _answers_a_person(rule) -> bool:  # noqa: ANN001 - Rule, avoiding a circular import
    """An enquiry rule produces prose for an employee. Admin and document rules act."""
    intent = rule.when.get("intent")
    intents = intent if isinstance(intent, list) else [intent]
    return "enquiry" in intents


def _check_escalation(cfg: ResolvedConfig) -> list[Problem]:
    out: list[Problem] = []
    roles = cfg.approvals.roles
    esc = cfg.manifest.escalation
    for field, role in (
        ("restricted_role", esc.restricted_role),
        ("verification_failure_role", esc.verification_failure_role),
        ("default_role", esc.default_role),
    ):
        if role not in roles:
            out.append(Problem("error", f"manifest.escalation.{field}", f"unknown role {role!r}"))
    return out


def _check_adapters(cfg: ResolvedConfig) -> list[Problem]:
    out: list[Problem] = []
    kinds = {a.kind for a in cfg.adapters}
    if "hris" not in kinds:
        out.append(Problem("error", "adapters", "a company needs at least one hris adapter for facts"))
    if "notify" not in kinds:
        out.append(Problem("error", "adapters", "nothing can be sent without a notify adapter"))

    owners: dict[str, list[str]] = {}
    for adapter in cfg.adapters:
        if not adapter.operations:
            out.append(Problem("warning", f"adapters.{adapter.name}", "declares no operations, so it can do nothing"))
        for field in adapter.owns_fields:
            owners.setdefault(field, []).append(adapter.name)
    for field, names in owners.items():
        if len(names) > 1:
            out.append(Problem("error", "adapters", f"field {field!r} is owned by more than one adapter: {', '.join(names)}"))
    return out


def _check_policies(cfg: ResolvedConfig) -> list[Problem]:
    out: list[Problem] = []
    for policy in cfg.policies:
        in_force = [v for v in policy.versions if v.status in {"in_force", "scheduled"}]
        if not in_force:
            out.append(Problem("warning", f"policies.{policy.id}", "has no version in force, so it can never be quoted"))
        # Two versions claiming the SAME start date for the same population is
        # the data fault the agent refuses to guess past. Two with different
        # dates is ordinary supersession: the later one takes over on its day,
        # which is what effective dating is for.
        by_start: dict[tuple[str, str], list[str]] = {}
        for version in policy.versions:
            if version.status not in {"in_force", "scheduled"}:
                continue
            key = (version.effective_from or "", json.dumps(version.scope, sort_keys=True))
            by_start.setdefault(key, []).append(version.version)
        for (start, scope), versions in by_start.items():
            if len(versions) > 1:
                where = f" for {scope}" if scope != "{}" else ""
                out.append(
                    Problem(
                        "error",
                        f"policies.{policy.id}",
                        f"{len(versions)} versions start on {start or 'no date'}{where} "
                        f"({', '.join(versions)}); nothing can decide which applies",
                    )
                )
        versions = [v.version for v in policy.versions]
        if len(versions) != len(set(versions)):
            out.append(Problem("error", f"policies.{policy.id}", "version numbers repeat"))
        if policy.owner_role and policy.owner_role not in cfg.approvals.roles:
            out.append(Problem("error", f"policies.{policy.id}", f"owner_role {policy.owner_role!r} is not a role"))
    return out


def _check_readiness(cfg: ResolvedConfig) -> list[Problem]:
    """A readiness item nothing can answer is worse than no item at all: it
    would be reported as outstanding for every new starter, for ever."""
    out: list[Problem] = []
    owned = {f for adapter in cfg.adapters for f in adapter.owns_fields}
    has_docstore = any(a.kind == "docstore" for a in cfg.adapters)
    for item in cfg.readiness:
        if item.field and item.field not in owned:
            out.append(Problem("error", f"readiness.{item.id}",
                               f"checks field {item.field!r}, which no configured system owns"))
        if item.document and not has_docstore:
            out.append(Problem("error", f"readiness.{item.id}",
                               "checks a document, but this company has no docstore adapter"))
    if not cfg.readiness:
        out.append(Problem("warning", "readiness",
                           "no checklist, so onboarding readiness reports nothing and says so"))
    return out


def _check_forms(cfg: ResolvedConfig) -> list[Problem]:
    out: list[Problem] = []
    seen: dict[str, str] = {}
    for template in cfg.forms:
        for phrase in template.detect:
            key = phrase.lower()
            if key in seen and seen[key] != template.id:
                out.append(Problem("error", f"forms.{template.id}",
                                   f"recognised by {phrase!r}, which already identifies {seen[key]}"))
            seen[key] = template.id
        if not template.restricted and not template.fields:
            out.append(Problem("warning", f"forms.{template.id}",
                               "declares no fields, so it can never be read as complete or incomplete"))
    # A readiness item naming a document nothing can produce will never be met.
    known = {t.id for t in cfg.forms}
    for item in cfg.readiness:
        if item.document and known and item.document not in known:
            out.append(Problem("warning", f"readiness.{item.id}",
                               f"waits for document {item.document!r}, which matches no form template"))
    return out


def _check_exit_checklist(cfg: ResolvedConfig) -> list[Problem]:
    out: list[Problem] = []
    forms = {t.id for t in cfg.forms}
    for item in cfg.exit_checklist:
        if item.owner_role not in cfg.approvals.roles:
            out.append(Problem("error", f"exit_checklist.{item.id}",
                               f"owned by unknown role {item.owner_role!r}"))
        if item.document and forms and item.document not in forms:
            out.append(Problem("warning", f"exit_checklist.{item.id}",
                               f"waits for document {item.document!r}, which matches no form template"))
    return out


def _check_segments(cfg: ResolvedConfig) -> list[Problem]:
    out: list[Problem] = []
    for segment in cfg.segments:
        unknown = sorted(set(segment.match) - MATCHABLE)
        if unknown:
            out.append(
                Problem(
                    "error",
                    f"segments.{segment.id}",
                    f"matches on {', '.join(unknown)}; allowed: {', '.join(sorted(MATCHABLE))}",
                )
            )
        if not segment.match:
            out.append(Problem("error", f"segments.{segment.id}", "matches nothing, so it can never apply"))
        for role in segment.approvals_role_overrides.values():
            if role not in cfg.approvals.roles:
                out.append(Problem("error", f"segments.{segment.id}", f"overrides to unknown role {role!r}"))
    return out


def _check_people(cfg: ResolvedConfig) -> list[Problem]:
    out: list[Problem] = []
    ids = {p.id for p in cfg.people}
    roles = set(cfg.approvals.roles)
    segment_ids = {s.id for s in cfg.segments}
    for person in cfg.people:
        if person.manager_id and person.manager_id not in ids:
            out.append(Problem("error", f"people.{person.id}", f"manager {person.manager_id!r} is not in the directory"))
        if person.manager_id == person.id:
            out.append(Problem("error", f"people.{person.id}", "is their own manager"))
        for role in person.roles:
            if role not in roles:
                out.append(Problem("error", f"people.{person.id}", f"holds unknown role {role!r}"))
        for key in person.segment_keys:
            if segment_ids and key not in segment_ids:
                out.append(Problem("warning", f"people.{person.id}", f"segment key {key!r} matches no segment"))

    if cfg.approvals.hr_roles and not any(set(p.roles) & set(cfg.approvals.hr_roles) for p in cfg.people if p.active):
        out.append(Problem("error", "approvals.hr_roles", "no active person holds an HR role, so nobody can act for anyone else"))

    # Somebody must be able to receive every kind of handover.
    for field in ("restricted_role", "verification_failure_role", "default_role"):
        role = getattr(cfg.manifest.escalation, field)
        if role in roles and not any(role in p.roles for p in cfg.people if p.active):
            out.append(Problem("error", f"manifest.escalation.{field}", f"no active person holds {role!r}"))
    return out


def _check_golden_cases(cfg: ResolvedConfig) -> list[Problem]:
    out: list[Problem] = []
    ids = {p.id for p in cfg.people}
    rule_ids = {r.id for r in cfg.rules}
    for case in cfg.golden_cases:
        if case.requester not in ids:
            out.append(Problem("error", f"golden_cases.{case.id}", f"requester {case.requester!r} is not in the directory"))
        if case.subject and case.subject not in ids:
            out.append(Problem("error", f"golden_cases.{case.id}", f"subject {case.subject!r} is not in the directory"))
        if case.expect_rule and case.expect_rule not in rule_ids:
            out.append(Problem("error", f"golden_cases.{case.id}", f"expects unknown rule {case.expect_rule!r}"))
    if not cfg.golden_cases:
        out.append(Problem("warning", "golden_cases", "none defined, so this company has no acceptance tests"))
    return out
