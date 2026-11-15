"""Outlook calendars and Outlook mail, through Microsoft Graph.

The same contract as the Google connector, for a company on Microsoft 365:

    adapters:
      - name: calendars
        kind: calendar
        implementation: microsoft_calendar
        operations: [free_busy, prepare_invite, commit_invite, prepare_cancel, commit_cancel]
        settings:
          organizer: recruiting@contoso.com   # the mailbox that owns interview events
          teams_meeting: true                 # attach a Teams link
          credentials: CONTOSO_MS             # optional; default HR_MS

      - name: email
        kind: notify
        implementation: outlook_mail
        operations: [prepare_message, commit_message]
        settings:
          from_address: recruiting@contoso.com

Authorisation is an app registration in the company's Entra ID tenant using
the client-credentials grant, so nobody's personal login is involved. The
secrets are `<PREFIX>_TENANT_ID`, `<PREFIX>_CLIENT_ID` and
`<PREFIX>_CLIENT_SECRET`. The application permissions needed are
`Calendars.ReadWrite` and `Mail.Send`; an Exchange application access policy
should restrict them to the mailboxes named above.

Outlook sends the invitations itself when an event with attendees is created
in the organizer's calendar, and the cancellations when it is cancelled, so
there is no separate "tell the panel" call. The candidate still gets a written
message with the invitation attached, exactly as with every other calendar.
"""

from __future__ import annotations

import base64
import urllib.parse
from typing import Any

from . import connect, interviews
from .connect import Response
from .tools import BaseAdapter, Prepared, ToolError

LOGIN = "https://login.microsoftonline.com"
GRAPH = "https://graph.microsoft.com/v1.0"
DEFAULT_CREDENTIALS = "HR_MS"
SECRETS = ("TENANT_ID", "CLIENT_ID", "CLIENT_SECRET")

# What Outlook reports that should keep an interview out of the slot.
# Tentative counts: a person who might be somewhere else is not free.
BUSY = {"busy", "oof", "tentative"}


def http(method: str, url: str, *, headers=None, json_body=None, form=None) -> Response:  # noqa: ANN001
    return connect.http(method, url, headers=headers, json_body=json_body, form=form, vendor="Microsoft")


# Swapped by tests.
TRANSPORT = http
TOKENS = connect.TokenCache()


class _Microsoft(BaseAdapter):
    VENDOR = "Microsoft"
    SECRETS = SECRETS
    DEFAULT_CREDENTIALS = DEFAULT_CREDENTIALS

    @property
    def graph(self) -> str:
        return connect.require_https(self.decl, str(self.decl.settings.get("graph_url") or GRAPH), "graph_url")

    def _token(self) -> str:
        found = connect.secrets(self.decl, DEFAULT_CREDENTIALS, SECRETS, vendor="Microsoft 365")
        login = connect.require_https(self.decl, str(self.decl.settings.get("login_url") or LOGIN), "login_url")

        def fetch() -> tuple[str, int]:
            reply = TRANSPORT(
                "POST", f"{login}/{urllib.parse.quote(found['TENANT_ID'])}/oauth2/v2.0/token",
                form={
                    "client_id": found["CLIENT_ID"], "client_secret": found["CLIENT_SECRET"],
                    "scope": f"{self.graph.split('/v1.0')[0]}/.default", "grant_type": "client_credentials",
                },
            )
            if reply.status >= 400 or not reply.body.get("access_token"):
                raise ToolError(f"Microsoft would not issue a token: {connect.error_detail(reply.body)}")
            return str(reply.body["access_token"]), int(reply.body.get("expires_in", 3600))

        return TOKENS.get((login, found["TENANT_ID"], found["CLIENT_ID"]), fetch)

    def _call(self, method: str, path: str, *, body: Any = None, headers: dict[str, str] | None = None,
              tolerate: tuple[int, ...] = ()) -> dict[str, Any]:
        reply = TRANSPORT(method, f"{self.graph}{path}",
                          headers={"Authorization": f"Bearer {self._token()}", **(headers or {})}, json_body=body)
        if reply.status >= 400 and reply.status not in tolerate:
            raise ToolError(f"Microsoft refused the request ({reply.status}): {connect.error_detail(reply.body)}")
        return reply.body

    def check_live(self) -> str | None:
        self._token()
        return "Microsoft accepted the credentials."


def _mailbox(value: str) -> str:
    return urllib.parse.quote(value, safe="@.")


