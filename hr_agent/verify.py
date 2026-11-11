"""The verify gate: the last thing between a draft and the world.

Every check returns pass, fail or skip with a reason, and the whole list is
stored on the decision and drawn in the UI. A failure never degrades into
sending: the kernel escalates to the role the company nominated.

The checks are deliberately mechanical. "Does the draft contain the exact words
of the policy it claims to quote" is a string comparison, and that is the point --
a gate you can reason about is a gate you can trust.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .policy import Citation
from .redact import Redactor

# Telling a person what THEY personally get. The agent may say what a policy
# says; it may never apply it to an individual, because it cannot verify their
# circumstances and the company would be bound by the answer.
PERSONAL_CLAIMS = [
    (r"\byou(?:'re| are) entitled\b", "tells the person what they are entitled to"),
    (r"\byour entitlement is\b", "states the person's entitlement"),
    (r"\byou (?:have|get) \d+\s*(?:days?|weeks?|months?)\b", "states a personal balance"),
    (r"\byou have \d+ (?:days?|weeks?) (?:remaining|left)\b", "states a personal balance"),
    (r"\byou (?:qualify|are eligible)\b", "decides eligibility"),
    (r"\byou will (?:receive|be paid|get)\b", "promises a payment or benefit"),
    (r"\bi (?:have )?approved?\b", "claims to have approved something"),
    (r"\byour request (?:is|has been) approved\b", "claims an approval"),
    (r"\bi can confirm (?:you|that you)\b", "confirms something about the person"),
    (r"\byou are owed\b", "states money owed"),
    (r"\byou have completed \d+\b", "states service the agent cannot verify"),
]

# Legal, medical and financial advice. Never, in any mode.
ADVICE = [
    (r"\byou should (?:see|consult|visit) a (?:doctor|gp|lawyer|solicitor|advisor|adviser)\b", "gives medical or legal advice"),
    (r"\byou have a (?:strong|good|weak) case\b", "assesses a legal position"),
    (r"\byour diagnosis\b", "comments on a diagnosis"),
    (r"\b(?:this|that) (?:fund|scheme|option|plan) (?:is|would be) better for you\b", "gives financial advice"),
    (r"\bi (?:recommend|advise) (?:you|that you) (?:invest|claim|sue|resign|accept)\b", "advises a course of action"),
    (r"\byou are (?:legally )?(?:entitled|obliged) under (?:the )?law\b", "states a legal position"),
]

MIN_WORDS = 12

# The only modes that produce an answer. A handover and a refusal answer nothing,
# so a citation requirement carried by the rule does not apply to them -- and
# must not, or tightening a citation-carrying rule into a handover (which a
# segment overlay is entitled to do) would fail verification for no reason.
ANSWERING_MODES = {"auto", "prepare"}


@dataclass
class Check:
    id: str
    label: str
    status: str          # pass | fail | skip
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label, "status": self.status, "detail": self.detail}


@dataclass
class Result:
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(c.status == "fail" for c in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status == "fail"]

    def as_list(self) -> list[dict[str, str]]:
        return [c.as_dict() for c in self.checks]

    def __str__(self) -> str:
        if self.passed:
            return f"passed {len(self.checks)} check(s)"
        return "failed: " + "; ".join(c.detail for c in self.failures)


def _normalise(text: str) -> str:
    """Compare on words, so a line break or double space is not a failure."""
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _is_echo(found: str, source: str) -> bool:
    """Did the person supply this identifier themselves?

    Repeating back a value somebody just gave you, so they can confirm it before
    it is written to their own record, is not a leak -- and a preview that hides
    the value cannot be checked, which defeats the point of a preview. An
    identifier the person did NOT supply is a different matter entirely: it came
    from a record, and it is leaving.
    """
    needle = re.sub(r"\s+", " ", found).strip().lower()
    haystack = re.sub(r"\s+", " ", source or "").lower()
    return bool(needle) and needle in haystack


def _is_own(found: str, declared: set[str] | None) -> bool:
    """Is this an address the company published about itself?

    A company's own recruitment mailbox is not somebody's personal identifier.
    It is written in the pack precisely so that outbound mail can carry it, and a
    gate that treats it as a leak either blocks every email the agent prepares or
    forces the preview to hide which mailbox a candidate would be told to reply
    to -- and an approver who cannot see that cannot approve it.

    This does not widen the gate. The permitted set is exactly what the resolved
    configuration declares, and configuration cannot be altered by a message, an
    attachment or an injected instruction: it is loaded from the pack, validated,
    and fingerprinted onto the decision.
    """
    if not declared:
        return False
    return re.sub(r"\s+", "", found).strip().lower() in declared


def verify(
    *,
    draft: str,
    mode: str,
    sensitivity: str,
    needs_citation: bool,
    citations: list[Citation],
    plans: list[dict[str, Any]] | None = None,
    redactor: Redactor | None = None,
    in_force_versions: dict[str, str] | None = None,
    source: str = "",
    own_addresses: set[str] | None = None,
) -> Result:
    """Run every check. The caller decides what to do with a failure; this does not."""
    checks: list[Check] = []
    plans = plans or []
    body = draft or ""
    normalised = _normalise(body)

    # ---------------------------------------------------------- citations
    if needs_citation and mode not in ANSWERING_MODES:
        checks.append(Check("citation_present", "A policy is cited", "skip",
                            f"a {mode.replace('_', ' ')} answers nothing, so there is nothing to quote"))
    elif needs_citation:
        if citations:
            checks.append(Check("citation_present", "A policy is cited", "pass",
                                ", ".join(c.label for c in citations)))
        else:
            checks.append(Check("citation_present", "A policy is cited", "fail",
                                "this rule may only answer by quoting a policy, and nothing was cited"))
    else:
        checks.append(Check("citation_present", "A policy is cited", "skip",
                            "this rule does not answer from policy"))

    if citations:
        missing = [c for c in citations if _normalise(c.quoted_text) not in normalised]
        if missing:
            checks.append(Check("citation_quoted", "The cited words appear in the reply", "fail",
                                f"{len(missing)} citation(s) are claimed but not quoted: "
                                + "; ".join(c.label for c in missing)))
        else:
            checks.append(Check("citation_quoted", "The cited words appear in the reply", "pass",
                                f"all {len(citations)} citation(s) are quoted verbatim"))

        by_doc: dict[str, set[str]] = {}
        for citation in citations:
            by_doc.setdefault(citation.doc_id, set()).add(citation.version)
        mixed = {d: v for d, v in by_doc.items() if len(v) > 1}
        checks.append(
            Check("one_version", "One version per policy", "fail",
                  "two versions of the same policy are quoted: "
                  + "; ".join(f"{d} {sorted(v)}" for d, v in mixed.items()))
            if mixed else
            Check("one_version", "One version per policy", "pass", "each policy is quoted at one version")
        )

        if in_force_versions is not None:
            stale = [c for c in citations if in_force_versions.get(c.doc_id) != c.version]
            checks.append(
                Check("still_in_force", "The cited version is still in force", "fail",
                      "the policy changed since this was drafted: "
                      + "; ".join(f"{c.doc_id} v{c.version} is no longer current" for c in stale))
                if stale else
                Check("still_in_force", "The cited version is still in force", "pass",
                      "every cited version is current")
            )
    else:
        checks.append(Check("citation_quoted", "The cited words appear in the reply", "skip", "nothing cited"))
        checks.append(Check("one_version", "One version per policy", "skip", "nothing cited"))

    # ---------------------------------------------------------- what the draft says
    found = [why for pattern, why in PERSONAL_CLAIMS if re.search(pattern, normalised)]
    checks.append(
        Check("no_personal_claim", "No claim about this person's entitlement", "fail",
              "; ".join(sorted(set(found))))
        if found else
        Check("no_personal_claim", "No claim about this person's entitlement", "pass",
              "the reply says what the policy says, not what the person gets")
    )

    found = [why for pattern, why in ADVICE if re.search(pattern, normalised)]
    checks.append(
        Check("no_advice", "No legal, medical or financial advice", "fail", "; ".join(sorted(set(found))))
        if found else
        Check("no_advice", "No legal, medical or financial advice", "pass", "none given")
    )

    if sensitivity == "restricted":
        if citations or len(body.split()) > 120:
            checks.append(Check("restricted_not_answered", "A restricted matter was not answered", "fail",
                                "a restricted matter must be acknowledged and routed, never answered"))
        else:
            checks.append(Check("restricted_not_answered", "A restricted matter was not answered", "pass",
                                "acknowledged and routed to a person, with no answer drafted"))
    else:
        checks.append(Check("restricted_not_answered", "A restricted matter was not answered", "skip",
                            "not a restricted matter"))

    words = len(body.split())
    checks.append(
        Check("substance", "The reply says something", "pass", f"{words} words")
        if words >= MIN_WORDS else
        Check("substance", "The reply says something", "fail",
              f"only {words} words, which is not an answer")
    )

    if redactor is not None:
        present = redactor.matches(body)
        leaked = [
            found for found in present
            if not _is_echo(found, source) and not _is_own(found, own_addresses)
        ]
        if leaked:
            checks.append(Check(
                "no_identifiers", "No identifier the person did not supply", "fail",
                f"the reply contains {len(leaked)} identifier(s) that did not come from the request "
                "or from this company's own configuration",
            ))
        elif present:
            checks.append(Check(
                "no_identifiers", "No identifier the person did not supply", "pass",
                f"{len(present)} identifier(s) present, each either supplied by the person and "
                "repeated back to confirm, or an address this company publishes about itself",
            ))
        elif redactor.findings(body):
            checks.append(Check(
                "no_identifiers", "No identifier the person did not supply", "pass",
                "the only identifiers present are ones the person supplied, repeated back to confirm",
            ))
        else:
            checks.append(Check("no_identifiers", "No identifier the person did not supply", "pass", "clean"))

    # ---------------------------------------------------------- what it would do
    if plans:
        unkeyed = [p for p in plans if not p.get("idempotency_key")]
        checks.append(
            Check("plans_keyed", "Every action has an idempotency key", "fail",
                  f"{len(unkeyed)} action(s) have no key and could run twice")
            if unkeyed else
            Check("plans_keyed", "Every action has an idempotency key", "pass",
                  f"{len(plans)} action(s), each with a key")
        )
        committed = [p for p in plans if p.get("status") == "committed"]
        if mode == "prepare" and committed:
            checks.append(Check("nothing_committed_early", "Nothing acted before approval", "fail",
                                f"{len(committed)} action(s) were committed under a decision that only allows preparing"))
        else:
            checks.append(Check("nothing_committed_early", "Nothing acted before approval", "pass",
                                "no action has run yet" if mode == "prepare" else "committing is allowed here"))
        keys = [p.get("idempotency_key") for p in plans if p.get("idempotency_key")]
        checks.append(
            Check("keys_unique", "No two actions share a key", "fail", "a key is reused within one run")
            if len(keys) != len(set(keys)) else
            Check("keys_unique", "No two actions share a key", "pass", "every key is distinct")
        )
    else:
        for check_id, label in (
            ("plans_keyed", "Every action has an idempotency key"),
            ("nothing_committed_early", "Nothing acted before approval"),
            ("keys_unique", "No two actions share a key"),
        ):
            checks.append(Check(check_id, label, "skip", "this outcome does nothing to the world"))

    return Result(checks)
