"""Google Calendar and Gmail, as adapters like any other.

A company that wants interviews in real calendars and real invitations in real
inboxes names these in its pack instead of the mocks. Nothing else changes: the
kernel, the rules, the verify gate and the approval queue are untouched, because
the whole point of the adapter interface is that the system of record is a
configuration choice.

    adapters:
      - name: calendars
        kind: calendar
        implementation: google_calendar
        operations: [free_busy, prepare_invite, commit_invite, prepare_cancel, commit_cancel]
        settings:
          calendar_id: primary
          conferencing: true          # ask Google for a Meet link
          send_updates: all           # Google emails the attendees itself

      - name: email
        kind: notify
        implementation: gmail
        operations: [prepare_message, commit_message]
        settings:
          from_address: "people@example.com"

Four decisions worth stating.

**Credentials never live in a pack.** Packs are configuration, they are read from
disk, they are hashed into every decision and they belong in version control. A
refresh token belongs in none of those places, so it comes from the environment
and the pack holds only the things it is safe to read in a code review.

**No credentials means a loud failure, not a quiet one.** If the token is absent
these adapters raise, the kernel's act phase catches it, prepared actions are
cancelled and a person is told a system did not respond. The one behaviour that
is never acceptable is pretending to have sent something.

**`read` stays a read.** `free_busy` calls `freeBusy.query`, which is a POST at
the HTTP level but has no effect on anything. Nothing in here writes during a
read, and the registry independently refuses to call a write operation as a read.

**The transport is injectable.** Every HTTP call goes through one function, so
the request bodies this module builds are exercised by tests against a fake
rather than left to be discovered in production. Standard library only: a
refresh-token grant needs no signing, so there is no dependency to install and
no key material to manage beyond the token itself.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

from . import db, interviews
from .tools import BaseAdapter, Prepared, ToolError

TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1"

# Read-only would be enough for free/busy, but creating an event and sending a
# message are writes, and these are the narrowest scopes that allow them.
SCOPES = (
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.send",
)

ENV_CLIENT_ID = "HR_GOOGLE_CLIENT_ID"
ENV_CLIENT_SECRET = "HR_GOOGLE_CLIENT_SECRET"
ENV_REFRESH_TOKEN = "HR_GOOGLE_REFRESH_TOKEN"

TIMEOUT_SECONDS = 20


# ------------------------------------------------------------------ transport


@dataclass
class Response:
    status: int
    body: dict[str, Any]


def http(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    form: dict[str, str] | None = None,
) -> Response:
    """One HTTP call. Replaced wholesale in tests, which is the point.

    Google's errors arrive as a JSON body with a non-2xx status, and the message
    inside it is the useful part -- so the body is read on failure too, rather
    than being discarded in favour of "HTTP 403".
    """
    data: bytes | None = None
    sending = dict(headers or {})
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        sending["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        sending["Content-Type"] = "application/x-www-form-urlencoded"

    request = urllib.request.Request(url, data=data, headers=sending, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as reply:
            raw = reply.read().decode("utf-8") or "{}"
            return Response(reply.status, json.loads(raw))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") or "{}"
        try:
            body = json.loads(raw)
        except ValueError:
            body = {"error": {"message": raw[:400]}}
        return Response(exc.code, body)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ToolError(f"Google did not respond: {exc}") from exc


# Swapped by tests. A module-level hook rather than constructor injection,
# because an adapter is built by the registry from a pack declaration and has
# nowhere to receive one.
TRANSPORT = http


def _call(
    method: str, url: str, *, token: str, json_body: dict[str, Any] | None = None
) -> dict[str, Any]:
    reply = TRANSPORT(method, url, headers={"Authorization": f"Bearer {token}"}, json_body=json_body)
    if reply.status >= 400:
        message = (reply.body.get("error") or {})
        detail = message.get("message") if isinstance(message, dict) else str(message)
        raise ToolError(f"Google refused the request ({reply.status}): {detail or 'no reason given'}")
    return reply.body


# ------------------------------------------------------------------ credentials


@dataclass
class Credentials:
    """An access token, refreshed when it is about to expire.

    Cached per process. A token lasts an hour and a refresh is a round trip, so
    fetching one per adapter call would make every scheduling request several
    times slower for nothing.
    """

    client_id: str
    client_secret: str
    refresh_token: str
    _token: str | None = field(default=None, repr=False)
    _expires: datetime | None = field(default=None, repr=False)

    @classmethod
    def from_environment(cls) -> Credentials:
        missing = [
            name for name in (ENV_CLIENT_ID, ENV_CLIENT_SECRET, ENV_REFRESH_TOKEN)
            if not os.environ.get(name)
        ]
        if missing:
            raise ToolError(
                "Google is configured as the system of record but is not authorised: "
                f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not set. "
                "Nothing has been sent or booked."
            )
        return cls(
            client_id=os.environ[ENV_CLIENT_ID],
            client_secret=os.environ[ENV_CLIENT_SECRET],
            refresh_token=os.environ[ENV_REFRESH_TOKEN],
        )

    def token(self) -> str:
        now = datetime.now(UTC)
        if self._token and self._expires and now < self._expires:
            return self._token
        reply = TRANSPORT(
            "POST",
            TOKEN_URL,
            form={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if reply.status >= 400 or not reply.body.get("access_token"):
            detail = reply.body.get("error_description") or reply.body.get("error") or "no reason given"
            raise ToolError(f"Google would not renew the authorisation: {detail}")
        self._token = str(reply.body["access_token"])
        # A minute of slack, so a token does not expire mid-request.
        self._expires = now + timedelta(seconds=int(reply.body.get("expires_in", 3600)) - 60)
        return self._token


_CREDENTIALS: Credentials | None = None


def credentials() -> Credentials:
    global _CREDENTIALS
    if _CREDENTIALS is None:
        _CREDENTIALS = Credentials.from_environment()
    return _CREDENTIALS


def forget_credentials() -> None:
    """Test hook, and the way to pick up a rotated token without a restart."""
    global _CREDENTIALS
    _CREDENTIALS = None


# ------------------------------------------------------------------ time


def _rfc3339(day: str, hhmm: str, timezone: str) -> str:
    """A local wall-clock time with its zone named separately.

    Google accepts `dateTime` plus `timeZone`, which is what the company
    actually means: 14:00 in its own zone, whatever the offset turns out to be
    on the day. Converting to UTC here would freeze today's offset into an
    interview that may fall the other side of a clock change.
    """
    return f"{day}T{hhmm}:00"


# ------------------------------------------------------------------ calendar


class GoogleCalendar(BaseAdapter):
    """Real free/busy and real calendar entries.

    Google emails the attendees itself when `send_updates` is on, which is why
    there is no separate "tell the panel" call in here: duplicating the
    notification would put two invitations in everyone's inbox for one interview.
    The candidate still gets a written message, because a bare calendar
    notification is not an invitation a person would have sent.
    """

    kind = "calendar"
    READS = ("free_busy",)
    WRITES = ("commit_invite", "commit_cancel")

    @property
    def calendar_id(self) -> str:
        return str(self.decl.settings.get("calendar_id", "primary"))

    @property
    def send_updates(self) -> str:
        return str(self.decl.settings.get("send_updates", "all"))

    def _email_of(self, person_id: str) -> str | None:
        person = next((p for p in self.cfg.people if p.id == person_id), None)
        return person.email if person else None

    # -- read
    def read(self, operation: str, **args: Any) -> dict[str, Any]:
        if operation != "free_busy":
            return super().read(operation, **args)
        hours = self.cfg.locale.working_hours
        timezone = self.cfg.locale.timezone
        day = args["date"]

        # Who to ask about. A person with no address in the directory cannot be
        # looked up, and is reported rather than silently treated as free --
        # "nobody told me they were busy" is not the same as "they are free".
        wanted: dict[str, str] = {}
        unknown: list[str] = []
        for person_id in args["person_ids"]:
            email = self._email_of(person_id)
            if email:
                wanted[email.lower()] = person_id
            else:
                unknown.append(person_id)

        windows: list[dict[str, str]] = []
        if wanted:
            body = _call(
                "POST",
                f"{CALENDAR_API}/freeBusy",
                token=credentials().token(),
                json_body={
                    "timeMin": _rfc3339(day, hours.start, timezone),
                    "timeMax": _rfc3339(day, hours.end, timezone),
                    "timeZone": timezone,
                    "items": [{"id": email} for email in wanted],
                },
            )
            for email, entry in (body.get("calendars") or {}).items():
                person_id = wanted.get(email.lower())
                if person_id is None:
                    continue
                if entry.get("errors"):
                    unknown.append(person_id)
                    continue
                for busy in entry.get("busy") or []:
                    windows.append({
                        "person_id": person_id,
                        "busy_from": _hhmm_of(busy.get("start"), hours.start),
                        "busy_to": _hhmm_of(busy.get("end"), hours.end),
                    })

        # An interview this agent booked and is now moving is excluded, exactly
        # as the mock does, so a reschedule does not compete with itself.
        excluded = args.get("exclude_interview_id")
        if excluded:
            booked = interviews.by_id(self.conn, self.cfg.tenant_id, excluded)
            if booked and booked["on_date"] == day:
                windows = [
                    w for w in windows
                    if not (w["busy_from"] == booked["start_time"] and w["busy_to"] == booked["end_time"])
                ]

        return {
            "date": day,
            "busy": windows,
            "unknown": sorted(set(unknown)),
            "free_starts": [],      # the solver works from busy windows, not from this
            "buffer_minutes": int(self.decl.settings.get("buffer_minutes", self.cfg.interviews.buffer_minutes)),
            "within": f"{hours.start}-{hours.end} {self.cfg.locale.timezone}",
        }

    # -- prepare
    def preview(self, operation: str, **args: Any) -> Prepared:
        if operation == "prepare_invite":
            attendees = args.get("attendees") or []
            candidate = next((a for a in attendees if a.get("part") == "candidate"), None)
            panel = [a for a in attendees if a.get("part") != "candidate"]
            lines = [
                f"Create a {args.get('minutes', 60)}-minute event in Google Calendar "
                f"({self.calendar_id}) on {args['date']} at {args['start']} "
                f"({args.get('timezone') or self.cfg.locale.timezone}).",
                f"  Google will email the invitation to: {self.send_updates}",
            ]
            if args.get("role_title"):
                lines.append(f"  For: {args['role_title']}")
            if args.get("location"):
                lines.append(f"  Where: {args['location']}")
            if self.decl.settings.get("conferencing"):
                lines.append("  A Google Meet link will be created with it")
            lines.append(
                "  Interviewers: "
                + (", ".join(f"{a.get('name')} <{a.get('email')}>" for a in panel) or "nobody")
            )
            lines.append(
                f"  Candidate: {candidate.get('name') or 'not named'} <{candidate.get('email')}>"
                "  — taken from the message, not from the directory"
                if candidate and candidate.get("email")
                else "  Candidate: not identified, so nobody outside the company is invited"
            )
            return Prepared(preview="\n".join(lines), args=dict(args))

        if operation == "prepare_cancel":
            return Prepared(
                preview=(
                    f"Delete the {args.get('stage_label') or 'interview'} event of {args['date']} "
                    f"{args['start']} from Google Calendar ({self.calendar_id}).\n"
                    f"  Google will send a cancellation to: {self.send_updates}\n"
                    f"  Reason: {args.get('reason') or 'not given'}"
                ),
                args=dict(args),
            )
        return super().preview(operation, **args)

    # -- commit
    def act(self, operation: str, **args: Any) -> dict[str, Any]:
        if operation == "commit_invite":
            return self._create(**args)
        if operation == "commit_cancel":
            return self._delete(**args)
        return super().act(operation, **args)

    def _create(self, **args: Any) -> dict[str, Any]:
        timezone = args.get("timezone") or self.cfg.locale.timezone
        attendees = args.get("attendees") or []
        interview_id = args.get("interview_id") or interviews.new_id()

        event: dict[str, Any] = {
            "summary": (args.get("stage_label") or "Interview")
            + (f" — {args['role_title']}" if args.get("role_title") else ""),
            "start": {"dateTime": _rfc3339(args["date"], args["start"], timezone), "timeZone": timezone},
            "end": {"dateTime": _rfc3339(args["date"], args["end"], timezone), "timeZone": timezone},
            "attendees": [
                {"email": a["email"], "displayName": a.get("name") or "",
                 "optional": False, "responseStatus": "needsAction"}
                for a in attendees if a.get("email")
            ],
            # Private, and busy. An interview is not a statement about anyone's
            # availability to the whole company.
            "visibility": "private",
            "transparency": "opaque",
            "guestsCanInviteOthers": False,
            "guestsCanSeeOtherGuests": False,
            # Our own id travels with the event, so a later reschedule or
            # cancellation can find it again without a lookup table.
            "extendedProperties": {"private": {"hr_interview_id": interview_id,
                                               "hr_tenant_id": self.cfg.tenant_id}},
        }
        if args.get("location"):
            event["location"] = args["location"]

        query = {"sendUpdates": self.send_updates}
        if self.decl.settings.get("conferencing"):
            event["conferenceData"] = {
                "createRequest": {
                    "requestId": interview_id,
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }
            query["conferenceDataVersion"] = "1"

        created = _call(
            "POST",
            f"{CALENDAR_API}/calendars/{urllib.parse.quote(self.calendar_id)}/events"
            f"?{urllib.parse.urlencode(query)}",
            token=credentials().token(),
            json_body=event,
        )

        conference = None
        for entry in ((created.get("conferenceData") or {}).get("entryPoints") or []):
            if entry.get("entryPointType") == "video":
                conference = entry.get("uri")
                break

        interviews.record(
            self.conn,
            tenant_id=self.cfg.tenant_id,
            interview_id=interview_id,
            work_item_id=args.get("work_item_id"),
            case_id=args.get("case_id"),
            stage=args.get("stage"),
            stage_label=args.get("stage_label"),
            role_title=args.get("role_title"),
            candidate_name=next((a.get("name") for a in attendees if a.get("part") == "candidate"), None),
            candidate_email=next((a.get("email") for a in attendees if a.get("part") == "candidate"), None),
            on_date=args["date"],
            start_time=args["start"],
            end_time=args["end"],
            minutes=int(args.get("minutes", 60)),
            timezone=timezone,
            location=args.get("location") or created.get("hangoutLink") or None,
            calendar_ref=created.get("id"),
            conference_url=conference or created.get("hangoutLink"),
            supersedes=args.get("supersedes"),
            created_by=args.get("actor"),
            attendees=attendees,
        )
        return {
            "interview_id": interview_id,
            "invite_id": created.get("id"),
            "calendar_ref": created.get("id"),
            "conference_url": conference or created.get("hangoutLink"),
            "html_link": created.get("htmlLink"),
            "date": args["date"],
            "start": args["start"],
            "end": args["end"],
            "notified": self.send_updates,
        }

    def _delete(self, **args: Any) -> dict[str, Any]:
        booked = interviews.by_id(self.conn, self.cfg.tenant_id, args["interview_id"])
        reference = (booked or {}).get("calendar_ref")
        if reference:
            reply = TRANSPORT(
                "DELETE",
                f"{CALENDAR_API}/calendars/{urllib.parse.quote(self.calendar_id)}/events/"
                f"{urllib.parse.quote(str(reference))}"
                f"?{urllib.parse.urlencode({'sendUpdates': self.send_updates})}",
                headers={"Authorization": f"Bearer {credentials().token()}"},
            )
            # 404 and 410 mean it is already gone, which is the state we wanted.
            if reply.status >= 400 and reply.status not in (404, 410):
                detail = (reply.body.get("error") or {})
                raise ToolError(
                    f"Google would not remove the event ({reply.status}): "
                    f"{detail.get('message') if isinstance(detail, dict) else detail}"
                )
        cancelled = interviews.cancel(
            self.conn, tenant_id=self.cfg.tenant_id, interview_id=args["interview_id"],
            reason=args.get("reason") or "cancelled by the people team",
        )
        return {
            "cancelled": cancelled, "interview_id": args["interview_id"],
            "calendar_ref": reference, "notified": self.send_updates,
        }


def _hhmm_of(timestamp: str | None, fallback: str) -> str:
    """The local hour and minute out of an RFC-3339 instant.

    Google answers a free/busy query in the zone the query named, so the wall
    clock in the reply is already the company's own.
    """
    if not timestamp:
        return fallback
    try:
        moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return fallback
    return f"{moment.hour:02d}:{moment.minute:02d}"


# ------------------------------------------------------------------ mail


class Gmail(BaseAdapter):
    """Sends the candidate's invitation, with the calendar entry attached.

    The one adapter in this file that reaches somebody who does not work here,
    which is the whole reason it exists: a candidate has no account in any
    internal system, so without a way to email them an interview cannot be
    communicated at all.
    """

    kind = "notify"
    READS = ()
    WRITES = ("commit_message",)
    REACHES_EXTERNAL = True

    @property
    def sender(self) -> str:
        address = self.decl.settings.get("from_address")
        if not address:
            raise ToolError(
                f"{self.name} has no from_address in its configuration, so there is no "
                "mailbox to send a candidate's invitation from"
            )
        return str(address)

    def preview(self, operation: str, **args: Any) -> Prepared:
        if operation != "prepare_message":
            return super().preview(operation, **args)
        to = args.get("to")
        if not to:
            raise ToolError(f"{self.name} needs an address to send to, and none was given")
        attached = "\n  With a calendar invitation attached (invite.ics)" if args.get("ics") else ""
        return Prepared(
            preview=(
                f"Send a real email through Gmail, from {self.sender} to {to}{attached}\n"
                f"  Subject: {args.get('subject') or '(no subject)'}\n\n{args['body']}"
            ),
            args={
                "to": to, "subject": args.get("subject"), "body": args["body"],
                "ics": args.get("ics"), "work_item_id": args["work_item_id"],
            },
        )

    def act(self, operation: str, **args: Any) -> dict[str, Any]:
        if operation != "commit_message":
            return super().act(operation, **args)
        subject = args.get("subject") or "(no subject)"
        message = EmailMessage()
        message["To"] = args["to"]
        message["From"] = self.sender
        message["Subject"] = subject
        if reply_to := self.decl.settings.get("reply_to"):
            message["Reply-To"] = str(reply_to)
        message.set_content(args["body"])
        if invitation := args.get("ics"):
            # `method=REQUEST` is what makes a mail client offer "add to
            # calendar" rather than treating it as an unremarkable attachment.
            message.add_attachment(
                invitation.encode("utf-8"),
                maintype="text", subtype="calendar",
                filename="invite.ics",
                params={"method": "REQUEST", "name": "invite.ics"},
            )

        sent = _call(
            "POST",
            f"{GMAIL_API}/users/me/messages/send",
            token=credentials().token(),
            json_body={"raw": base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")},
        )

        # The log records what was sent, as it does for every other notifier, so
        # "what did we actually tell this candidate" has an answer.
        record_id = "mail_" + uuid.uuid4().hex[:12]
        db.insert(self.conn, "message_log", {
            "id": record_id,
            "tenant_id": self.cfg.tenant_id,
            "work_item_id": args["work_item_id"],
            "direction": "outbound",
            "channel": self.name,
            "body": f"To: {args['to']}\nSubject: {subject}\n\n{args['body']}",
            "idempotency_key": args.get("idempotency_key"),
            "suppressed": 0,
            "at": db.now(),
        })
        return {
            "message_id": sent.get("id") or record_id,
            "thread_id": sent.get("threadId"),
            "to": args["to"],
            "subject": subject,
            "carried_invitation": bool(args.get("ics")),
        }


IMPLEMENTATIONS: dict[str, type[BaseAdapter]] = {
    "google_calendar": GoogleCalendar,
    "gmail": Gmail,
}
