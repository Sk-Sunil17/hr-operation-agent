"""Calendar invitations, as the one format every calendar client understands.

This exists so that the message the agent prepares for a candidate carries a
real invitation rather than a sentence describing one. A candidate who has to
copy a date out of an email into their own calendar is a candidate who turns up
on the wrong day, and that is our fault rather than theirs.

Two decisions worth stating:

  * Local time with a `TZID`, never UTC. The company's locale supplies the zone,
    and writing `14:00 Europe/London` is both what the company meant and what
    survives a daylight-saving change between now and the interview. Converting
    to UTC here would bake in today's offset.
  * The `UID` is the interview's own id and `SEQUENCE` counts revisions, which is
    what makes a reschedule REPLACE the entry in a candidate's calendar instead
    of adding a second one next to it. Getting this wrong is how someone ends up
    with two interviews on two days and no idea which is real.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

# RFC 5545 wants CRLF line endings, and clients are stricter about it than you
# would hope.
CRLF = "\r\n"
PRODID = "-//HR Operations Agent//Interview scheduling//EN"


def _escape(value: str) -> str:
    """Escape the four characters that mean something in an iCalendar value."""
    return (
        (value or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> list[str]:
    """Fold at 75 octets, as the spec requires. Continuations start with a space."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return [line]
    out, chunk = [], bytearray()
    for char in line:
        encoded = char.encode("utf-8")
        limit = 75 if not out else 74       # a continuation spends one octet on its space
        if len(chunk) + len(encoded) > limit:
            out.append(chunk.decode("utf-8"))
            chunk = bytearray()
        chunk += encoded
    if chunk:
        out.append(chunk.decode("utf-8"))
    return [out[0], *(" " + part for part in out[1:])]


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y%m%dT%H%M%S")


@dataclass
class Attendee:
    email: str
    name: str | None = None
    # A candidate is asked whether they can come; a panel member is told.
    role: str = "REQ-PARTICIPANT"


def invitation(
    *,
    uid: str,
    summary: str,
    on: date | str,
    start: str,
    end: str,
    timezone: str,
    organiser_email: str | None = None,
    organiser_name: str | None = None,
    attendees: list[Attendee] | None = None,
    location: str | None = None,
    description: str | None = None,
    sequence: int = 0,
    cancelled: bool = False,
    created_at: datetime | None = None,
) -> str:
    """One VEVENT, as a complete iCalendar object.

    `cancelled` produces the withdrawal a calendar client needs in order to take
    the entry out again. A cancellation that is merely not sent leaves the
    interview sitting in four people's calendars for ever.
    """
    day = date.fromisoformat(on) if isinstance(on, str) else on
    stamped = created_at or datetime(2000, 1, 1)
    begin = datetime.combine(day, datetime.strptime(start, "%H:%M").time())
    finish = datetime.combine(day, datetime.strptime(end, "%H:%M").time())

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        f"METHOD:{'CANCEL' if cancelled else 'REQUEST'}",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"SEQUENCE:{sequence}",
        f"DTSTAMP:{_stamp(stamped)}Z",
        f"DTSTART;TZID={timezone}:{_stamp(begin)}",
        f"DTEND;TZID={timezone}:{_stamp(finish)}",
        f"SUMMARY:{_escape(summary)}",
        f"STATUS:{'CANCELLED' if cancelled else 'CONFIRMED'}",
        # Interviews are not a statement about anyone's general availability to
        # the whole company, so the entry is busy but not public.
        "TRANSP:OPAQUE",
        "CLASS:PRIVATE",
    ]
    if location:
        lines.append(f"LOCATION:{_escape(location)}")
    if description:
        lines.append(f"DESCRIPTION:{_escape(description)}")
    if organiser_email:
        name = f';CN="{_escape(organiser_name)}"' if organiser_name else ""
        lines.append(f"ORGANIZER{name}:mailto:{organiser_email}")
    for attendee in attendees or []:
        parts = [f"ROLE={attendee.role}", "PARTSTAT=NEEDS-ACTION", "RSVP=TRUE"]
        if attendee.name:
            parts.append(f'CN="{_escape(attendee.name)}"')
        lines.append("ATTENDEE;" + ";".join(parts) + f":mailto:{attendee.email}")
    lines += ["END:VEVENT", "END:VCALENDAR"]

    folded: list[str] = []
    for line in lines:
        folded.extend(_fold(line))
    return CRLF.join(folded) + CRLF
