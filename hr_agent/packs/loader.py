"""Reading packs off disk and layering them into one resolved configuration.

Resolution order, least to most specific:

    jurisdiction floor  ->  base pack  ->  tenant pack  ->  segment overlay

Never per-user. A later layer may only make the agent *more* restrictive; the
`Loosened` error is raised the moment a pack tries to do otherwise, so the
guarantee is enforced at load time rather than hoped for at runtime.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from .. import config
from .models import (
    Approvals,
    BasePack,
    CaseType,
    InterviewConfig,
    Locale,
    Manifest,
    RedactionPack,
    ResolvedConfig,
    Rule,
    RuleOverlay,
    Segment,
    TenantPack,
    restriction,
)


class PackError(Exception):
    """A pack could not be read or does not make sense."""


class Loosened(PackError):
    """A later layer tried to give the agent more freedom than the base allows."""


def read_yaml(path: Path, *, required: bool = True) -> Any:
    if not path.exists():
        if required:
            raise PackError(f"missing pack file: {path}")
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PackError(f"{path}: {exc}") from exc


# ------------------------------------------------------------------ base


def load_base(base_dir: Path | None = None) -> BasePack:
    root = base_dir or config.BASE_PACK_DIR
    return BasePack(
        taxonomy=read_yaml(root / "taxonomy.yaml"),
        cases=read_yaml(root / "cases.yaml")["cases"],
        rules=read_yaml(root / "rules.yaml")["rules"],
        redaction=read_yaml(root / "redaction" / "generic.yaml"),
    )


def load_redaction_pack(name: str, base_dir: Path | None = None) -> RedactionPack:
    root = base_dir or config.BASE_PACK_DIR
    return RedactionPack(**read_yaml(root / "redaction" / f"{name}.yaml"))


# ------------------------------------------------------------------ tenant


def tenant_ids(tenants_dir: Path | None = None) -> list[str]:
    root = tenants_dir or config.TENANTS_DIR
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / "manifest.yaml").exists())


def tenant_dir(tenant_id: str, tenants_dir: Path | None = None) -> Path:
    root = tenants_dir or config.TENANTS_DIR
    path = root / tenant_id
    if not (path / "manifest.yaml").exists():
        raise PackError(f"no pack for tenant {tenant_id!r} in {root}")
    return path


def load_tenant(tenant_id: str, tenants_dir: Path | None = None) -> TenantPack:
    root = tenant_dir(tenant_id, tenants_dir)

    def section(filename: str, key: str, *, required: bool = True, default: Any = None) -> Any:
        doc = read_yaml(root / filename, required=required)
        if doc is None:
            return default
        if key not in doc:
            raise PackError(f"{root / filename}: expected a top-level '{key}' key")
        return doc[key]

    manifest = Manifest(**read_yaml(root / "manifest.yaml"))
    if manifest.id != tenant_id:
        raise PackError(f"{root / 'manifest.yaml'}: id is {manifest.id!r} but the folder is {tenant_id!r}")

    policies = section("policies/versions.yaml", "policies", required=False, default=[]) or []

    pack = TenantPack(
        manifest=manifest,
        locale=Locale(**read_yaml(root / "locale.yaml")),
        cases=section("cases.yaml", "cases", required=False, default=[]) or [],
        rules=section("rules.yaml", "rules", required=False, default=[]) or [],
        approvals=Approvals(**read_yaml(root / "approvals.yaml")),
        adapters=section("adapters.yaml", "adapters"),
        redaction=(lambda d: RedactionPack(**d) if d else None)(read_yaml(root / "redaction.yaml", required=False)),
        segments=section("segments.yaml", "segments", required=False, default=[]) or [],
        people=section("people.yaml", "people", required=False, default=[]) or [],
        policies=policies,
        readiness=section("readiness.yaml", "items", required=False, default=[]) or [],
        forms=section("forms.yaml", "forms", required=False, default=[]) or [],
        exit_checklist=section("exit_checklist.yaml", "items", required=False, default=[]) or [],
        interviews=InterviewConfig(**(read_yaml(root / "interviews.yaml", required=False) or {})),
        golden_cases=section("golden_cases.yaml", "cases", required=False, default=[]) or [],
    )
    _check_policy_files(root, pack)
    return pack


def _check_policy_files(root: Path, pack: TenantPack) -> None:
    for policy in pack.policies:
        for version in policy.versions:
            path = root / "policies" / version.file
            if not path.exists():
                raise PackError(f"policy {policy.id} {version.version} names a missing file: policies/{version.file}")


# ------------------------------------------------------------------ layering


def _apply_overlay(rules: list[Rule], overlays: list[RuleOverlay], layer: str, provenance: dict[str, str]) -> list[Rule]:
    """Apply one layer of rule overlays, refusing anything that loosens."""
    by_id = {r.id: r for r in rules}
    for overlay in overlays:
        current = by_id.get(overlay.id)
        if current is None:
            raise PackError(f"{layer} overlay names unknown rule {overlay.id!r}")

        if overlay.mode is not None and overlay.mode != current.mode:
            if restriction(overlay.mode) < restriction(current.mode):
                raise Loosened(
                    f"{layer} tries to move rule {overlay.id} from {current.mode!r} to {overlay.mode!r}. "
                    "A later layer may only tighten."
                )
            provenance[f"rules.{overlay.id}.mode"] = layer

        if overlay.needs_citation is not None and overlay.needs_citation != current.needs_citation:
            if current.needs_citation and not overlay.needs_citation:
                raise Loosened(
                    f"{layer} tries to drop the citation requirement on rule {overlay.id}. "
                    "A citation requirement may only be added."
                )
            provenance[f"rules.{overlay.id}.needs_citation"] = layer

        if overlay.reason is not None:
            provenance[f"rules.{overlay.id}.reason"] = layer

        by_id[overlay.id] = current.model_copy(
            update={
                k: v
                for k, v in (
                    ("mode", overlay.mode),
                    ("needs_citation", overlay.needs_citation),
                    ("reason", overlay.reason),
                )
                if v is not None
            }
        )
    # Order is significant: the base pack's order is the evaluation order.
    return [by_id[r.id] for r in rules]


def _merge_redaction(base: RedactionPack, extra: RedactionPack | None) -> RedactionPack:
    if extra is None:
        return base
    names = {p.name for p in base.patterns}
    clashes = sorted(p.name for p in extra.patterns if p.name in names)
    if clashes:
        raise PackError(f"tenant redaction patterns reuse base names: {', '.join(clashes)}")
    return RedactionPack(
        id=f"{base.id}+{extra.id}",
        patterns=[*base.patterns, *extra.patterns],
        sensitive_keys=sorted({*base.sensitive_keys, *extra.sensitive_keys}),
    )


def config_hash(resolved: ResolvedConfig) -> str:
    """A stable fingerprint of the resolved configuration.

    Stamped onto every work item and decision, so a decision can always be traced
    back to the exact configuration that produced it.
    """
    payload = resolved.model_dump(mode="json", exclude={"provenance", "config_hash"})
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "cfg_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def resolve(base: BasePack, tenant: TenantPack, *, base_dir: Path | None = None) -> ResolvedConfig:
    """Layer a tenant pack onto the base pack and fingerprint the result."""
    provenance: dict[str, str] = {}

    for rule in base.rules:
        provenance.setdefault(f"rules.{rule.id}.mode", "base")
    rules = _apply_overlay(base.rules, tenant.rules, "tenant", provenance)

    cases: dict[str, CaseType] = {c.id: c for c in base.cases}
    for case in base.cases:
        provenance[f"cases.{case.id}"] = "base"
    for case in tenant.cases:
        cases[case.id] = case
        provenance[f"cases.{case.id}"] = "tenant"

    redaction = base.redaction
    for name in tenant.manifest.redaction_packs:
        if name != base.redaction.id:
            redaction = _merge_redaction(redaction, load_redaction_pack(name, base_dir))
    redaction = _merge_redaction(redaction, tenant.redaction)
    provenance["redaction"] = "base+tenant" if tenant.redaction else "base"

    for section in ("manifest", "locale", "approvals", "adapters", "people", "policies", "readiness", "forms", "exit_checklist", "golden_cases"):
        provenance[section] = "tenant"
    # A company that supplies no interviews.yaml gets the defaults, and the
    # provenance says so rather than implying it chose them.
    provenance["interviews"] = "tenant" if tenant.interviews != InterviewConfig() else "default"
    provenance["taxonomy"] = "base"
    for segment in tenant.segments:
        provenance[f"segments.{segment.id}"] = "tenant"

    resolved = ResolvedConfig(
        tenant_id=tenant.manifest.id,
        manifest=tenant.manifest,
        locale=tenant.locale,
        taxonomy=base.taxonomy,
        cases=list(cases.values()),
        rules=rules,
        approvals=tenant.approvals,
        adapters=tenant.adapters,
        redaction=redaction,
        segments=tenant.segments,
        people=tenant.people,
        policies=tenant.policies,
        readiness=tenant.readiness,
        forms=tenant.forms,
        exit_checklist=tenant.exit_checklist,
        interviews=tenant.interviews,
        golden_cases=tenant.golden_cases,
        provenance=provenance,
    )
    resolved.config_hash = config_hash(resolved)
    return resolved


def rules_for_segment(resolved: ResolvedConfig, segment_ids: list[str]) -> list[Rule]:
    """The rule set as it applies to one population. Segments tighten further."""
    rules = resolved.rules
    for segment in resolved.segments:
        if segment.id in segment_ids and segment.rules:
            rules = _apply_overlay(rules, segment.rules, f"segment:{segment.id}", {})
    return rules


def load_resolved(tenant_id: str, *, base_dir: Path | None = None, tenants_dir: Path | None = None) -> ResolvedConfig:
    base = load_base(base_dir)
    return resolve(base, load_tenant(tenant_id, tenants_dir), base_dir=base_dir)
