"""The shape of every pack file, as Pydantic models.

If a pack file does not match these models it does not load. That is the point:
a company's configuration is validated data, not prose a consultant interprets.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# How much the agent may do, ordered from most to least autonomous. The index is
# the whole enforcement mechanism for "a tenant may tighten, never loosen".
MODES: tuple[str, ...] = ("auto", "prepare", "hand_over", "refuse")

Mode = Literal["auto", "prepare", "hand_over", "refuse"]
Sensitivity = Literal["routine", "personal", "restricted"]


def restriction(mode: str) -> int:
    """Higher is more restrictive. `refuse` is the most restrictive outcome."""
    return MODES.index(mode)


class Strict(BaseModel):
    """Unknown keys are an error, so a typo in a pack file fails loudly."""

    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ base pack


class FactSpec(Strict):
    """A fact the rule engine may test. Declared here so rule predicates can be
    checked against it at load time rather than failing at runtime."""

    label: str
    type: Literal["enum", "bool", "int", "str"]
    values: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def enum_needs_values(self) -> FactSpec:
        if self.type == "enum" and not self.values:
            raise ValueError("an enum fact must list its values")
        return self


class Taxonomy(Strict):
    intents: dict[str, str]
    sensitivities: dict[str, str]
    facts: dict[str, FactSpec]
    # "address" -> "home_address". Maps what a person writes to the field name an
    # adapter may own, so `writes_owned_field` can be decided without guessing.
    field_hints: dict[str, str] = Field(default_factory=dict)

    @field_validator("sensitivities")
    @classmethod
    def three_levels(cls, v: dict[str, str]) -> dict[str, str]:
        if set(v) != {"routine", "personal", "restricted"}:
            raise ValueError("sensitivities are fixed: routine, personal, restricted")
        return v


class CaseType(Strict):
    """A kind of work that outlives one message.

    `intent` and `for_restricted` are how a new request is matched to a case
    that already exists, which is what makes a take-over apply to the matter a
    person took over rather than to the person themselves.
    """

    id: str
    label: str
    intent: str
    description: str
    for_restricted: bool = False


class Rule(Strict):
    """One row of the case matrix.

    `when` is a conjunction of fact tests. Rules are evaluated in file order and
    the first match wins, so the last rule must match everything.
    """

    id: str
    when: dict[str, Any] = Field(default_factory=dict)
    mode: Mode
    needs_citation: bool = False
    reason: str
    opens_case: str | None = None

    @field_validator("reason")
    @classmethod
    def readable(cls, v: str) -> str:
        # Every rule must be explainable to the person it affected.
        if len(v.split()) < 4:
            raise ValueError("reason must be a sentence a person can read")
        return v


class RedactionPattern(Strict):
    name: str
    klass: str = Field(alias="class")
    regex: str
    replacement: str

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class RedactionPack(Strict):
    id: str
    patterns: list[RedactionPattern] = Field(default_factory=list)
    sensitive_keys: list[str] = Field(default_factory=list)


class BasePack(Strict):
    """The shared ceiling. Identical for every customer."""

    taxonomy: Taxonomy
    cases: list[CaseType]
    rules: list[Rule]
    redaction: RedactionPack

    @model_validator(mode="after")
    def coherent(self) -> BasePack:
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("rule ids must be unique")
        if self.rules and self.rules[-1].when:
            raise ValueError("the last rule must match everything (empty `when`)")
        if self.rules and self.rules[-1].mode == "auto":
            raise ValueError("the catch-all rule may not act alone")
        return self

    def rule(self, rule_id: str) -> Rule | None:
        return next((r for r in self.rules if r.id == rule_id), None)

    def patterns_in_order(self) -> list[RedactionPattern]:
        """Redaction patterns as the pack lists them. Order is part of the meaning."""
        return list(self.redaction.patterns)


# ------------------------------------------------------------------ tenant pack


class WorkingHours(Strict):
    start: str
    end: str
    days: list[str]


class Locale(Strict):
    """Supplied, never assumed. No country is implied by anything here."""

    timezone: str
    date_format: str = "d MMM yyyy"
    week_start: str = "monday"
    languages: list[str] = Field(default_factory=lambda: ["en"])
    currency: str | None = None
    working_hours: WorkingHours
    holiday_calendar: str | None = None  # a file inside the pack, if the company has one


class RuleOverlay(Strict):
    """A tenant's change to one base rule. Tightening only."""

    id: str
    mode: Mode | None = None
    needs_citation: bool | None = None
    reason: str | None = None


