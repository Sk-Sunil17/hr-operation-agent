"""Reading a form that arrived.

Three things this does, and one it refuses to do.

    detect   which of this company's templates this is, if any
    read     what each declared field says, and what it does not
    judge    complete or not, with the missing fields named

What it refuses: reading a restricted type at all. A medical certificate is
restricted because of what kind of document it is, not because of what it turns
out to say -- deciding that by reading it means reading it.

Two distinctions matter more than they look:

  * blank is not the same as illegible. "I could not read this" and "they left
    it empty" call for different actions from a person, and collapsing them
    loses the one piece of information that decides which.
  * a label nobody declared is surfaced, not dropped. A form with an extra
    field on it is usually a form that has changed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .packs.models import FormTemplate, ResolvedConfig

# The many ways a person writes nothing. All of these mean the field is empty,
# and treating any of them as a value files "N/A" as somebody's bank account.
BLANKS = {
    "", "-", "--", "—", "n/a", "na", "n.a.", "nil", "none", "not applicable",
    "tbc", "tba", "to follow", "unknown", "?", "??", "???", "[blank]", "(blank)",
    "x", "none given", "not known", "blank",
}

# Written when a person could not make the writing out. Different from blank.
ILLEGIBLE = {
    "[illegible]", "(illegible)", "illegible", "unreadable", "[unreadable]",
    "can't read", "cannot read", "[can't read]", "unclear", "[unclear]",
}

# Page furniture that is not a field, however much it looks like one.
FURNITURE = re.compile(
    r"^(?:page\s*\d+|\d+\s*of\s*\d+|continued|signature|signed|date signed|"
    r"for office use(?: only)?|office use|please return to|version|form\s*[a-z0-9-]*)\s*$",
    re.I,
)

LINE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 /'()\-\.]{1,48}?)\s*[:\-–]\s*(.*)$")


@dataclass
class FieldReading:
    id: str
    label: str
    value: str | None
    state: str              # filled | blank | illegible | absent
    required: bool

    @property
    def satisfied(self) -> bool:
        return self.state == "filled"


@dataclass
class Reading:
    template: FormTemplate | None
    fields: list[FieldReading] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    old_template: bool = False
    stated_version: str | None = None

    @property
    def recognised(self) -> bool:
        return self.template is not None

    @property
    def restricted(self) -> bool:
        return bool(self.template and self.template.restricted)

    @property
    def missing(self) -> list[FieldReading]:
        return [f for f in self.fields if f.required and f.state in {"blank", "absent"}]

    @property
    def illegible(self) -> list[FieldReading]:
        return [f for f in self.fields if f.state == "illegible"]

    @property
    def complete(self) -> bool:
        """A restricted form is never complete, because it is never read."""
        if not self.recognised or self.restricted:
            return False
        return not self.missing and not self.illegible

    def as_dict(self) -> dict[str, Any]:
        return {
            "template": self.template.id if self.template else None,
            "label": self.template.label if self.template else None,
            "restricted": self.restricted,
            "complete": self.complete,
            "old_template": self.old_template,
            "stated_version": self.stated_version,
            "fields": [
                {"id": f.id, "label": f.label, "state": f.state, "value": f.value if f.satisfied else None}
                for f in self.fields
            ],
            "missing": [f.label for f in self.missing],
            "illegible": [f.label for f in self.illegible],
            "unexpected": self.unexpected,
        }


def detect(text: str, cfg: ResolvedConfig) -> FormTemplate | None:
    """Which template this is. Longest phrase wins, so a specific form beats a
    general one that happens to share a word."""
    lowered = (text or "").lower()
    best: tuple[int, FormTemplate] | None = None
    for template in cfg.forms:
        for phrase in template.detect:
            if phrase.lower() in lowered and (best is None or len(phrase) > best[0]):
                best = (len(phrase), template)
    return best[1] if best else None


def _classify_value(raw: str) -> tuple[str, str | None]:
    value = (raw or "").strip().strip(".,;")
    lowered = value.lower()
    if lowered in ILLEGIBLE:
        return "illegible", None
    if lowered in BLANKS:
        return "blank", None
    return "filled", value


def _label_lookup(template: FormTemplate) -> dict[str, str]:
    """Every label and alias that maps to a field id, lowercased."""
    out: dict[str, str] = {}
    for spec in template.fields:
        out[spec.label.lower()] = spec.id
        for alias in spec.aliases:
            out[alias.lower()] = spec.id
    return out


def read(text: str, cfg: ResolvedConfig, *, template: FormTemplate | None = None) -> Reading:
    """Read a form against its template. Never reads a restricted one."""
    template = template or detect(text, cfg)
    if template is None:
        return Reading(template=None)

    stated = None
    if match := re.search(r"\bversion\s*[:\s]\s*([A-Za-z0-9.\-]+)", text or "", re.I):
        stated = match.group(1)

    reading = Reading(
        template=template,
        stated_version=stated,
        old_template=bool(stated and stated != template.version),
    )

    if template.restricted:
        # Stop here. Nothing is extracted and no field is reported, because
        # reporting which fields are filled is itself reading the document.
        return reading

    lookup = _label_lookup(template)
    found: dict[str, tuple[str, str | None]] = {}
    for line in (text or "").splitlines():
        if FURNITURE.match(line.strip()):
            continue
        match = LINE.match(line)
        if not match:
            continue
        label, raw = match.group(1).strip(), match.group(2)
        field_id = lookup.get(label.lower())
        if field_id is None:
            # Furniture can carry a value too -- "Signed: yes", "Page: 2 of 2" --
            # so the LABEL is checked as well as the whole line.
            if label.lower() not in {"version"} and not FURNITURE.match(label):
                reading.unexpected.append(label)
            continue
        found[field_id] = _classify_value(raw)

    for spec in template.fields:
        state, value = found.get(spec.id, ("absent", None))
        reading.fields.append(FieldReading(spec.id, spec.label, value, state, spec.required))
    return reading
