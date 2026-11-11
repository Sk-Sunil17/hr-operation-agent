"""Working out what a request is, and how sensitive it is.

Rules and lexicons first; a model only ever fills a gap the taxonomy could not.
Two invariants hold whatever the provider is:

  * The model may RAISE sensitivity and may never lower it.
  * The model is never asked what the agent is allowed to do. That is the rule
    engine's job, and it reads facts, not prose.

Everything here is deliberately boring. A classifier that can be read is a
classifier that can be argued with, which matters when the thing it classifies
is somebody's grievance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .packs.models import ResolvedConfig

# ---------------------------------------------------------------- lexicons
#
# Restricted wins over everything. These are the matters a person must own: the
# agent acknowledges and routes, and never drafts an answer.
RESTRICTED = (
    r"grievance|complain(?:t|ing)?|bull(?:y|ied|ying)|harass|discriminat|racist|racial|sexist|sexual|"
    r"victimis|retaliat|whistleblow|misconduct|disciplinar|"
    r"not been paid|haven't been paid|have not been paid|underpaid|paid incorrectly|not paid correctly|wrong pay|"
    r"injur(?:y|ed)|accident at work|workers comp|compensation claim|"
    r"mental health|suicid|self.harm|depress|anxiet|counsel?ling|"
    r"terminat|made redundant|redundanc|sack(?:ed|ing)?|fired|dismiss|"
    r"comments about my|said about my|remarks about my"
)

# Personal: the answer depends on this individual's circumstances, so a person
# confirms it. First-person phrasing alone is NOT enough -- "how many days do I
# get" is a routine policy question that happens to use the word "I".
#
# Two strengths, because "am I entitled" is ambiguous and the difference matters:
#
#   STATE        about this person's own position: balance, accrued, my contract,
#                in my case. Always personal.
#   ELIGIBILITY  "am I entitled / eligible / allowed". Personal UNLESS the
#                sentence is a quantity question, where it is asking for the
#                policy's number rather than about the person:
#
#                  "How many days am I entitled to?"            -> routine
#                  "Am I entitled to carry over my leave?"       -> personal
#
# Reading the weak form as personal in every case sends routine questions to a
# human for no reason, which is the deflection-chatbot failure in reverse.
PERSONAL_STATE = (
    r"my (?:entitlement|balance|remaining|accrued|notice period|probation|contract|agreement)|"
    r"do i (?:qualify|have enough|still have)|"
    r"how (?:much|many) (?:do|have) i (?:have|got|accrued|left|remaining)|"
    r"in my case|for me specifically|my situation|my circumstances|"
    r"can i carry (?:over|forward)|unused leave|carry over my|carry forward my|"
    r"pregnan|maternity|paternity|parental leave|adoption leave"
)

PERSONAL_ELIGIBILITY = r"am i (?:entitled|eligible|able|allowed)"

# A question about a rate or a quantity, which the policy answers for everyone.
QUANTITY_FRAME = r"^\s*(?:how (?:many|much|long)|what (?:is|are) the (?:rate|allowance|limit|entitlement))\b"

# Arranging, moving or calling off a time with someone. Split into a verb and a
# noun because the single pattern this replaces only matched "schedule a call"
# literally: "schedule a 30 minute screening call" fell through to `unknown` and
# was answered with a clarifying question, which is a poor showing for the one
# phrasing a recruiter is most likely to use.
INTERVIEW_VERB = (
    r"(?:schedul\w*|arrang\w*|set ?up|setting up|book\w*|organis\w*|organiz\w*|"
    r"find (?:a|some) time|line up|reschedul\w*|re-?arrang\w*|postpon\w*|cancel\w*|"
    r"mov\w*|push\w* back|invite\w*)"
)
INTERVIEW_NOUN = r"(?:interview|panel|screening|screen|slot|availability|call|meeting|chat|sitting)"

INTENT_PATTERNS: list[tuple[str, str]] = [
    # Order matters: a publish request is not an enquiry about policy.
    ("policy", r"\b(?:publish|retire|withdraw|supersede|approve|roll ?out)\b[^.?!]{0,40}\b(?:polic|version)"),
    ("policy", r"\bpolic(?:y|ies)\b[^.?!]{0,30}\b(?:publish|retire|withdraw|live|in force)\b"),
    # A document arriving outranks what the document is ABOUT. "I have attached
    # my completed new starter form" is an intake, not an onboarding question --
    # and reading it the other way files nothing.
    ("document", r"\b(?:attach(?:ed|ing)?|upload(?:ed)?|here is my|enclos(?:ed|ing)|completed form|signed (?:form|contract)|certificate)\b"),
    ("status", r"\b(?:where (?:did|has|is)|what(?:'s| is) happening (?:to|with)|any update|progress (?:of|on)|chase|got to)\b"),
    ("offboarding", r"\b(?:leaving|resign|resignation|last day|exit|offboard|notice period ends|final day)\b"),
    ("onboarding", r"\b(?:new (?:starter|joiner|hire)|starts? (?:on|in|next)|first day|onboard|ready to (?:go|start)|day one)\b"),
    # The word itself, which is not ambiguous. "exit interview" is caught by the
    # offboarding pattern above, which is why that one comes first.
    ("interview", r"\binterview\b"),
    ("interview", rf"\b{INTERVIEW_VERB}\b[^.?!]{{0,40}}\b{INTERVIEW_NOUN}\b"),
    ("interview", rf"\b{INTERVIEW_NOUN}\b[^.?!]{{0,30}}\b(?:with|for)\b[^.?!]{{0,30}}\bcandidate\b"),
    ("interview", r"\bavailability for\b"),
    ("admin", r"\b(?:update|change|correct|amend|fix|revise|set)\b[^.?!]{0,40}\b(?:address|phone|mobile|email|bank|name|details|contact|number|title|record|records|profile|file)\b"),
    ("admin", r"\b(?:i have |i've )?moved\b|\bnew (?:address|phone|bank)\b"),
    ("enquiry", r"\b(?:how (?:much|many|do|long)|what (?:is|are|happens|do i)|when (?:do|does|can)|can i|am i|do i|who|where|why|rules on|entitled to|eligible for)\b"),
]

# A question frame carries no meaning for retrieval and should not be a clue here either.
VAGUE = r"^(?:hi|hello|hey|help|question|a question|quick question|can you help|are you there)[\s.!?]*$"

STATUS_HINT = r"\b(?:request|change|form|ticket|case|application)\b"

# How people actually state a new value. Without this the agent identified the
# field and the system correctly and then wrote "(as described in the request)",
# leaving the approver to read the original message and retype it -- which
# removed most of the saving the feature exists for.
# A value runs to the end of the sentence. `[^.]` is too crude: it truncates
# `mo@example.test` to `mo@example` and `Apt 2.1` to `Apt 2`. A full stop only
# ends the value when it is followed by whitespace or the end of the message.
# `?` and `!` end it outright -- no address or number contains one, and without
# them "change Sam's number to 07700 900481? He reports to me" captured the
# following sentence as part of the value and offered it for a record.
# Braces are doubled: these frames go through .format(phrase=...), which would
# otherwise read {2,80} as a placeholder.
_VALUE = r"(?P<value>(?:[^.?!\n]|\.(?!\s|$)){{2,80}})"

# Frames come in two strengths.
#
#   STRICT  an explicit change verb, or a colon. Whatever follows is the value.
#   LOOSE   "my X is Y". This also matches "my phone is broken", which is a
#           complaint, not a new number -- so a loose match must look like data
#           before it is believed.
# The words that may sit between the verb and the field name. A fixed list of
# determiners was too narrow for how people actually write: it matched
# "change my phone number to X" but not "change Sam's phone number to X" or
# "change my registered home address to X", so both identified the field
# correctly and then reported that no value had been given. Up to three short
# words, matched lazily, and the frame is still anchored on the field name
# followed by "to" -- which is what keeps it from running away.
_FILLER = r"(?:[\w''\-]+\s+){{0,3}}?"

STRICT_FRAMES = [
    r"(?:change|update|correct|amend|set|fix|make)\s+" + _FILLER + r"{phrase}\s+(?:to|as)\s+" + _VALUE,
    r"(?:my|his|her|their|the)\s+new\s+{phrase}\s+(?:is|will be|should be)\s*[:=]?\s*" + _VALUE,
    r"{phrase}\s*[:=]\s*" + _VALUE,
]

LOOSE_FRAMES = [
    r"(?:my|his|her|their)\s+{phrase}\s+(?:is now|is|should now be)\s*" + _VALUE,
]

# Words that describe a problem with a field rather than a new value for it.
NOT_A_VALUE = {
    "broken", "lost", "stolen", "missing", "wrong", "incorrect", "out of date",
    "outdated", "not working", "disconnected", "dead", "old", "the same",
    "unchanged", "correct", "up to date", "fine", "ok", "okay", "unavailable",
}


def _looks_like_data(value: str) -> bool:
    """A loose match is only believed when it looks like a value.

    Digits, an @, or several words. "broken" is none of those.
    """
    return bool(re.search(r"[\d@]", value)) or len(value.split()) >= 2


def _describes_a_problem(value: str) -> bool:
    """Match the LEADING phrase, not the whole string.

    "broken, I cannot be reached today" is several words and so passes the
    looks-like-data test, but it is still a complaint. What the value starts
    with is what it is about.
    """
    lead = re.split(r"[,;]", value, maxsplit=1)[0].strip().lower()
    first = lead.split()[0] if lead.split() else ""
    return lead in NOT_A_VALUE or first in NOT_A_VALUE


# Words that open a trailing remark rather than continuing a value.
#
# A comma cannot simply end a value: "12 Oakfield Road, Bristol BS6 7JQ" is one
# address. But "07700 900481, he gave us a new one" is a number followed by an
# explanation, and writing the explanation into the record is worse than asking.
# No part of an address or a number begins with any of these.
TRAILING_CLAUSE = (
    "i", "we", "he", "she", "they", "you", "it", "his", "her", "their", "my", "our",
    "and", "also", "as", "because", "so", "since", "but", "which", "who", "that",
    "thanks", "thank", "please", "could", "can", "let", "if", "when", "now",
)


def _trim_trailing_clause(value: str) -> str:
    """Drop a comma-separated tail that is a remark rather than part of the value."""
    parts = re.split(r"\s*[,;]\s*", value)
    kept = [parts[0]]
    for part in parts[1:]:
        first = part.split()[0].lower().strip(".:") if part.split() else ""
        if first in TRAILING_CLAUSE:
            break
        kept.append(part)
    return ", ".join(kept).strip()


def _stated_value(text: str, phrases: list[str]) -> str | None:
    """The value a person gave for a field, if they gave one.

    Returns None rather than a guess. An approver told "the new value was not
    stated" can ask; an approver shown a placeholder that looks like data may
    not notice.
    """
    for phrase in sorted(phrases, key=len, reverse=True):
        for frame in STRICT_FRAMES:
            match = re.search(frame.format(phrase=re.escape(phrase)), text, re.I)
            if match and (value := _trim_trailing_clause(match.group("value").strip(" ,;:"))):
                if not _describes_a_problem(value):
                    return value
        for frame in LOOSE_FRAMES:
            match = re.search(frame.format(phrase=re.escape(phrase)), text, re.I)
            if match and (value := _trim_trailing_clause(match.group("value").strip(" ,;:"))):
                if not _describes_a_problem(value) and _looks_like_data(value):
                    return value
    return None


# A compiled name index per resolved configuration.
#
# `_entities` used to loop the whole directory for every message, running two
# freshly escaped regexes per person. At 14 people that was 0.4 ms; at the 2,500
# the Meridian pack describes it was 131 ms -- the shipped reference tenant was
# the one that broke. One compiled alternation, built once, is flat.
_NAME_INDEX: dict[str, tuple[re.Pattern[str] | None, dict[str, str]]] = {}


def _name_index(cfg: ResolvedConfig, requester_id: str | None) -> tuple[re.Pattern[str] | None, dict[str, str]]:
    key = cfg.config_hash
    if key not in _NAME_INDEX:
        lookup: dict[str, str] = {}
        for person in cfg.people:
            lookup.setdefault(person.name.lower(), person.id)
            lookup.setdefault(person.name.split()[0].lower(), person.id)
        if lookup:
            # Longest first, so "Chan Ka Wai" wins over "Chan".
            alternation = "|".join(re.escape(n) for n in sorted(lookup, key=len, reverse=True))
            pattern = re.compile(rf"\b({alternation})(?:'s)?\b", re.I)
        else:
            pattern = None
        _NAME_INDEX[key] = (pattern, lookup)
    return _NAME_INDEX[key]


def forget_name_index() -> None:
    """Test hook: drop the cache so an edited directory is seen."""
    _NAME_INDEX.clear()


@dataclass
class Classification:
    """What the request is. `confidence` below the pack's floor sets `confidence_low`."""

    intent: str
    sensitivity: str
    confidence: float
    entities: dict[str, Any] = field(default_factory=dict)
    compound: bool = False
    clauses: list[str] = field(default_factory=list)
    source: str = "taxonomy"   # taxonomy | model
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "sensitivity": self.sensitivity,
            "confidence": round(self.confidence, 2),
            "entities": self.entities,
            "compound": self.compound,
            "source": self.source,
            "notes": self.notes,
        }