class ApprovalRoute(Strict):
    """Who decides, when a decision is needed."""

    when: dict[str, Any] = Field(default_factory=dict)
    role: str
    then_role: str | None = None  # a second approver, for companies that need one


class Approvals(Strict):
    roles: dict[str, str]
    matrix: list[ApprovalRoute]
    delegation: dict[str, list[str]] = Field(default_factory=dict)
    # Roles the company treats as HR: they may act on another person's record.
    # Nothing in the kernel knows which role names mean "HR".
    hr_roles: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def roles_exist(self) -> Approvals:
        for route in self.matrix:
            for role in (route.role, route.then_role):
                if role and role not in self.roles:
                    raise ValueError(f"approval route names unknown role {role!r}")
        for holder, deputies in self.delegation.items():
            for role in [holder, *deputies]:
                if role not in self.roles:
                    raise ValueError(f"delegation names unknown role {role!r}")
        for role in self.hr_roles:
            if role not in self.roles:
                raise ValueError(f"hr_roles names unknown role {role!r}")
        return self


class Adapter(Strict):
    """A system of record. It declares what it owns; the kernel refuses anything else."""

    name: str
    kind: Literal["hris", "calendar", "itsm", "docstore", "notify", "esign"]
    implementation: str
    owns_fields: list[str] = Field(default_factory=list)
    operations: list[str] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)

    @field_validator("owns_fields")
    @classmethod
    def no_pay_or_access(cls, v: list[str]) -> list[str]:
        banned = {"salary", "pay", "pay_rate", "base_pay", "bonus", "access", "permissions", "roles", "entitlements"}
        bad = sorted(set(v) & banned)
        if bad:
            raise ValueError(f"no adapter may own pay or access fields: {', '.join(bad)}")
        return v


class Segment(Strict):
    """A population inside one company. Overlays resolve after the tenant layer."""

    id: str
    label: str
    match: dict[str, Any]
    rules: list[RuleOverlay] = Field(default_factory=list)
    approvals_role_overrides: dict[str, str] = Field(default_factory=dict)


class ModelRouting(Strict):
    """Which provider answers for this company. `null` means no model at all:
    rules, retrieval and templates only, which must always remain a working mode."""

    provider: Literal["null", "local", "anthropic", "bedrock", "vertex", "foundry"] = "null"
    draft_model: str | None = None
    classify_model: str | None = None
    verify_model: str | None = None
    residency: str | None = None
    train_on_tenant_data: Literal[False] = False  # never, unless a contract says otherwise


class Metrics(Strict):
    """How this company wants its own saving measured.

    `baseline_minutes_per_case` is the company's own estimate of how long one of
    these took a person before. There is no default: with nothing supplied, the
    metrics screen reports that no saving can be claimed rather than inventing one.
    """

    baseline_minutes_per_case: int | None = None
    review_minutes_per_queued_case: int = 3
    small_sample_below: int = 30


class Sla(Strict):
    onboarding_lead_days: int = 30   # how early a readiness check is useful
    acknowledge_minutes: int = 5
    answer_hours: int = 24
    restricted_acknowledge_minutes: int = 2
    reminder_cap: int = 2
    reminder_interval_hours: int = 48


class Escalation(Strict):
    """Where handovers go. Roles, never named individuals, so people can change."""

    restricted_role: str
    verification_failure_role: str
    default_role: str


class Manifest(Strict):
    id: str
    name: str
    extends: str = "base"
    channels: list[str] = Field(default_factory=lambda: ["web"])
    tone: Literal["plain", "warm", "formal"] = "plain"
    sla: Sla = Field(default_factory=Sla)
    escalation: Escalation
    redaction_packs: list[str] = Field(default_factory=lambda: ["generic"])
    models: ModelRouting = Field(default_factory=ModelRouting)
    metrics: Metrics = Field(default_factory=Metrics)
    jurisdiction_packs: list[str] = Field(default_factory=list)  # empty in v1, by design


class Person(Strict):
    """Mock directory entry. Real deployments read this from an HRIS adapter."""

    id: str
    name: str
    email: str | None = None
    manager_id: str | None = None
    entity: str | None = None
    employment_type: str | None = None
    segment_keys: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    timezone: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    active: bool = True


