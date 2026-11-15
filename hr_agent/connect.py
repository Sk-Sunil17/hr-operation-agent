"""What every connector to an outside system shares, whoever the vendor is.

Companies use different products: one books interviews in Google Calendar,
another in Outlook, a third mails candidates through its own SMTP relay and
keeps staff records in BambooHR. The kernel never knows which. It asks the
registry for "a calendar" or "something that can email a candidate", and the
pack decides what answers.

What does NOT vary by vendor lives here, so every connector behaves the same:

  * **Credentials come from the environment, named per adapter.** A pack says
    `credentials: ACME_GOOGLE` and the secrets are read from
    `ACME_GOOGLE_CLIENT_ID` and friends. Two companies on one server can
    therefore use two different Google (or Microsoft) accounts. The pack itself
    never holds a secret; the model refuses one.
  * **No credentials is a loud failure.** A missing variable raises before any
    request is made, and says which variable, so the kernel hands the work to a
    person instead of pretending something was sent.
  * **One transport per process, replaceable in tests**, so the request bodies
    each connector builds are asserted rather than discovered in production.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import TYPE_CHECKING, Any

from . import db, interviews
from .tools import ToolError

if TYPE_CHECKING:
    from .packs.models import Adapter as AdapterDecl
    from .tools import BaseAdapter

TIMEOUT_SECONDS = 20

_PREFIX = re.compile(r"[A-Z][A-Z0-9_]*")


class ConnectorError(ToolError):
    """An outside system could not be reached, or is not set up to be."""


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
    json_body: Any = None,
    form: dict[str, str] | None = None,
    vendor: str = "The service",
) -> Response:
    """One HTTP call. Vendors put the useful part of an error in the body, so the
    body is read on failure too rather than reduced to "HTTP 403"."""
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
            return Response(reply.status, _json(reply.read()))
    except urllib.error.HTTPError as exc:
        return Response(exc.code, _json(exc.read(), keep_text=True))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ConnectorError(f"{vendor} did not respond: {exc}") from exc


def _json(raw: bytes, *, keep_text: bool = False) -> dict[str, Any]:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except ValueError:
        return {"error": {"message": text[:400]}} if keep_text else {"text": text[:400]}
    return parsed if isinstance(parsed, dict) else {"items": parsed}


def error_detail(body: dict[str, Any]) -> str:
    """The human part of a vendor's error body, whichever shape it takes."""
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "no reason given")
    return str(error or body.get("message") or body.get("error_description") or body.get("text") or "no reason given")


# ------------------------------------------------------------------ credentials


def env_prefix(decl: AdapterDecl, default: str) -> str:
    prefix = str(decl.settings.get("credentials") or default).strip().upper()
    if not _PREFIX.fullmatch(prefix):
        raise ConnectorError(
            f"{decl.name}: credentials must name an environment prefix such as ACME_GOOGLE, not {prefix!r}"
        )
    return prefix


def env_names(decl: AdapterDecl, default: str, names: tuple[str, ...]) -> dict[str, str]:
    prefix = env_prefix(decl, default)
    return {name: f"{prefix}_{name}" for name in names}


def missing(decl: AdapterDecl, default: str, names: tuple[str, ...]) -> list[str]:
    return [env for env in env_names(decl, default, names).values() if not os.environ.get(env)]


def secrets(decl: AdapterDecl, default: str, names: tuple[str, ...], *, vendor: str) -> dict[str, str]:
    wanted = env_names(decl, default, names)
    absent = [env for env in wanted.values() if not os.environ.get(env)]
    if absent:
        raise ConnectorError(
            f"{decl.name} is connected to {vendor} but is not authorised: "
            f"{', '.join(absent)} {'is' if len(absent) == 1 else 'are'} not set. "
            "Nothing has been sent, booked or changed."
        )
    return {name: os.environ[env] for name, env in wanted.items()}


class TokenCache:
    """Access tokens, per credential set, renewed a minute before they expire.

    A token lasts about an hour and fetching one is a round trip, so fetching
    one per call would make every scheduling request several times slower.
    """

    def __init__(self) -> None:
        self._tokens: dict[tuple[str, ...], tuple[str, datetime]] = {}

    def get(self, key: tuple[str, ...], fetch: Callable[[], tuple[str, int]]) -> str:
        now = datetime.now(UTC)
        held = self._tokens.get(key)
        if held and now < held[1]:
            return held[0]
        token, lifetime = fetch()
        self._tokens[key] = (token, now + timedelta(seconds=max(60, lifetime) - 60))
        return token

    def clear(self) -> None:
        self._tokens.clear()


# ------------------------------------------------------------------ shared behaviour


def require_https(decl: AdapterDecl, url: str, what: str) -> str:
    if not url.lower().startswith("https://"):
        raise ConnectorError(f"{decl.name}: {what} must be an https:// address, so nothing travels in the clear")
    return url.rstrip("/")