class OutlookCalendar(_Microsoft):
    kind = "calendar"
    PRODUCT = "Outlook Calendar"
    REQUIRED_SETTINGS = ("organizer",)
    READS = ("free_busy",)
    WRITES = ("commit_invite", "commit_cancel")

    @property
    def organizer(self) -> str:
        organizer = self.decl.settings.get("organizer")
        if not organizer:
            raise ToolError(f"{self.name} has no organizer mailbox, so there is no calendar to book in")
        return str(organizer)

    # -- read
    def read(self, operation: str, **args: Any) -> dict[str, Any]:
        if operation != "free_busy":
            return super().read(operation, **args)
        hours = self.cfg.locale.working_hours
        timezone = self.cfg.locale.timezone
        day = args["date"]

        wanted: dict[str, str] = {}
        unknown: list[str] = []
        for person_id in args["person_ids"]:
            email = connect.email_of(self, person_id)
            if email:
                wanted[email.lower()] = person_id
            else:
                unknown.append(person_id)

        windows: list[dict[str, str]] = []
        if wanted:
            body = self._call(
                "POST", f"/users/{_mailbox(self.organizer)}/calendar/getSchedule",
                # Ask for the answer in the company's own zone, so the wall clock
                # in the reply needs no conversion.
                headers={"Prefer": f'outlook.timezone="{timezone}"'},
                body={
                    "schedules": list(wanted),
                    "startTime": {"dateTime": f"{day}T{hours.start}:00", "timeZone": timezone},
                    "endTime": {"dateTime": f"{day}T{hours.end}:00", "timeZone": timezone},
                    "availabilityViewInterval": 15,
                },
            )
            for entry in body.get("value") or []:
                person_id = wanted.get(str(entry.get("scheduleId", "")).lower())
                if person_id is None:
                    continue
                if entry.get("error"):
                    # A calendar we may not read is not a free calendar.
                    unknown.append(person_id)
                    continue
                for item in entry.get("scheduleItems") or []:
                    if str(item.get("status", "")).lower() not in BUSY:
                        continue
                    windows.append({
                        "person_id": person_id,
                        "busy_from": connect.clock(day, (item.get("start") or {}).get("dateTime"), hours.start),
                        "busy_to": connect.clock(day, (item.get("end") or {}).get("dateTime"), hours.end),
                    })

        return {
            "date": day,
            "busy": connect.without_rescheduled(self, day, windows, args.get("exclude_interview_id")),
            "unknown": sorted(set(unknown)),
            "free_starts": [],
            "buffer_minutes": int(self.decl.settings.get("buffer_minutes", self.cfg.interviews.buffer_minutes)),
            "within": f"{hours.start}-{hours.end} {timezone}",
        }

    # -- prepare
    def preview(self, operation: str, **args: Any) -> Prepared:
        if operation == "prepare_invite":
            extra = [f"  Outlook will email the invitation from {self.organizer}"]
            if self.decl.settings.get("teams_meeting"):
                extra.append("  A Microsoft Teams link will be created with it")
            return Prepared(
                preview=connect.invite_preview(self, args, product=f"Outlook ({self.organizer})", extra=extra),
                args=dict(args),
            )
        if operation == "prepare_cancel":
            return Prepared(
                preview=(
                    f"Cancel the {args.get('stage_label') or 'interview'} of {args['date']} {args['start']} "
                    f"in Outlook ({self.organizer}).\n"
                    "  Outlook will send the cancellation to everyone invited\n"
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
            return self._cancel(**args)
        return super().act(operation, **args)

    def _create(self, **args: Any) -> dict[str, Any]:
        timezone = args.get("timezone") or self.cfg.locale.timezone
        interview_id = args.get("interview_id") or interviews.new_id()
        event: dict[str, Any] = {
            "subject": (args.get("stage_label") or "Interview")
            + (f" — {args['role_title']}" if args.get("role_title") else ""),
            # Local wall clock plus a named zone, never UTC, so a clock change
            # between now and the interview cannot move it.
            "start": {"dateTime": f"{args['date']}T{args['start']}:00", "timeZone": timezone},
            "end": {"dateTime": f"{args['date']}T{args['end']}:00", "timeZone": timezone},
            "attendees": [
                {"emailAddress": {"address": a["email"], "name": a.get("name") or ""}, "type": "required"}
                for a in (args.get("attendees") or []) if a.get("email")
            ],
            "sensitivity": "private",
            "showAs": "busy",
            "hideAttendees": True,
            "allowNewTimeProposals": False,
            # Graph's own idempotency key: a retried create returns the first
            # event instead of booking a second one.
            "transactionId": interview_id,
        }
        if args.get("location"):
            event["location"] = {"displayName": args["location"]}
        if self.decl.settings.get("teams_meeting"):
            event["isOnlineMeeting"] = True
            event["onlineMeetingProvider"] = "teamsForBusiness"

        created = self._call("POST", f"/users/{_mailbox(self.organizer)}/events", body=event)
        join = (created.get("onlineMeeting") or {}).get("joinUrl")
        connect.record_interview(self, args, interview_id=interview_id, calendar_ref=created.get("id"),
                                 conference_url=join)
        return {
            "interview_id": interview_id, "invite_id": created.get("id"), "calendar_ref": created.get("id"),
            "conference_url": join, "html_link": created.get("webLink"),
            "date": args["date"], "start": args["start"], "end": args["end"], "notified": "attendees",
        }

    def _cancel(self, **args: Any) -> dict[str, Any]:
        booked = interviews.by_id(self.conn, self.cfg.tenant_id, args["interview_id"])
        reference = (booked or {}).get("calendar_ref")
        if reference:
            # 404 means it is already gone, which is the state we wanted.
            self._call(
                "POST",
                f"/users/{_mailbox(self.organizer)}/events/{urllib.parse.quote(str(reference))}/cancel",
                body={"comment": args.get("reason") or "This interview has been cancelled."},
                tolerate=(404,),
            )
        cancelled = interviews.cancel(
            self.conn, tenant_id=self.cfg.tenant_id, interview_id=args["interview_id"],
            reason=args.get("reason") or "cancelled by the people team",
        )
        return {"cancelled": cancelled, "interview_id": args["interview_id"],
                "calendar_ref": reference, "notified": "attendees"}


class OutlookMail(_Microsoft):
    """Email to anyone, candidates included, from a company mailbox."""

    kind = "notify"
    PRODUCT = "Outlook mail"
    REQUIRED_SETTINGS = ("from_address",)
    READS = ()
    WRITES = ("commit_message",)
    REACHES_EXTERNAL = True

    def preview(self, operation: str, **args: Any) -> Prepared:
        if operation != "prepare_message":
            return super().preview(operation, **args)
        to = args.get("to")
        if not to:
            raise ToolError(f"{self.name} needs an address to send to, and none was given")
        attached = "\n  With a calendar invitation attached (invite.ics)" if args.get("ics") else ""
        return Prepared(
            preview=(
                f"Send an email through Outlook, from {self.decl.settings.get('from_address')} to {to}{attached}\n"
                f"  Subject: {args.get('subject') or '(no subject)'}\n\n{args['body']}"
            ),
            args={"to": to, "subject": args.get("subject"), "body": args["body"],
                  "ics": args.get("ics"), "work_item_id": args["work_item_id"]},
        )

    def act(self, operation: str, **args: Any) -> dict[str, Any]:
        if operation != "commit_message":
            return super().act(operation, **args)
        sender = self.decl.settings.get("from_address")
        if not sender:
            raise ToolError(f"{self.name} has no from_address, so there is no mailbox to send from")
        subject = args.get("subject") or "(no subject)"
        message: dict[str, Any] = {
            "subject": subject,
            "body": {"contentType": "Text", "content": args["body"]},
            "toRecipients": [{"emailAddress": {"address": args["to"]}}],
        }
        if reply_to := self.decl.settings.get("reply_to"):
            message["replyTo"] = [{"emailAddress": {"address": str(reply_to)}}]
        if args.get("ics"):
            message["attachments"] = [{
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": "invite.ics",
                "contentType": "text/calendar; method=REQUEST",
                "contentBytes": base64.b64encode(args["ics"].encode("utf-8")).decode("ascii"),
            }]
        self._call("POST", f"/users/{_mailbox(str(sender))}/sendMail",
                   body={"message": message, "saveToSentItems": True})
        record_id = connect.log_message(
            self, args, f"To: {args['to']}\nSubject: {subject}\n\n{args['body']}", prefix="mail")
        return {"message_id": record_id, "to": args["to"], "subject": subject,
                "carried_invitation": bool(args.get("ics"))}


IMPLEMENTATIONS: dict[str, type[BaseAdapter]] = {
    "microsoft_calendar": OutlookCalendar,
    "outlook_mail": OutlookMail,
}