class PolicyVersionDecl(Strict):
    version: str
    status: Literal["draft", "scheduled", "in_force", "retired"]
    effective_from: str | None = None
    effective_to: str | None = None
    scope: dict[str, Any] = Field(default_factory=dict)
    file: str


class PolicyDecl(Strict):
    id: str
    title: str
    owner_role: str | None = None
    review_months: int | None = None
    versions: list[PolicyVersionDecl]


class ExitItem(Strict):
    """One step on a company's departure checklist.

    Unlike a readiness item, most of these are tasks a person performs rather
    than facts a system holds — returning a laptop, a handover conversation.
    Where a system CAN answer (a document returned), it is asked; where it
    cannot, the item is reported as needing a person rather than guessed at.

    Nothing here revokes access. No tool in this system can, by design.
    """

    id: str
    label: str
    owner_role: str
    document: str | None = None
    before_last_day: bool = True


class FormField(Strict):
    """One field on a form, and the labels a person might write for it."""

    id: str
    label: str
    aliases: list[str] = Field(default_factory=list)
    required: bool = True


class FormTemplate(Strict):
    """A form this company receives.

    `restricted` is a property of the TYPE, not of the contents. A medical
    certificate is restricted whether or not it happens to mention anything
    sensitive, because deciding that by reading it means reading it.
    """

    id: str
    label: str
    version: str
    detect: list[str]
    fields: list[FormField] = Field(default_factory=list)
    restricted: bool = False
    expires_after_months: int | None = None

    @model_validator(mode="after")
    def sensible(self) -> FormTemplate:
        if not self.detect:
            raise ValueError("a form template needs at least one phrase to recognise it by")
        if self.restricted and self.fields:
            raise ValueError("a restricted form must declare no fields: it is never read")
        ids = [f.id for f in self.fields]
        if len(ids) != len(set(ids)):
            raise ValueError("field ids must be unique within a form")
        return self


class ReadinessItem(Strict):
    """One thing that must be true before a new starter's first day.

    `field` is satisfied when the owning system holds a value for it.
    `document` is satisfied when a document of that type has been filed.
    Exactly one of the two, so the check is always answerable from a system
    rather than from an opinion.
    """

    id: str
    label: str
    field: str | None = None
    document: str | None = None
    blocking: bool = False      # a start date cannot be confirmed without it

    @model_validator(mode="after")
    def one_source(self) -> ReadinessItem:
        if bool(self.field) == bool(self.document):
            raise ValueError("a readiness item checks exactly one of `field` or `document`")
        return self


class InterviewStage(Strict):
    """One kind of interview this company runs.

    A stage exists so that "schedule a screening call" and "schedule the final
    panel" do not have to mean the same thing. Duration and panel size are the
    company's, because a 30-minute screen and a 90-minute panel are not
    interchangeable and nothing in the kernel should have an opinion about which
    a company uses.
    """

    id: str
    label: str
    duration_minutes: int
    detect: list[str] = Field(default_factory=list)   # phrases that name this stage
    panel_min: int = 1
    panel_roles: list[str] = Field(default_factory=list)   # roles that must be present

    @model_validator(mode="after")
    def sensible(self) -> InterviewStage:
        if self.duration_minutes <= 0:
            raise ValueError("an interview stage must last longer than no time at all")
        if self.panel_min < 1:
            raise ValueError("an interview needs at least one interviewer")
        return self


