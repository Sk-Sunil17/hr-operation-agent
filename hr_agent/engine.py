"""The rule engine: the only thing that decides how much the agent may do.

Rules come from the pack, are evaluated in pack order, and the first match wins.
The last rule matches everything, so there is no path off the end of the list.

Three properties this module exists to guarantee:

  * No model is consulted. The engine reads facts, which come from systems.
  * A predicate that cannot be evaluated does NOT match. A broken or missing
    fact closes the gate; it never opens it.
  * The decision carries the rule id, the facts it fired on, and the pack's own
    reason, so the outcome can be explained to the person it affected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .packs.loader import rules_for_segment
from .packs.models import ResolvedConfig, Rule


@dataclass(frozen=True)
class Verdict:
    """What the engine decided, and everything needed to explain it."""

    rule_id: str
    mode: str
    needs_citation: bool
    reason: str
    opens_case: str | None
    facts: dict[str, Any]
    matched_on: dict[str, Any] = field(default_factory=dict)
    considered: list[str] = field(default_factory=list)
    segment_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "mode": self.mode,
            "needs_citation": self.needs_citation,
            "reason": self.reason,
            "opens_case": self.opens_case,
            "matched_on": self.matched_on,
            "considered": self.considered,
            "segments": self.segment_ids,
        }


def test_predicate(expected: Any, actual: Any) -> bool:
    """One fact test. A fact that is not known never satisfies a predicate."""
    if actual is None:
        return False
    if isinstance(expected, list):
        return actual in expected
    return actual == expected


def matches(rule: Rule, facts: dict[str, Any]) -> bool:
    """Every test in `when` must hold. An empty `when` matches everything."""
    try:
        return all(test_predicate(expected, facts.get(name)) for name, expected in rule.when.items())
    except Exception:
        # A predicate that blows up is a closed gate, not an open one.
        return False


def evaluate(cfg: ResolvedConfig, facts: dict[str, Any], *, segment_ids: list[str] | None = None) -> Verdict:
    """Decide the mode for one request.

    Segment overlays are applied here rather than at load time, because which
    population a person belongs to is a fact about them, not about the company.
    """
    segments = segment_ids or []
    rules = rules_for_segment(cfg, segments) if segments else cfg.rules

    considered: list[str] = []
    for rule in rules:
        considered.append(rule.id)
        if matches(rule, facts):
            return Verdict(
                rule_id=rule.id,
                mode=rule.mode,
                needs_citation=rule.needs_citation,
                reason=rule.reason,
                opens_case=rule.opens_case,
                facts=facts,
                matched_on={name: facts.get(name) for name in rule.when},
                considered=considered,
                segment_ids=segments,
            )

    # Unreachable while the pack is valid: the loader requires a catch-all rule.
    # If it ever happens, hand over rather than invent a decision.
    return Verdict(
        rule_id="NONE",
        mode="hand_over",
        needs_citation=False,
        reason="No rule matched, including the catch-all. A person must decide what this is.",
        opens_case="unresolved",
        facts=facts,
        considered=considered,
        segment_ids=segments,
    )


def segments_for(cfg: ResolvedConfig, person) -> list[str]:  # noqa: ANN001 - Person
    """Which populations a person belongs to, by the pack's own match rules."""
    if person is None:
        return []
    out = []
    for segment in cfg.segments:
        if all(_segment_field_matches(getattr(person, key, None), expected) for key, expected in segment.match.items()):
            out.append(segment.id)
    return out


def _segment_field_matches(actual: Any, expected: Any) -> bool:
    if actual is None:
        return False
    if isinstance(actual, list):
        wanted = expected if isinstance(expected, list) else [expected]
        return any(w in actual for w in wanted)
    if isinstance(expected, list):
        return actual in expected
    return actual == expected
