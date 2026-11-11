"""Redaction. Nothing leaves this process, reaches a model, or is written to the
audit trail until it has been through here.

Two jobs:

    text(...)     mask identifiers in free text, keeping the class label so a
                  reviewer can still read the document and see what was removed
    mapping(...)  drop fields whose *name* says they are sensitive, before any
                  structure is stored or logged

Not one identifier format is written in this file. Patterns come from the pack:
`packs/base/redaction/generic.yaml` for generic classes, and a company's own
`redaction.yaml` for its local formats. That is the whole reason the kernel stays
jurisdiction-neutral.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from . import config
from .packs.models import RedactionPack

MAX_DEPTH = 12  # deep or self-referential structures are truncated, not followed forever
DROPPED = "[dropped]"


def _words(name: str) -> tuple[str, ...]:
    """Split a field name into words, however it was written.

    `bank_details`, `bankDetails`, `Bank-Details` and `BANK DETAILS` all become
    `("bank", "details")`, so the way a schema spells a field cannot hide it.
    """
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return tuple(w for w in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if w)


class RedactionDisabled(RuntimeError):
    """Something tried to run with redaction switched off."""


class Leak(AssertionError):
    """Text that should have been clean still contains an identifier."""


def guard() -> None:
    """Called before anything that would send text onwards.

    The setting exists only so a test can prove this refusal happens; there is no
    supported way to run the agent with redaction off.
    """
    if not config.REDACTION_ENABLED:
        raise RedactionDisabled(
            "redaction is disabled, so nothing may be sent, stored or shown. Unset HR_REDACTION."
        )


@dataclass(frozen=True)
class Finding:
    """One thing that was masked. Counted in metrics; never stores what it saw."""

    pattern: str
    klass: str
    count: int


class Redactor:
    """Compiled, reusable. Build one per tenant from the resolved pack."""

    def __init__(self, pack: RedactionPack) -> None:
        self.pack_id = pack.id
        # Order is significant and comes from the pack: specific shapes first.
        self._patterns = [
            (p.name, p.klass, re.compile(p.regex), p.replacement) for p in pack.patterns
        ]
        self._sensitive_words = {_words(k) for k in pack.sensitive_keys}

    # ---------------------------------------------------------------- text

    def text(self, value: str) -> str:
        """Mask every identifier the pack knows about."""
        guard()
        if not value:
            return value
        out = value
        for _name, _klass, regex, replacement in self._patterns:
            out = regex.sub(replacement, out)
        return out

    def findings(self, value: str) -> list[Finding]:
        """What was in there, by class. Used for audit detail and metrics."""
        if not value:
            return []
        found = []
        for name, klass, regex, _replacement in self._patterns:
            if count := len(regex.findall(value)):
                found.append(Finding(name, klass, count))
        return found

    def matches(self, value: str) -> list[str]:
        """The actual identifier text found, not just which classes matched.

        Used to tell a leak from an echo: an identifier the person supplied in
        their own message, repeated back for them to confirm, is not a leak.
        """
        out: list[str] = []
        for _name, _klass, regex, _replacement in self._patterns:
            for match in regex.finditer(value or ""):
                # Group 2 is the value in a keyed pattern; group 0 otherwise.
                found = match.group(2) if match.re.groups >= 2 else match.group(0)
                out.append(found.strip())
        return out

    def is_clean(self, value: str) -> bool:
        return not self.findings(value)

    def assert_clean(self, value: str, where: str = "outbound text") -> None:
        """Raise if anything is still in the clear. Used on every outbound message."""
        if leaks := self.findings(value):
            classes = ", ".join(sorted({f.klass for f in leaks}))
            raise Leak(f"{where} still contains: {classes}")

    # ---------------------------------------------------------------- structures

    def mapping(self, value: Any, _depth: int = 0) -> Any:
        """Redact a structure: drop sensitive keys by name, mask strings in place.

        A key named `bank_account` is dropped whatever it holds, because a field
        name is a more reliable signal than a regex over its contents.
        """
        guard()
        if _depth >= MAX_DEPTH:
            return DROPPED
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, Mapping):
            out: dict[str, Any] = {}
            for key, item in value.items():
                if self._is_sensitive_key(key):
                    out[str(key)] = DROPPED
                else:
                    out[str(key)] = self.mapping(item, _depth + 1)
            return out
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [self.mapping(item, _depth + 1) for item in value]
        return value

    def _is_sensitive_key(self, key: Any) -> bool:
        """Match when a sensitive key appears as whole words inside the field name.

        Real schemas prefix and suffix with equal enthusiasm -- `employee_salary`
        and `salary_band` are both pay -- so the sensitive name is matched as a
        contiguous run of words anywhere in the field name.

        Matching whole words rather than substrings is what keeps `bank_holiday`
        and `tokenization_policy` readable while still dropping `api_token`. The
        bias is deliberately towards dropping: a field whose name contains
        `token` goes, including `token_count`. Over-redacting an audit detail is
        recoverable; under-redacting it is not.
        """
        words = _words(str(key))
        return any(
            any(words[i : i + len(sensitive)] == sensitive for i in range(len(words) - len(sensitive) + 1))
            for sensitive in self._sensitive_words
        )


def for_tenant(resolved) -> Redactor:  # noqa: ANN001 - ResolvedConfig, avoiding a circular import
    return Redactor(resolved.redaction)