SENSITIVITY_ORDER = ("routine", "personal", "restricted")


def _split_clauses(text: str) -> list[str]:
    """Split on sentence ends and on 'and also' style joins."""
    parts = re.split(r"(?<=[.!?])\s+|\s+and also\s+|\s*;\s*|\s+also,?\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _interview_stage_named(lowered: str, cfg: ResolvedConfig) -> bool:
    """Whether this company's own name for an interview stage appears, with a verb.

    Every company names its hiring stages differently, and those names are
    already in the pack. Reading them from there costs nothing and means a
    company whose vocabulary this file has never heard of does not need a code
    change to be understood -- which is the same reason no country, role name or
    policy title is hardcoded anywhere else either.

    Checked only after every explicit pattern above has failed, so a stage label
    that happens to be an ordinary word cannot capture a message that is plainly
    about something else.
    """
    phrases = [
        phrase.lower()
        for stage in cfg.interviews.stages
        for phrase in [*stage.detect, stage.label]
        if phrase
    ]
    if not phrases or not re.search(INTERVIEW_VERB, lowered):
        return False
    alternation = "|".join(re.escape(p) for p in sorted(set(phrases), key=len, reverse=True))
    return bool(re.search(rf"\b(?:{alternation})\b", lowered))


def _intent_of(text: str, cfg: ResolvedConfig) -> tuple[str, float]:
    lowered = text.lower()
    for intent, pattern in INTENT_PATTERNS:
        if re.search(pattern, lowered):
            return intent, 0.8
    if _interview_stage_named(lowered, cfg):
        return "interview", 0.8
    if "?" in text:
        return "enquiry", 0.5
    return "unknown", 0.3


def _sensitivity_of(text: str) -> tuple[str, list[str]]:
    lowered = text.lower()
    if match := re.search(RESTRICTED, lowered):
        return "restricted", [f"matched a restricted topic: {match.group(0)!r}"]
    if match := re.search(PERSONAL_STATE, lowered):
        return "personal", [f"asks about this person's own position: {match.group(0)!r}"]
    if match := re.search(PERSONAL_ELIGIBILITY, lowered):
        if re.search(QUANTITY_FRAME, lowered):
            return "routine", [
                f"{match.group(0)!r} appears, but the question asks for the policy's figure, "
                "not about this person"
            ]
        return "personal", [f"asks whether this person qualifies: {match.group(0)!r}"]
    return "routine", []


def _entities(text: str, cfg: ResolvedConfig, requester_id: str | None) -> dict[str, Any]:
    """Who and what the message names. Facts are confirmed against systems later;
    this only says what the text appears to refer to."""
    found: dict[str, Any] = {}

    # A person from this company's directory, named in the message. One pass
    # over the text against one compiled pattern, whatever the directory size.
    pattern, lookup = _name_index(cfg, requester_id)
    if pattern is not None:
        named: list[str] = []
        for match in pattern.finditer(text):
            person_id = lookup.get(match.group(1).lower())
            if person_id and person_id != requester_id and person_id not in named:
                named.append(person_id)
        if named:
            found["people"] = named

    # Which record field a change would touch, using the pack's own hints.
    hints = cfg.taxonomy.field_hints
    fields = sorted({
        canonical
        for phrase, canonical in hints.items()
        if re.search(rf"\b{re.escape(phrase)}\b", text, re.I)
    })
    # A field the company does not map is still worth naming, so A2 can refuse clearly.
    if not fields:
        if unmapped := re.findall(r"\b(?:job title|salary|pay|grade|team|department|manager)\b", text, re.I):
            found["unmapped_fields"] = sorted({u.lower().replace(" ", "_") for u in unmapped})
    if fields:
        found["fields"] = fields
        # For each field, the value the person actually stated, if any.
        values: dict[str, str] = {}
        for canonical in fields:
            phrases = [phrase for phrase, target in hints.items() if target == canonical]
            if value := _stated_value(text, phrases):
                values[canonical] = value
        if values:
            found["values"] = values

    if versions := re.findall(r"\bversion\s+(\d+(?:\.\d+)*)\b", text, re.I):
        found["versions"] = versions
    if policies := re.findall(r"\b(leave|conduct|remote|flexible|grievance|onboarding|parental)\b", text, re.I):
        found["topics"] = sorted({p.lower() for p in policies})
    return found


def classify(
    text: str,
    cfg: ResolvedConfig,
    *,
    requester_id: str | None = None,
    channel: str = "web",
    has_attachment: bool = False,
) -> Classification:
    """Classify one inbound message. No model involved."""
    clauses = _split_clauses(text)

    # Sensitivity is taken over the WHOLE message: a restricted sentence buried in
    # a longer message must still make the whole thing restricted.
    sensitivity, notes = _sensitivity_of(text)

    per_clause = [_intent_of(c, cfg) for c in clauses] or [("unknown", 0.3)]
    intents = [i for i, _ in per_clause]
    intent, confidence = max(per_clause, key=lambda pair: pair[1])

    # Compound only when the clauses genuinely ask for different things. One
    # request spread over two sentences is not two requests.
    distinct = {i for i in intents if i != "unknown"}
    compound = len(clauses) > 1 and len(distinct) > 1

    if re.match(VAGUE, text.strip(), re.I):
        intent, confidence = "unknown", 0.2
        notes.append("too short to act on; a person sees it with one clarifying question")

    # A document that actually arrived decides this, whatever the covering note
    # says. The wording is a reading of the text; the attachment is a fact about
    # the request, and facts outrank wording everywhere else in this system too.
    #
    # Without this, "Can you file this for me please." with a real document
    # attached was classified as unclear and answered with a clarifying question,
    # while the document itself went unread. Sensitivity is unaffected, so a
    # restricted matter with something attached is still decided by R1 first.
    if has_attachment and intent != "document":
        notes.append(
            f"a document arrived, so this is an intake rather than {intent}"
            if intent != "unknown"
            else "a document arrived, so this is an intake"
        )
        intent, confidence = "document", 0.8
        compound = False

    entities = _entities(text, cfg, requester_id)

    # A status question is a status question whatever it is about.
    if intent in {"enquiry", "unknown"} and re.search(STATUS_HINT, text, re.I) and re.search(
        r"\b(?:where|update|progress|happening|got to|chase)\b", text, re.I
    ):
        intent, confidence = "status", 0.8

    if compound:
        notes.append(f"{len(distinct)} separate requests in one message: {', '.join(sorted(distinct))}")

    return Classification(
        intent=intent,
        sensitivity=sensitivity,
        confidence=confidence,
        entities=entities,
        compound=compound,
        clauses=clauses,
        notes=notes,
    )


def apply_model_opinion(base: Classification, opinion: dict[str, Any]) -> Classification:
    """Fold a model's opinion into a taxonomy classification, under hard limits.

    The model may raise sensitivity, may lower confidence, and may fill an intent
    the taxonomy could not place. It may never lower sensitivity, never raise
    confidence, and has no say in what the agent is allowed to do.
    """
    out = base
    notes = list(base.notes)

    if (proposed := opinion.get("sensitivity")) in SENSITIVITY_ORDER:
        if SENSITIVITY_ORDER.index(proposed) > SENSITIVITY_ORDER.index(base.sensitivity):
            out = Classification(**{**out.__dict__, "sensitivity": proposed, "source": "model"})
            notes.append(f"a model raised sensitivity from {base.sensitivity} to {proposed}")
        elif proposed != base.sensitivity:
            notes.append(f"a model proposed {proposed}, which would lower sensitivity, so it was ignored")

    if (proposed := opinion.get("intent")) and base.intent == "unknown" and proposed in {"enquiry", "admin", "document", "onboarding", "offboarding", "interview", "policy", "status"}:
        out = Classification(**{**out.__dict__, "intent": proposed, "source": "model"})
        notes.append(f"a model placed the intent as {proposed}; the taxonomy could not")

    if isinstance(proposed := opinion.get("confidence"), (int, float)) and proposed < out.confidence:
        out = Classification(**{**out.__dict__, "confidence": float(proposed)})

    return Classification(**{**out.__dict__, "notes": notes})
