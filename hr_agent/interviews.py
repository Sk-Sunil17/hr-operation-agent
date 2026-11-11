"""Interview scheduling: reading the request, finding a time, proposing it.

Scheduling is the one HR task where the agent's output lands in other people's
calendars and in a stranger's inbox. That shapes every decision in this module:

  * **The solver lives here, not in the adapter.** A calendar system is asked one
    question -- when is this person busy -- and answers it. Which of the free
    times is *acceptable* depends on the company's notice period, its buffer, its
    daily cap and its working days, and those answers must be identical whether
    the calendar behind it is Google, Outlook or the mock. An adapter that
    decided this would mean a company's own rules changed when it changed
    calendar vendor.

  * **Nothing is ever guessed.** If the message does not say who the candidate
    is, the reply says so and no message is addressed outside the company. If a
    reschedule could refer to two interviews, it names both and stops. A
    confident wrong guess here books a stranger into the wrong hour of someone
    else's day.

  * **Panel members come from the directory. Only the candidate comes from the
    text.** This is the security boundary. A message can contain any address at
    all, so the one address taken from a message is the candidate's -- a person
    who by definition is not in the directory -- and it is shown in full in the
    preview an approver reads before anything is sent. Internal attendees are
    resolved to directory entries and can never be an arbitrary address a
    message supplied.

Dates, not clocks: the kernel runs on a date (`on`), which is what makes a run
reproducible and lets a company ask "what will this say next Tuesday". The
notice window is therefore counted from the start of the working day on that
date rather than from the wall clock, and that is a deliberate, documented
approximation rather than an oversight.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from . import db
from .packs.models import InterviewStage, Person, ResolvedConfig

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

MONTHS = {
    name: number
    for number, names in enumerate(
        [
            ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
            ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
            ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"), ("dec", "december"),
        ],
        start=1,
    )
    for name in names
}

# What a request can be, beyond "book something new".
RESCHEDULE = r"\b(?:reschedul|re-?arrang|postpon|push(?:ed)? back|mov(?:e|ing) (?:the|his|her|their)\b)"
CANCEL = r"\b(?:cancel|call(?:ing)? off|withdraw|scrap)\b"

# A remote interview. Worth detecting because "a video link" and "meeting room 2"
# are different enough that telling a candidate the wrong one wastes their day.
REMOTE = r"\b(?:video|remote(?:ly)?|online|zoom|teams|meet|google meet|dial.?in|phone|call)\b"

EMAIL = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"

# "the Head of Finance role", "for the Senior Nurse position".
ROLE_TITLE = (
    r"\bfor\s+(?:the\s+)?(?P<title>[A-Z][\w&/\-]*(?:\s+(?:of|for|and|the|de|du)?\s*[A-Z\w&/\-]+){0,5}?)"
    r"\s+(?:role|position|vacancy|job|post|opening)\b"
)

# A capitalised personal name. Deliberately narrow: two or three capitalised
# words, which is a name far more often than it is anything else.
NAME = r"(?P<name>[A-Z][a-z'\-]+(?:\s+[A-Z][a-z'\-]+){1,2})"

# The lead-in is case-insensitive and the name is not: a message opening with
# "Interview Dana Olofsson" is ordinary, and matching the keyword case-sensitively
# simply missed it. The `(?i:...)` scope keeps the capitalisation requirement on
# NAME itself, which is the only thing distinguishing a name from a noun.
CANDIDATE_FRAMES = [
    rf"\b(?i:candidate)\s+(?i:is\s+|called\s+|named\s+)?{NAME}",
    rf"\b(?i:interview(?:ing)?)\s+{NAME}",
    rf"\b(?i:with)\s+{NAME}\s*[\(<]",          # "with Dana Olofsson (dana@…)"
    rf"\b(?i:for)\s+{NAME}\s*[\(<,]",
    rf"\b(?i:with)\s+{NAME}\b",
]

# How far after a name an address may sit and still be taken as that person's.
# "Dana Olofsson (dana@example.test)" is theirs; an address in the next sentence
# is not demonstrably anybody's.
ADDRESS_NEAR_NAME = 60

DURATION = r"\b(?P<count>\d{1,3})\s*(?P<unit>minutes?|mins?|hours?|hrs?|h)\b"
AT_TIME = r"\bat\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>am|pm)?\b"
ISO_DATE = r"\b(?P<year>20\d{2})-(?P<month>\d{2})-(?P<day>\d{2})\b"
DAY_MONTH = r"\b(?:on\s+)?(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+(?P<month>[A-Za-z]{3,9})\b"
MONTH_DAY = r"\b(?P<month>[A-Za-z]{3,9})\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?\b"


# ------------------------------------------------------------------ time helpers


def to_minutes(hhmm: str) -> int:
    hours, _, minutes = hhmm.partition(":")
    return int(hours) * 60 + int(minutes or 0)


def to_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def working_days(cfg: ResolvedConfig) -> set[str]:
    return {d.lower() for d in cfg.locale.working_hours.days}


def is_working_day(cfg: ResolvedConfig, day: date) -> bool:
    return WEEKDAYS[day.weekday()] in working_days(cfg)


def within_label(cfg: ResolvedConfig) -> str:
    hours = cfg.locale.working_hours
    return f"{hours.start}-{hours.end} {cfg.locale.timezone}"


MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def written_date(cfg: ResolvedConfig, day: date) -> str:
    """A date in the format this company declared, for a person to read.

    The locale supplies `date_format` and every other part of this system
    honours it, so an interview invitation should too: a candidate reading
    "2026-11-12" has to decode it, and one reading "12 Nov 2026" does not.

    Only the tokens the pack format actually uses are supported, and anything
    unrecognised falls back to the ISO form -- which is unambiguous everywhere,
    and therefore the right thing to be wrong with.
    """
    tokens = {
        "EEEE": WEEKDAY_NAMES[day.weekday()],
        "MMMM": MONTH_NAMES[day.month - 1],
        "MMM": MONTH_NAMES[day.month - 1][:3],
        "yyyy": f"{day.year:04d}",
        "dd": f"{day.day:02d}",
        "MM": f"{day.month:02d}",
        "d": str(day.day),
    }
    pattern = cfg.locale.date_format or ""
    if not re.search(r"[dMy]", pattern):
        return day.isoformat()
    out, index = [], 0
    while index < len(pattern):
        for token, value in tokens.items():          # longest tokens first
            if pattern.startswith(token, index):
                out.append(value)
                index += len(token)
                break
        else:
            out.append(pattern[index])
            index += 1
    return "".join(out)


def overlaps(start: int, end: int, busy_from: int, busy_to: int, *, buffer_minutes: int) -> bool:
    """Whether a slot collides with a busy window, once the buffer is applied.

    The buffer is dead time either side of the BUSY window, so an interview is
    not booked to start the second someone's previous meeting ends.
    """
    return start < busy_to + buffer_minutes and end + buffer_minutes > busy_from


# ------------------------------------------------------------------ the request


@dataclass
class Request:
    """What the message asked for, as far as it can be read without guessing."""

    action: str = "schedule"                     # schedule | reschedule | cancel
    stage: InterviewStage | None = None
    stage_label: str = "Interview"
    minutes: int = 60
    candidate_name: str | None = None
    candidate_email: str | None = None
    role_title: str | None = None
    panel_ids: list[str] = field(default_factory=list)
    organiser_id: str | None = None
    on: date | None = None                       # a day the message actually named
    at: str | None = None                        # a time the message actually named
    remote: bool = False
    location: str | None = None
    # Where the candidate's address came from. A reschedule inherits it from the
    # interview being moved, and a reply that says "the address the message gave"
    # when the message gave none is a small lie in a preview somebody is about to
    # approve.
    email_source: str | None = None               # message | record
    # What could not be read, in words a person can act on. Split by origin so
    # the list cannot go stale: a reschedule learns the candidate's address after
    # the message has been parsed, and a gap saying otherwise would survive into
    # the reply alongside the action that contradicts it.
    panel_gaps: list[str] = field(default_factory=list)
    extra_gaps: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def candidate_known(self) -> bool:
        return bool(self.candidate_name or self.candidate_email)

    @property
    def reachable(self) -> bool:
        """Whether there is an address to tell the candidate anything at."""
        return bool(self.candidate_email)

    @property
    def gaps(self) -> list[str]:
        """Everything unresolved, recomputed from what is currently known."""
        candidate: list[str] = []
        if not self.candidate_known:
            candidate.append(
                "the candidate was not named, so nothing is addressed to anyone outside the company"
            )
        elif not self.candidate_email:
            who = self.candidate_name or "the candidate"
            candidate.append(
                f"there is no email address for {who}, so they cannot be told about it from here"
            )
        return [*self.panel_gaps, *candidate, *self.extra_gaps]

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "stage": self.stage.id if self.stage else None,
            "stage_label": self.stage_label,
            "minutes": self.minutes,
            "candidate_name": self.candidate_name,
            "candidate_email": self.candidate_email,
            "role_title": self.role_title,
            "panel_ids": self.panel_ids,
            "on": self.on.isoformat() if self.on else None,
            "at": self.at,
            "remote": self.remote,
            "location": self.location,
            "email_source": self.email_source,
            "gaps": self.gaps,
            "notes": self.notes,
        }


def _stage_from(text: str, cfg: ResolvedConfig) -> InterviewStage | None:
    """Which kind of interview, using this company's own words for them.

    Longest phrase first, so "phone screen" is not read as "screen" when a
    company happens to define both.
    """
    lowered = text.lower()
    candidates: list[tuple[int, InterviewStage]] = []
    for stage in cfg.interviews.stages:
        for phrase in [*stage.detect, stage.label, stage.id]:
            if phrase and re.search(rf"\b{re.escape(phrase.lower())}\b", lowered):
                candidates.append((len(phrase), stage))
    if not candidates:
        return None
    return max(candidates, key=lambda pair: pair[0])[1]


def _duration_from(text: str) -> int | None:
    match = re.search(DURATION, text, re.I)
    if not match:
        return None
    count = int(match.group("count"))
    unit = match.group("unit").lower()
    minutes = count * 60 if unit.startswith(("hour", "hr", "h")) else count
    # A sanity floor and ceiling. "a 2 minute interview" is a typo, and so is
    # "a 9 hour interview"; believing either produces a nonsense proposal.
    return minutes if 10 <= minutes <= 480 else None


def _date_from(text: str, *, today: date, cfg: ResolvedConfig) -> tuple[date | None, str | None]:
    """A day the message named, and how it named it.

    Returns (None, None) rather than a guess. "soon" is not a date, and treating
    it as one is how an interview gets proposed for a day nobody asked about.
    """
    if match := re.search(ISO_DATE, text):
        try:
            return date(int(match.group("year")), int(match.group("month")), int(match.group("day"))), "a date"
        except ValueError:
            return None, None

    lowered = text.lower()
    if re.search(r"\btomorrow\b", lowered):
        return today + timedelta(days=1), "tomorrow"
    if re.search(r"\btoday\b", lowered):
        return today, "today"

    for pattern in (DAY_MONTH, MONTH_DAY):
        if match := re.search(pattern, text, re.I):
            month = MONTHS.get(match.group("month").lower())
            if not month:
                continue
            day_number = int(match.group("day"))
            if not 1 <= day_number <= 31:
                continue
            for year in (today.year, today.year + 1):
                try:
                    found = date(year, month, day_number)
                except ValueError:
                    break
                if found >= today:
                    return found, "a date"

    # A weekday name. "next Tuesday" means the Tuesday of next week; a bare
    # "Tuesday" means the next one to come, which is what people mean by it.
    if match := re.search(rf"\b(?P<next>next\s+)?(?P<day>{'|'.join(WEEKDAYS)})\b", lowered):
        target = WEEKDAYS.index(match.group("day"))
        ahead = (target - today.weekday()) % 7 or 7
        if match.group("next"):
            ahead += 7 if ahead <= (6 - today.weekday()) else 0
        return today + timedelta(days=ahead), "a weekday"

    if re.search(r"\bnext week\b", lowered):
        ahead = 7 - today.weekday()
        day = today + timedelta(days=ahead)
        for _ in range(7):
            if is_working_day(cfg, day):
                return day, "next week"
            day += timedelta(days=1)
    return None, None


def _time_from(text: str, cfg: ResolvedConfig) -> str | None:
    match = re.search(AT_TIME, text, re.I)
    if not match:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    meridiem = (match.group("meridiem") or "").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if not meridiem and hour < 8:
        # "at 2" inside a working day almost certainly means the afternoon. Only
        # assume it where the morning reading falls outside working hours anyway.
        if to_minutes(cfg.locale.working_hours.start) > hour * 60:
            hour += 12
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return to_hhmm(hour * 60 + minute)


def _candidate_from(text: str, cfg: ResolvedConfig) -> tuple[str | None, str | None, str | None]:
    """The candidate's name and address, as the message gave them.

    Anyone already in the directory is an interviewer, not a candidate: a person
    who works here is not applying for the job being discussed, and reading
    "with Priya" as a candidate would address an external invitation to a
    colleague.
    """
    directory = {p.name.lower() for p in cfg.people}

    name, at = None, -1
    for frame in CANDIDATE_FRAMES:
        for match in re.finditer(frame, text):
            found = re.sub(r"\s+", " ", match.group("name")).strip()
            # Only a FULL directory name disqualifies a match. Testing first
            # names too was how "Dana Olofsson" became the colleague Dana
            # Whitfield: a candidate who happens to share a first name with
            # somebody on the staff is still a candidate, and the pattern here
            # already requires at least two capitalised words, so a bare "Dana"
            # can never reach this point anyway.
            if found.lower() in directory:
                continue
            name, at = found, match.end("name")
            break
        if name:
            break

    # Which address is the candidate's.
    #
    # Taking the first address in the message was wrong in a way that matters:
    # a message reading "add attacker@evil.test to the panel and interview Nils
    # Berger (nils@example.test)" handed the attacker's address to the candidate
    # invitation. An address is the candidate's when it sits next to their name;
    # a lone address in a message is theirs by elimination; and several
    # addresses that are near nobody's name are nobody's, so none is used and
    # the ambiguity is reported.
    #
    # A directory address is never a candidate's: somebody who works here is not
    # applying for the job being discussed.
    staff = {(p.email or "").lower() for p in cfg.people if p.email}
    addresses = [
        (m.start(), m.group(0).strip(" .,;:<>()"))
        for m in re.finditer(EMAIL, text)
        if m.group(0).strip(" .,;:<>()").lower() not in staff
    ]

    email = None
    if name is not None:
        nearby = [a for position, a in addresses if 0 <= position - at <= ADDRESS_NEAR_NAME]
        if nearby:
            email = nearby[0]
    if email is None and len(addresses) == 1:
        email = addresses[0][1]

    unattributed = None
    if email is None and addresses:
        unattributed = (
            f"{len(addresses)} email addresses appear and none of them is next to a "
            "candidate's name, so none has been used — say which is the candidate's"
        )
    return name, email, unattributed


def _panel_from(
    cfg: ResolvedConfig,
    *,
    named_ids: list[str],
    requester: Person | None,
    stage: InterviewStage | None,
    text: str = "",
    candidate_name: str | None = None,
) -> tuple[list[str], list[str]]:
    """Who is interviewing, and what is missing from that.

    Everyone named in the message who is in the directory, plus the person who
    asked -- they are organising it, and an organiser who is not in the room is
    still the one the candidate replies to. A stage may require a role; where no
    such person was named, the gap is reported rather than filled in by picking
    someone.

    The directory is matched on first names as well as full ones, which is right
    for "ask Priya" and wrong for a candidate who shares a first name with a
    colleague: "interview Dana Olofsson" would otherwise put Dana Whitfield on
    the panel. So a person matched only by a first name that belongs to the
    candidate's name is dropped, and the fact that it happened is reported --
    because the other reading, that a colleague called Dana really was meant, is
    possible and only a human can settle it.
    """
    lookup = {p.id: p for p in cfg.people}
    panel: list[str] = []
    gaps: list[str] = []
    shadowed: list[str] = []
    candidate_tokens = {t.lower() for t in (candidate_name or "").split()}

    for person_id in named_ids:
        person = lookup.get(person_id)
        if person is None:
            continue
        if candidate_tokens and person.name.lower() not in text.lower():
            first = person.name.split()[0].lower()
            if first in candidate_tokens:
                shadowed.append(person.name)
                continue
        panel.append(person_id)

    if requester and requester.id not in panel:
        panel.append(requester.id)
    if shadowed:
        gaps.append(
            f"{', '.join(shadowed)} was not added to the panel: only the first name appeared, "
            f"and it is also the candidate's. Say the full name if {'they were' if len(shadowed) > 1 else 'that person was'} meant"
        )

    if stage:
        held = {
            role
            for person_id in panel
            for person in cfg.people
            if person.id == person_id
            for role in person.roles
        }
        for role in stage.panel_roles:
            if role not in held:
                label = cfg.approvals.roles.get(role, role)
                gaps.append(
                    f"this company's {stage.label.lower()} requires a {label} on the panel, "
                    "and nobody with that role was named"
                )
        if len(panel) < stage.panel_min:
            gaps.append(
                f"a {stage.label.lower()} here is run by at least {stage.panel_min} interviewers, "
                f"and {len(panel)} {'was' if len(panel) == 1 else 'were'} named"
            )
    return panel, gaps


def parse(
    message: str,
    cfg: ResolvedConfig,
    *,
    requester: Person | None,
    named_ids: list[str] | None = None,
    today: date,
) -> Request:
    """Read a scheduling request. No model involved, and nothing invented."""
    text = message or ""
    stage = _stage_from(text, cfg)
    action = "schedule"
    if re.search(CANCEL, text, re.I):
        action = "cancel"
    elif re.search(RESCHEDULE, text, re.I):
        action = "reschedule"

    candidate_name, candidate_email, unattributed = _candidate_from(text, cfg)
    panel, gaps = _panel_from(
        cfg, named_ids=named_ids or [], requester=requester, stage=stage,
        text=text, candidate_name=candidate_name,
    )
    day, how = _date_from(text, today=today, cfg=cfg)
    at = _time_from(text, cfg)

    notes: list[str] = []
    if stage is None and cfg.interviews.stages:
        notes.append(
            "The message did not say which kind of interview this is, so the company's "
            f"default length of {cfg.interviews.default_duration_minutes} minutes was used."
        )
    if how:
        notes.append(f"The day was read from {how} in the message.")

    stated_minutes = _duration_from(text)
    minutes = stated_minutes or cfg.interviews.minutes_for(stage)
    if stated_minutes and stage and stated_minutes != stage.duration_minutes:
        notes.append(
            f"The message asked for {stated_minutes} minutes; a {stage.label.lower()} here is "
            f"normally {stage.duration_minutes}."
        )

    if unattributed:
        gaps.append(unattributed)

    remote = bool(re.search(REMOTE, text, re.I))

    return Request(
        action=action,
        stage=stage,
        stage_label=stage.label if stage else "Interview",
        minutes=minutes,
        candidate_name=candidate_name,
        candidate_email=candidate_email,
        role_title=(lambda m: re.sub(r"\s+", " ", m.group("title")).strip() if m else None)(
            re.search(ROLE_TITLE, text)
        ),
        panel_ids=panel,
        organiser_id=requester.id if requester else None,
        on=day,
        at=at,
        remote=remote,
        # Deliberately not "a link will be attached": whether one exists depends
        # on whether the connected calendar creates conferencing, which this
        # function cannot know. A calendar that does will overwrite this with the
        # real link when the invitation is committed.
        location=(
            "Online — the people team will confirm the joining details"
            if remote else cfg.interviews.default_location
        ),
        email_source="message" if candidate_email else None,
        panel_gaps=gaps,
        notes=notes,
    )


# ------------------------------------------------------------------ the solver


@dataclass
class Slot:
    on: date
    start: str
    end: str

    @property
    def label(self) -> str:
        return f"{self.on.isoformat()} {self.start}-{self.end}"

    def as_dict(self, cfg: ResolvedConfig | None = None) -> dict[str, Any]:
        """`date` stays machine-readable; `label` is the company's own format.

        Both, because a screen that mixes ISO dates with written ones in the
        same panel looks like two features bolted together.
        """
        return {
            "date": self.on.isoformat(), "start": self.start, "end": self.end,
            "label": written_date(cfg, self.on) if cfg else self.on.isoformat(),
        }


@dataclass
class Rejected:
    """A day or a time that was ruled out, and the reason a person can check.

    `when` stays machine-readable for the audit row and the API; `label` is the
    same moment in the company's own date format, for the reply a person reads.
    Keeping both means the trail is parseable and the prose is not half ISO.
    """

    when: str
    why: str
    label: str = ""

    def __post_init__(self) -> None:
        self.label = self.label or self.when

    def as_dict(self) -> dict[str, str]:
        return {"when": self.when, "why": self.why, "label": self.label}


@dataclass
class Proposal:
    slots: list[Slot] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)
    panel: list[str] = field(default_factory=list)
    minutes: int = 60
    within: str = ""
    searched: list[str] = field(default_factory=list)
    # Set only when the message asked for a particular time.
    requested_time_available: bool | None = None

    @property
    def found(self) -> bool:
        return bool(self.slots)

    @property
    def best(self) -> Slot | None:
        return self.slots[0] if self.slots else None

    def as_dict(self, cfg: ResolvedConfig | None = None) -> dict[str, Any]:
        return {
            "slots": [s.as_dict(cfg) for s in self.slots],
            "rejected": [r.as_dict() for r in self.rejected],
            "panel": self.panel,
            "minutes": self.minutes,
            "within": self.within,
            "searched": self.searched,
            "requested_time_available": self.requested_time_available,
        }


def _days_to_search(cfg: ResolvedConfig, request: Request, *, on: date) -> tuple[list[date], list[Rejected]]:
    """The days worth looking at, and why any named day was not one of them."""
    rejected: list[Rejected] = []
    if request.on is not None:
        if not is_working_day(cfg, request.on):
            rejected.append(Rejected(
                request.on.isoformat(),
                f"this company does not work on a {WEEKDAYS[request.on.weekday()]}",
                label=written_date(cfg, request.on),
            ))
            return [], rejected
        if request.on < on:
            rejected.append(Rejected(
                request.on.isoformat(), "that day has already passed",
                label=written_date(cfg, request.on),
            ))
            return [], rejected
        return [request.on], rejected

    days, day = [], on
    for _ in range(cfg.interviews.search_days * 3):
        if len(days) >= cfg.interviews.search_days:
            break
        if is_working_day(cfg, day):
            days.append(day)
        day += timedelta(days=1)
    return days, rejected


def _busy_minutes(
    conn: sqlite3.Connection,
    cfg: ResolvedConfig,
    *,
    panel: list[str],
    day: date,
    read,
    exclude: str | None = None,
) -> tuple[dict[str, list[tuple[int, int]]], dict[str, int]]:
    """Busy windows per person for one day, and how many interviews each already has.

    The calendar system is the authority on busy time, and interviews this agent
    has already booked are in it -- but they are also in our own table, and
    reading both is what stops the second request of the morning proposing the
    hour the first one just took.

    `exclude` is the interview being moved. Without it a reschedule competes
    with itself: the hour it currently occupies reads as busy, and a 90-minute
    interview with a 30-minute buffer would rule out two and a half hours of the
    only day anybody asked about.
    """
    busy: dict[str, list[tuple[int, int]]] = {person_id: [] for person_id in panel}
    answer = read(date=day.isoformat(), person_ids=panel, exclude_interview_id=exclude)
    for window in answer.get("busy") or []:
        person_id = window.get("person_id")
        if person_id in busy:
            busy[person_id].append((to_minutes(window["busy_from"]), to_minutes(window["busy_to"])))

    counts = {person_id: 0 for person_id in panel}
    sql = (
        "SELECT i.start_time, i.end_time, a.person_id FROM interview i "
        "JOIN interview_attendee a ON a.interview_id = i.id AND a.tenant_id = i.tenant_id "
        "WHERE i.tenant_id = ? AND i.on_date = ? AND i.status = 'scheduled' AND a.person_id IS NOT NULL"
    )
    params: list[Any] = [cfg.tenant_id, day.isoformat()]
    if exclude:
        sql += " AND i.id != ?"
        params.append(exclude)
    for row in db.rows(conn, sql, params):
        person_id = row["person_id"]
        if person_id not in busy:
            continue
        counts[person_id] += 1
        window = (to_minutes(row["start_time"]), to_minutes(row["end_time"]))
        if window not in busy[person_id]:
            busy[person_id].append(window)
    return busy, counts


def propose(
    conn: sqlite3.Connection,
    cfg: ResolvedConfig,
    *,
    request: Request,
    read_free_busy,
    on: date,
    exclude: str | None = None,
    not_at: tuple[date, str] | None = None,
) -> Proposal:
    """Find times that work for everyone, under this company's own constraints.

    `read_free_busy(date=, person_ids=)` is the adapter's read operation, passed
    in rather than looked up so that this function is pure domain logic and can
    be tested against any calendar at all.

    `not_at` is the time a reschedule is moving away from. `exclude` frees that
    interview's hours so a nearby alternative can be found; without `not_at` as
    well, the freed hour is simply the best one again and the agent proposes
    moving an interview to the time it is already at.
    """
    settings = cfg.interviews
    hours = cfg.locale.working_hours
    open_at, close_at = to_minutes(hours.start), to_minutes(hours.end)
    proposal = Proposal(panel=list(request.panel_ids), minutes=request.minutes, within=within_label(cfg))

    if not request.panel_ids:
        proposal.rejected.append(Rejected("every day", "nobody was named to interview, so no calendar could be read"))
        return proposal

    days, proposal.rejected = _days_to_search(cfg, request, on=on)
    if not days:
        return proposal

    # Notice is counted from the start of the working day this is being run on.
    # The kernel runs on a date, which is what makes a run reproducible; this is
    # the honest consequence of that and is documented in the module docstring.
    notice_from = datetime.combine(on, datetime.min.time()) + timedelta(minutes=open_at)
    earliest = notice_from + timedelta(hours=settings.min_notice_hours)

    requested = to_minutes(request.at) if request.at else None
    if requested is not None:
        proposal.requested_time_available = False

    for day in days:
        proposal.searched.append(day.isoformat())
        busy, counts = _busy_minutes(
            conn, cfg, panel=request.panel_ids, day=day, read=read_free_busy, exclude=exclude
        )

        at_cap = [p for p, count in counts.items() if count >= settings.max_per_day_per_interviewer]
        if at_cap:
            proposal.rejected.append(Rejected(
                day.isoformat(),
                f"{_names(cfg, at_cap)} already {'has' if len(at_cap) == 1 else 'have'} "
                f"{settings.max_per_day_per_interviewer} interview(s) that day, which is this company's limit",
                label=written_date(cfg, day),
            ))
            continue

        grid = list(range(open_at, close_at - request.minutes + 1, settings.slot_step_minutes))
        # A time the message asked for is tried first, then the rest of the day.
        # Refusing outright because one particular hour was taken is less useful
        # than saying "not 14:00, but 15:30 works" -- and the draft reports which
        # of the two happened, so nobody is told they got the time they asked for.
        starts = grid
        if requested is not None:
            starts = [requested, *[s for s in grid if s != requested]]
            if requested + request.minutes > close_at or requested < open_at:
                proposal.rejected.append(Rejected(
                    f"{day.isoformat()} {to_hhmm(requested)}",
                    f"a {request.minutes}-minute interview starting then would fall outside "
                    f"{within_label(cfg)}",
                    label=f"{written_date(cfg, day)} at {to_hhmm(requested)}",
                ))
                starts = grid

        too_soon = False
        clashed: set[str] = set()
        found_today: Slot | None = None
        unchanged = False
        for start in starts:
            end = start + request.minutes
            if end > close_at or start < open_at:
                continue
            if not_at is not None and not_at[0] == day and to_hhmm(start) == not_at[1]:
                unchanged = True
                continue
            if datetime.combine(day, datetime.min.time()) + timedelta(minutes=start) < earliest:
                too_soon = True
                continue
            busy_with = [
                person_id
                for person_id, windows in busy.items()
                if any(overlaps(start, end, b_from, b_to, buffer_minutes=settings.buffer_minutes)
                       for b_from, b_to in windows)
            ]
            if busy_with:
                clashed.update(busy_with)
                continue
            found_today = Slot(day, to_hhmm(start), to_hhmm(end))
            break

        if found_today is not None:
            proposal.slots.append(found_today)
            if requested is not None and to_minutes(found_today.start) == requested:
                proposal.requested_time_available = True
            if len(proposal.slots) >= settings.propose_slots:
                break
            continue

        if unchanged and found_today is None and not clashed:
            proposal.rejected.append(Rejected(
                f"{day.isoformat()} {not_at[1]}",
                "that is the time it is already at, and moving an interview to the time "
                "it is already at is not a change",
                label=f"{written_date(cfg, day)} at {not_at[1]}",
            ))
        if too_soon and not clashed:
            proposal.rejected.append(Rejected(
                day.isoformat(),
                f"inside this company's {settings.min_notice_hours}-hour notice period, "
                "which is the warning a candidate is given",
                label=written_date(cfg, day),
            ))
        elif clashed:
            proposal.rejected.append(Rejected(
                day.isoformat(),
                f"{_names(cfg, sorted(clashed))} {'is' if len(clashed) == 1 else 'are'} busy for every "
                f"{request.minutes}-minute slot that day, allowing {settings.buffer_minutes} minutes either side",
                label=written_date(cfg, day),
            ))
        else:
            proposal.rejected.append(Rejected(
                day.isoformat(),
                f"no {request.minutes}-minute slot fits inside {within_label(cfg)}",
                label=written_date(cfg, day),
            ))

    return proposal


def _names(cfg: ResolvedConfig, ids: list[str]) -> str:
    lookup = {p.id: p.name for p in cfg.people}
    names = [lookup.get(i, i) for i in ids]
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" and {names[-1]}"


# ------------------------------------------------------------------ the probe


@dataclass
class Probe:
    """What a read-only look at the calendars found.

    Runs during `understand`, before any rule is evaluated, for the same reason
    the policy probe does: whether a time exists is a fact the rules test, and a
    fact has to be known before it can be tested. Reading free/busy has no side
    effects by construction -- the only calendar operation used here is a read,
    and the registry refuses to call a write as a read -- so a probe cannot book
    anything even if something later goes wrong.
    """

    request: Request
    has_calendar: bool = False
    proposal: Proposal | None = None
    existing: dict[str, Any] | None = None
    matches: list[dict[str, Any]] = field(default_factory=list)
    # Set when a calendar IS configured but would not answer. The rule engine
    # treats this the same as having none -- the agent cannot see when anybody is
    # free either way -- but the person reading the reply needs to know which of
    # the two happened, because only one of them is somebody's job to fix.
    unreachable: str | None = None

    @property
    def slot_available(self) -> bool | None:
        """None when no search was run, which is not the same as 'no time'."""
        return None if self.proposal is None else self.proposal.found

    def facts(self) -> dict[str, Any]:
        """Exactly what the rule engine may test about an interview request."""
        out: dict[str, Any] = {
            "has_calendar": self.has_calendar,
            "interview_action": self.request.action,
        }
        if self.slot_available is not None:
            out["slot_available"] = self.slot_available
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "request": self.request.as_dict(),
            "has_calendar": self.has_calendar,
            "unreachable": self.unreachable,
            "proposal": self.proposal.as_dict() if self.proposal else None,   # noqa: E501
            "existing": self.existing["id"] if self.existing else None,
            "ambiguous": [m["id"] for m in self.matches] if not self.existing else [],
        }


def probe(
    conn: sqlite3.Connection,
    cfg: ResolvedConfig,
    *,
    message: str,
    requester: Person | None,
    named_ids: list[str] | None = None,
    read_free_busy=None,
    today: date,
) -> Probe:
    """Read the request and look for a time. Reads only; commits nothing."""
    request = parse(message, cfg, requester=requester, named_ids=named_ids, today=today)
    found = Probe(request=request, has_calendar=read_free_busy is not None)
    if read_free_busy is None:
        return found

    if request.action in {"cancel", "reschedule"}:
        found.existing, found.matches = find_existing(conn, cfg, request=request, on=today)
        if request.action == "cancel":
            return found
        # A reschedule still needs somewhere to move it to, and the interview
        # being moved must not block its own replacement.
        if found.existing:
            request.panel_ids = [
                a["person_id"] for a in found.existing["attendees"]
                if a["part"] in {"panel", "organiser"} and a["person_id"]
            ] or request.panel_ids
            request.candidate_name = request.candidate_name or found.existing["candidate_name"]
            if not request.candidate_email and found.existing["candidate_email"]:
                request.candidate_email = found.existing["candidate_email"]
                request.email_source = "record"
            request.role_title = request.role_title or found.existing["role_title"]

    found.proposal = propose(
        conn, cfg, request=request, read_free_busy=read_free_busy, on=today,
        exclude=found.existing["id"] if found.existing else None,
        not_at=(
            (date.fromisoformat(found.existing["on_date"]), found.existing["start_time"])
            if found.existing else None
        ),
    )
    return found


# ------------------------------------------------------------------ persistence


def new_id() -> str:
    return "int_" + uuid.uuid4().hex[:12]


def record(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    interview_id: str,
    work_item_id: str | None,
    case_id: str | None,
    stage: str | None,
    stage_label: str | None,
    role_title: str | None,
    candidate_name: str | None,
    candidate_email: str | None,
    on_date: str,
    start_time: str,
    end_time: str,
    minutes: int,
    timezone: str,
    location: str | None,
    calendar_ref: str | None,
    conference_url: str | None = None,
    supersedes: str | None = None,
    created_by: str | None = None,
    attendees: list[dict[str, Any]] | None = None,
) -> str:
    """Write a booked interview and everyone on it.

    Called from a calendar adapter's commit, which is the only place an interview
    becomes real. A reschedule marks the one it replaces as superseded in the
    same transaction, so there is never a moment where both look live.
    """
    if supersedes:
        conn.execute(
            "UPDATE interview SET status = 'superseded' WHERE tenant_id = ? AND id = ? AND status = 'scheduled'",
            (tenant_id, supersedes),
        )
    db.insert(conn, "interview", {
        "id": interview_id, "tenant_id": tenant_id, "work_item_id": work_item_id, "case_id": case_id,
        "stage": stage, "stage_label": stage_label, "role_title": role_title,
        "candidate_name": candidate_name, "candidate_email": candidate_email,
        "on_date": on_date, "start_time": start_time, "end_time": end_time, "minutes": minutes,
        "timezone": timezone, "location": location, "status": "scheduled",
        "calendar_ref": calendar_ref, "conference_url": conference_url, "supersedes": supersedes,
        "created_at": db.now(), "created_by": created_by,
    })
    for attendee in attendees or []:
        db.insert(conn, "interview_attendee", {
            "id": "att_" + uuid.uuid4().hex[:12], "tenant_id": tenant_id, "interview_id": interview_id,
            "person_id": attendee.get("person_id"), "name": attendee.get("name"),
            "email": attendee.get("email"), "part": attendee.get("part", "panel"),
        })
    return interview_id


def cancel(
    conn: sqlite3.Connection, *, tenant_id: str, interview_id: str, reason: str, at: str | None = None
) -> bool:
    """Mark an interview cancelled. Returns whether there was one to cancel."""
    cursor = conn.execute(
        "UPDATE interview SET status = 'cancelled', cancelled_at = ?, cancel_reason = ? "
        "WHERE tenant_id = ? AND id = ? AND status = 'scheduled'",
        (at or db.now(), reason, tenant_id, interview_id),
    )
    return cursor.rowcount > 0


def by_id(conn: sqlite3.Connection, tenant_id: str, interview_id: str) -> dict[str, Any] | None:
    row = db.one(conn, "SELECT * FROM interview WHERE tenant_id = ? AND id = ?", (tenant_id, interview_id))
    return _with_attendees(conn, tenant_id, row) if row else None


def _with_attendees(conn: sqlite3.Connection, tenant_id: str, row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["attendees"] = [
        dict(a) for a in db.rows(
            conn,
            "SELECT person_id, name, email, part FROM interview_attendee "
            "WHERE tenant_id = ? AND interview_id = ? ORDER BY part, name",
            (tenant_id, row["id"]),
        )
    ]
    return out


def scheduled(
    conn: sqlite3.Connection,
    cfg: ResolvedConfig,
    *,
    from_date: date | str | None = None,
    to_date: date | str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Interviews in a window, soonest first, each with its attendees."""
    sql = "SELECT * FROM interview WHERE tenant_id = ?"
    params: list[Any] = [cfg.tenant_id]
    if from_date:
        sql += " AND on_date >= ?"
        params.append(from_date if isinstance(from_date, str) else from_date.isoformat())
    if to_date:
        sql += " AND on_date <= ?"
        params.append(to_date if isinstance(to_date, str) else to_date.isoformat())
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY on_date, start_time"
    return [_with_attendees(conn, cfg.tenant_id, r) for r in db.rows(conn, sql, params)]