def build_email(
    *, to: str, sender: str, subject: str, body: str, reply_to: str | None = None, ics: str | None = None
) -> EmailMessage:
    """A plain message, with the calendar invitation attached when there is one.

    `method=REQUEST` is what makes a mail client offer "add to calendar" rather
    than treating the attachment as an unremarkable file.
    """
    message = EmailMessage()
    message["To"] = to
    message["From"] = sender
    message["Subject"] = subject
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(body)
    if ics:
        message.add_attachment(
            ics.encode("utf-8"), maintype="text", subtype="calendar",
            filename="invite.ics", params={"method": "REQUEST", "name": "invite.ics"},
        )
    return message


def log_message(adapter: BaseAdapter, args: dict[str, Any], body: str, *, prefix: str = "msg") -> str:
    """Every sent message is logged, whichever product carried it, so "what did we
    actually tell this person" always has an answer."""
    record_id = f"{prefix}_" + uuid.uuid4().hex[:12]
    db.insert(adapter.conn, "message_log", {
        "id": record_id, "tenant_id": adapter.cfg.tenant_id, "work_item_id": args["work_item_id"],
        "direction": "outbound", "channel": adapter.name, "body": body,
        "idempotency_key": args.get("idempotency_key"), "suppressed": 0, "at": db.now(),
    })
    return record_id


def email_of(adapter: BaseAdapter, person_id: str) -> str | None:
    person = next((p for p in adapter.cfg.people if p.id == person_id), None)
    return person.email if person else None


def clock(day: str, stamp: str | None, fallback: str) -> str:
    """The local HH:MM of a vendor timestamp, clipped to the day being asked about.

    Vendors answer in the zone the query named, so the wall clock in the reply
    is already the company's own. A busy block that began yesterday begins, for
    today's purposes, at the fallback: the start of the working day for a start
    time, its end for an end time.
    """
    if not stamp or not re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", stamp) or stamp[:10] != day:
        return fallback
    return stamp[11:16]


def without_rescheduled(adapter: BaseAdapter, day: str, windows: list[dict[str, str]], excluded: str | None):
    """An interview being moved must not compete with itself for its old slot."""
    if not excluded:
        return windows
    booked = interviews.by_id(adapter.conn, adapter.cfg.tenant_id, excluded)
    if not booked or booked["on_date"] != day:
        return windows
    return [w for w in windows
            if not (w["busy_from"] == booked["start_time"] and w["busy_to"] == booked["end_time"])]


def record_interview(
    adapter: BaseAdapter, args: dict[str, Any], *, interview_id: str, calendar_ref: str | None,
    conference_url: str | None = None, location: str | None = None,
) -> None:
    attendees = args.get("attendees") or []
    candidate = next((a for a in attendees if a.get("part") == "candidate"), {})
    interviews.record(
        adapter.conn, tenant_id=adapter.cfg.tenant_id, interview_id=interview_id,
        work_item_id=args.get("work_item_id"), case_id=args.get("case_id"),
        stage=args.get("stage"), stage_label=args.get("stage_label"), role_title=args.get("role_title"),
        candidate_name=candidate.get("name"), candidate_email=candidate.get("email"),
        on_date=args["date"], start_time=args["start"], end_time=args["end"],
        minutes=int(args.get("minutes", 60)),
        timezone=args.get("timezone") or adapter.cfg.locale.timezone,
        location=location or args.get("location"), calendar_ref=calendar_ref,
        conference_url=conference_url, supersedes=args.get("supersedes"),
        created_by=args.get("actor"), attendees=attendees,
    )


def invite_preview(adapter: BaseAdapter, args: dict[str, Any], *, product: str, extra: list[str]) -> str:
    """What an approver is shown for an interview, in any vendor's calendar.

    The candidate's address is written out in full and labelled as coming from
    the message: it is the one address that did not come from the directory.
    """
    attendees = args.get("attendees") or []
    candidate = next((a for a in attendees if a.get("part") == "candidate"), None)
    panel = [a for a in attendees if a.get("part") != "candidate"]
    lines = [
        f"Create a {args.get('minutes', 60)}-minute {args.get('stage_label') or 'interview'} in {product} "
        f"on {args['date']} at {args['start']} ({args.get('timezone') or adapter.cfg.locale.timezone}).",
        *extra,
    ]
    if args.get("role_title"):
        lines.append(f"  For: {args['role_title']}")
    if args.get("location"):
        lines.append(f"  Where: {args['location']}")
    lines.append("  Interviewers: " + (", ".join(
        f"{a.get('name')} <{a.get('email')}>" if a.get("email") else str(a.get("name")) for a in panel) or "nobody"))
    lines.append(
        f"  Candidate: {candidate.get('name') or 'not named'} <{candidate['email']}>"
        "  — taken from the message, not from the directory"
        if candidate and candidate.get("email")
        else "  Candidate: not identified, so nobody outside the company is invited"
    )
    if args.get("supersedes"):
        lines.append(f"  Replaces: interview {args['supersedes']}")
    return "\n".join(lines)


def dotted_get(data: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(data, dict):
            data = data.get(part)
        elif isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        else:
            return None
    return data


def dotted_set(data: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    for part in parts[:-1]:
        data = data.setdefault(part, {})
    data[parts[-1]] = value