class InterviewConfig(Strict):
    """How this company schedules interviews.

    Every number here is an operational choice a company actually differs on, so
    every one is config. The kernel supplies no statutory notice period and no
    working-time arithmetic: `min_notice_hours` is this company's courtesy to a
    candidate, not a legal calculation, and that distinction is the same one that
    keeps the rest of this system jurisdiction-neutral.
    """

    default_duration_minutes: int = 60
    # How much warning a candidate and a panel get. Counted from the start of the
    # working day the request is made on, because the kernel runs on dates.
    min_notice_hours: int = 24
    # Dead time either side of an interview, so a panel is not booked wall to wall.
    buffer_minutes: int = 15
    # Interviewing is tiring and a tired panel interviews badly.
    max_per_day_per_interviewer: int = 3
    # How far forward to look for a time, when the request did not name a day.
    search_days: int = 10
    # How many times to offer. More than a handful is not a choice, it is a chore.
    propose_slots: int = 3
    # Minutes between candidate start times. 15 fits most calendars' own grid.
    slot_step_minutes: int = 15
    stages: list[InterviewStage] = Field(default_factory=list)
    default_location: str | None = None
    # Whether the agent prepares a message to each party when a time is approved.
    # Preparing is not sending: an approval still commits it.
    notify_candidate: bool = True
    notify_panel: bool = True

    @model_validator(mode="after")
    def coherent(self) -> InterviewConfig:
        ids = [s.id for s in self.stages]
        if len(ids) != len(set(ids)):
            raise ValueError("interview stage ids must be unique")
        if self.slot_step_minutes <= 0:
            raise ValueError("the slot step must be a positive number of minutes")
        if self.propose_slots < 1:
            raise ValueError("proposing no times is not a proposal")
        if self.search_days < 1:
            raise ValueError("the search has to cover at least one day")
        return self

    def stage(self, stage_id: str | None) -> InterviewStage | None:
        return next((s for s in self.stages if s.id == stage_id), None)

    def minutes_for(self, stage: InterviewStage | None) -> int:
        return stage.duration_minutes if stage else self.default_duration_minutes


class GoldenCase(Strict):
    """A case this company expects the agent to handle a particular way.
    `hrctl simulate` runs these; they are the company's own acceptance tests."""

    id: str
    message: str
    requester: str
    subject: str | None = None
    expect_rule: str | None = None
    expect_mode: Mode | None = None
    note: str | None = None
    # A document arriving with the message. Without this, document intake -- the
    # largest piece of real HR work the agent does -- could not be expressed as
    # a company's own acceptance case, so rules D0 to D4 were never exercised by
    # any pack and never appeared on any screen.
    attachment: dict[str, Any] | None = None


class TenantPack(Strict):
    manifest: Manifest
    locale: Locale
    cases: list[CaseType] = Field(default_factory=list)
    rules: list[RuleOverlay] = Field(default_factory=list)
    approvals: Approvals
    adapters: list[Adapter]
    redaction: RedactionPack | None = None
    segments: list[Segment] = Field(default_factory=list)
    people: list[Person] = Field(default_factory=list)
    policies: list[PolicyDecl] = Field(default_factory=list)
    readiness: list[ReadinessItem] = Field(default_factory=list)
    forms: list[FormTemplate] = Field(default_factory=list)
    exit_checklist: list[ExitItem] = Field(default_factory=list)
    interviews: InterviewConfig = Field(default_factory=InterviewConfig)
    golden_cases: list[GoldenCase] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_ids(self) -> TenantPack:
        for label, values in (
            ("adapter", [a.name for a in self.adapters]),
            ("segment", [s.id for s in self.segments]),
            ("person", [p.id for p in self.people]),
            ("policy", [p.id for p in self.policies]),
            ("golden case", [g.id for g in self.golden_cases]),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} ids must be unique")
        # A panel requirement naming a role nobody holds can never be satisfied,
        # and would silently stop every interview of that stage being proposed.
        for stage in self.interviews.stages:
            for role in stage.panel_roles:
                if role not in self.approvals.roles:
                    raise ValueError(
                        f"interview stage {stage.id!r} requires unknown role {role!r}"
                    )
        return self


# ------------------------------------------------------------------ resolved


class ResolvedConfig(BaseModel):
    """What the kernel actually runs against, after layering.

    `provenance` records which layer set each value, so `hrctl show config`
    can answer "why is it like this" without anyone reading five files.
    """

    tenant_id: str
    manifest: Manifest
    locale: Locale
    taxonomy: Taxonomy
    cases: list[CaseType]
    rules: list[Rule]
    approvals: Approvals
    adapters: list[Adapter]
    redaction: RedactionPack
    segments: list[Segment]
    people: list[Person]
    policies: list[PolicyDecl]
    readiness: list[ReadinessItem]
    forms: list[FormTemplate]
    exit_checklist: list[ExitItem]
    interviews: InterviewConfig
    golden_cases: list[GoldenCase]
    provenance: dict[str, str] = Field(default_factory=dict)
    config_hash: str = ""

    def rule(self, rule_id: str) -> Rule | None:
        return next((r for r in self.rules if r.id == rule_id), None)

    def adapter_owning(self, field: str) -> Adapter | None:
        return next((a for a in self.adapters if field in a.owns_fields), None)