def find_existing(
    conn: sqlite3.Connection,
    cfg: ResolvedConfig,
    *,
    request: Request,
    on: date,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """The interview a reschedule or cancellation refers to.

    Returns (the one match, every candidate match). Two matches is not a tie to
    be broken by recency: it means the message was ambiguous, and the honest
    answer is to name both and let a person say which. Picking one would move the
    wrong interview and tell the wrong candidate.
    """
    upcoming = scheduled(conn, cfg, from_date=on, status="scheduled")
    if not upcoming:
        return None, []

    matches = upcoming
    if request.candidate_email:
        narrowed = [i for i in matches if (i["candidate_email"] or "").lower() == request.candidate_email.lower()]
        matches = narrowed or matches
    if request.candidate_name:
        wanted = request.candidate_name.lower()
        narrowed = [i for i in matches if wanted in (i["candidate_name"] or "").lower()]
        matches = narrowed or matches
    if request.role_title:
        wanted = request.role_title.lower()
        narrowed = [i for i in matches if wanted in (i["role_title"] or "").lower()]
        matches = narrowed or matches
    if request.stage:
        narrowed = [i for i in matches if i["stage"] == request.stage.id]
        matches = narrowed or matches
    if request.on:
        narrowed = [i for i in matches if i["on_date"] == request.on.isoformat()]
        matches = narrowed or matches

    # Nothing narrowed it at all: the message named no interview in particular.
    if matches is upcoming and len(upcoming) > 1:
        return None, upcoming
    return (matches[0] if len(matches) == 1 else None), matches


def attendee_list(
    cfg: ResolvedConfig, *, request: Request, organiser_id: str | None
) -> list[dict[str, Any]]:
    """Everyone to be invited, with where each one came from.

    Panel addresses are read from the directory. The candidate's is the only one
    taken from the message, and it is the only address here that belongs to
    somebody outside the company.
    """
    lookup = {p.id: p for p in cfg.people}
    out: list[dict[str, Any]] = []
    for person_id in request.panel_ids:
        person = lookup.get(person_id)
        if person is None:
            continue
        out.append({
            "person_id": person.id,
            "name": person.name,
            "email": person.email,
            "part": "organiser" if person.id == organiser_id else "panel",
        })
    if request.candidate_known:
        out.append({
            "person_id": None,
            "name": request.candidate_name,
            "email": request.candidate_email,
            "part": "candidate",
        })
    return out


def horizon(
    conn: sqlite3.Connection, cfg: ResolvedConfig, *, on: date, days: int
) -> list[dict[str, Any]]:
    """Interviews coming up, with anything about them that needs a person.

    Used by the scheduled job. Like every job in this system it reports and
    closes nothing: an interview missing a candidate address is something a
    person fixes, not something an agent quietly papers over.
    """
    out = []
    for row in scheduled(conn, cfg, from_date=on, to_date=on + timedelta(days=days), status="scheduled"):
        concerns = []
        panel = [a for a in row["attendees"] if a["part"] in {"panel", "organiser"}]
        candidate = next((a for a in row["attendees"] if a["part"] == "candidate"), None)
        if candidate is None or not (candidate.get("email") or row["candidate_email"]):
            concerns.append("no candidate address, so nobody outside the company has been told")
        if not panel:
            concerns.append("no interviewer is recorded against it")
        active = {p.id for p in cfg.people if p.active}
        gone = [a["name"] or a["person_id"] for a in panel if a["person_id"] and a["person_id"] not in active]
        if gone:
            concerns.append(f"{', '.join(gone)} is no longer in the directory")
        if row["stage"] and (stage := cfg.interviews.stage(row["stage"])):
            if len(panel) < stage.panel_min:
                concerns.append(
                    f"{len(panel)} interviewer(s), and a {stage.label.lower()} here needs {stage.panel_min}"
                )
        out.append({
            **row,
            "days_away": (date.fromisoformat(row["on_date"]) - on).days,
            "concerns": concerns,
        })
    return out
