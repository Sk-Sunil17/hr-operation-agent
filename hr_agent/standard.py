"""Connectors built on open standards, for whatever products a company uses.

Not every company is on Google or Microsoft, and no list of named vendors is
ever complete. These connect to anything that speaks a standard:

  smtp_mail       Email through any provider: Zoho, Fastmail, Amazon SES,
                  Mailgun, SendGrid, an on-premises Exchange relay, or Google
                  and Microsoft themselves.
  webhook         Staff notifications into a Slack, Microsoft Teams, Google Chat,
                  Mattermost or Discord channel, through its incoming webhook.
  rest_hris       Staff records in any HR system with a REST API (BambooHR,
                  HiBob, Personio, Workday, Rippling, an in-house system), by
                  mapping this company's field names onto the vendor's.
  rest_docstore   Files a received document with any document system, directly
                  or through an automation service such as Power Automate,
                  Zapier or Make that forwards to SharePoint, Google Drive or Box.

Example, for a company on BambooHR with a Zoho mailbox and Slack:

    adapters:
      - name: people_system
        kind: hris
        implementation: rest_hris
        owns_fields: [phone, home_address, personal_email]
        operations: [read_person, read_manager, read_record, prepare_record_change, commit_record_change]
        settings:
          vendor: BambooHR
          base_url: https://api.bamboohr.com/api/gateway.php/acme/v1
          auth: basic                       # BambooHR: API key as user name, any password
          credentials: ACME_BAMBOO          # ACME_BAMBOO_USERNAME, ACME_BAMBOO_PASSWORD
          read_path: /employees/{person}?fields=mobilePhone,address1,homeEmail
          write_path: /employees/{person}
          write_method: POST
          fields: { phone: mobilePhone, home_address: address1, personal_email: homeEmail }

      - name: email
        kind: notify
        implementation: smtp_mail
        operations: [prepare_message, commit_message]
        settings: { host: smtp.zoho.eu, port: 587, from_address: people@acme.com, credentials: ACME_SMTP }

      - name: slack
        kind: notify
        implementation: webhook
        operations: [prepare_message, commit_message]
        settings: { format: slack, channel_label: "#people-help", credentials: ACME_SLACK }  # ACME_SLACK_URL

Secrets come from the environment, never from the pack. Every address must be
https (or, for mail, encrypted) unless it is on this machine.
"""

from __future__ import annotations

import base64
import smtplib
import ssl
import urllib.parse
from typing import Any

from . import connect, db
from .connect import Response
from .tools import BaseAdapter, MockHris, Prepared, ToolError

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def http(method: str, url: str, *, headers=None, json_body=None, form=None) -> Response:  # noqa: ANN001
    return connect.http(method, url, headers=headers, json_body=json_body, form=form, vendor="The system")


# Swapped by tests.
TRANSPORT = http
SMTP_FACTORY = None


# ------------------------------------------------------------------ email over SMTP


class SmtpMail(BaseAdapter):
    """Email to anyone, through any mail provider."""

    kind = "notify"
    VENDOR = "Any email provider"
    PRODUCT = "Any email provider (SMTP)"
    REQUIRED_SETTINGS = ("host", "from_address")
    SECRETS = ("USERNAME", "PASSWORD")
    DEFAULT_CREDENTIALS = "HR_SMTP"
    READS = ()
    WRITES = ("commit_message",)
    REACHES_EXTERNAL = True

    @classmethod
    def secret_names(cls, decl) -> tuple[str, ...]:  # noqa: ANN001
        return () if decl.settings.get("auth") == "none" else cls.SECRETS

    @classmethod
    def check_settings(cls, decl) -> list[str]:  # noqa: ANN001
        problems = super().check_settings(decl)
        security = decl.settings.get("security", "starttls")
        if security not in {"starttls", "ssl", "none"}:
            problems.append("security must be starttls, ssl or none")
        if security == "none" and str(decl.settings.get("host", "")).lower() not in LOCAL_HOSTS:
            problems.append("security: none is only allowed for a relay on this machine; mail would travel unencrypted")
        return problems

    def _open(self):  # noqa: ANN202
        problems = self.check_settings(self.decl)
        if problems:
            raise ToolError(f"{self.name}: {'; '.join(problems)}")
        host = str(self.decl.settings["host"])
        security = self.decl.settings.get("security", "starttls")
        port = int(self.decl.settings.get("port") or (465 if security == "ssl" else 587))
        login = connect.secrets(self.decl, self.DEFAULT_CREDENTIALS, self.secret_names(self.decl),
                                vendor=f"the mail server {host}")
        try:
            if SMTP_FACTORY is not None:
                server = SMTP_FACTORY(host, port, security)
            elif security == "ssl":
                server = smtplib.SMTP_SSL(host, port, timeout=connect.TIMEOUT_SECONDS,
                                          context=ssl.create_default_context())
            else:
                server = smtplib.SMTP(host, port, timeout=connect.TIMEOUT_SECONDS)
                if security == "starttls":
                    server.starttls(context=ssl.create_default_context())
            if login:
                server.login(login["USERNAME"], login["PASSWORD"])
        except (smtplib.SMTPException, OSError) as exc:
            raise ToolError(f"The mail server {host} refused or did not respond: {exc}") from exc
        return server

    def check_live(self) -> str | None:
        self._open().quit()
        return f"{self.decl.settings['host']} accepted the login."

    def preview(self, operation: str, **args: Any) -> Prepared:
        if operation != "prepare_message":
            return super().preview(operation, **args)
        to = args.get("to")
        if not to:
            raise ToolError(f"{self.name} needs an address to send to, and none was given")
        attached = "\n  With a calendar invitation attached (invite.ics)" if args.get("ics") else ""
        return Prepared(
            preview=(
                f"Email from {self.decl.settings.get('from_address')} to {to} via {self.decl.settings.get('host')}"
                f"{attached}\n  Subject: {args.get('subject') or '(no subject)'}\n\n{args['body']}"
            ),
            args={"to": to, "subject": args.get("subject"), "body": args["body"],
                  "ics": args.get("ics"), "work_item_id": args["work_item_id"]},
        )

    def act(self, operation: str, **args: Any) -> dict[str, Any]:
        if operation != "commit_message":
            return super().act(operation, **args)
        subject = args.get("subject") or "(no subject)"
        reply_to = self.decl.settings.get("reply_to")
        message = connect.build_email(
            to=args["to"], sender=str(self.decl.settings["from_address"]), subject=subject,
            body=args["body"], reply_to=str(reply_to) if reply_to else None, ics=args.get("ics"),
        )
        server = self._open()
        try:
            refused = server.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            raise ToolError(f"The mail server would not accept the message: {exc}") from exc
        finally:
            try:
                server.quit()
            except (smtplib.SMTPException, OSError):
                pass
        if refused:
            raise ToolError(f"The mail server refused {', '.join(refused)}")
        record_id = connect.log_message(
            self, args, f"To: {args['to']}\nSubject: {subject}\n\n{args['body']}", prefix="mail")
        return {"message_id": record_id, "to": args["to"], "subject": subject,
                "carried_invitation": bool(args.get("ics"))}


# ------------------------------------------------------------------ chat webhooks


def _chat_body(fmt: str, text: str) -> dict[str, Any]:
    if fmt == "teams":
        return {"type": "message", "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard", "version": "1.4",
                "body": [{"type": "TextBlock", "text": text, "wrap": True}],
            },
        }]}
    if fmt == "discord":
        return {"content": text[:2000]}
    return {"text": text}


class Webhook(BaseAdapter):
    """A message into one staff channel. It cannot reach anyone outside the company."""

    kind = "notify"
    VENDOR = "Any chat tool"
    PRODUCT = "Chat channel (Slack, Teams, Google Chat, Mattermost, Discord)"
    SECRETS = ("URL",)
    DEFAULT_CREDENTIALS = "HR_WEBHOOK"
    FORMATS = {"slack", "teams", "google_chat", "mattermost", "discord", "text"}
    READS = ()
    WRITES = ("commit_message",)

    @classmethod
    def check_settings(cls, decl) -> list[str]:  # noqa: ANN001
        fmt = decl.settings.get("format", "text")
        return [] if fmt in cls.FORMATS else [f"format must be one of {', '.join(sorted(cls.FORMATS))}"]

    @property
    def label(self) -> str:
        return str(self.decl.settings.get("channel_label") or self.name)

    def preview(self, operation: str, **args: Any) -> Prepared:
        if operation != "prepare_message":
            return super().preview(operation, **args)
        fmt = self.decl.settings.get("format", "text")
        return Prepared(
            preview=f"Post to {self.label} ({fmt.replace('_', ' ')}) via {self.name}:\n\n{args['body']}",
            args={"to": self.label, "body": args["body"], "work_item_id": args["work_item_id"]},
        )

    def act(self, operation: str, **args: Any) -> dict[str, Any]:
        if operation != "commit_message":
            return super().act(operation, **args)
        url = connect.secrets(self.decl, self.DEFAULT_CREDENTIALS, self.SECRETS, vendor="a chat channel")["URL"]
        connect.require_https(self.decl, url, "the webhook address")
        reply = TRANSPORT("POST", url, json_body=_chat_body(self.decl.settings.get("format", "text"), args["body"]))
        if reply.status >= 400:
            raise ToolError(f"{self.label} refused the message ({reply.status}): {connect.error_detail(reply.body)}")
        record_id = connect.log_message(self, args, args["body"])
        return {"message_id": record_id, "to": self.label}


# ------------------------------------------------------------------ REST systems


class _Rest:
    """Authentication and addressing shared by the REST connectors."""

    AUTHS = {"bearer", "basic", "header"}
    DEFAULT_CREDENTIALS = "HR_REST"

    @classmethod
    def secret_names(cls, decl) -> tuple[str, ...]:  # noqa: ANN001
        return ("USERNAME", "PASSWORD") if decl.settings.get("auth") == "basic" else ("TOKEN",)

    @classmethod
    def rest_problems(cls, decl) -> list[str]:  # noqa: ANN001
        problems = []
        base = str(decl.settings.get("base_url") or "")
        if not base:
            problems.append("needs the setting 'base_url'")
        elif not base.lower().startswith("https://"):
            problems.append("base_url must be an https:// address")
        auth = decl.settings.get("auth", "bearer")
        if auth not in cls.AUTHS:
            problems.append(f"auth must be one of {', '.join(sorted(cls.AUTHS))}")
        if auth == "header" and not decl.settings.get("header_name"):
            problems.append("auth: header needs header_name, e.g. X-API-Key")
        if decl.settings.get("person_key", "id") not in {"id", "email"}:
            problems.append("person_key must be id or email")
        return problems

    def _headers(self) -> dict[str, str]:
        decl = self.decl  # type: ignore[attr-defined]
        vendor = str(decl.settings.get("vendor") or decl.name)
        found = connect.secrets(decl, self.DEFAULT_CREDENTIALS, self.secret_names(decl), vendor=vendor)
        auth = decl.settings.get("auth", "bearer")
        if auth == "basic":
            pair = f"{found['USERNAME']}:{found['PASSWORD']}".encode()
            value = {"Authorization": "Basic " + base64.b64encode(pair).decode("ascii")}
        elif auth == "header":
            value = {str(decl.settings["header_name"]): found["TOKEN"]}
        else:
            value = {"Authorization": f"Bearer {found['TOKEN']}"}
        return {"Accept": "application/json", **value}

    def _url(self, path_setting: str, person_id: str, default: str | None = None) -> str:
        decl = self.decl  # type: ignore[attr-defined]
        problems = self.rest_problems(decl)
        if problems:
            raise ToolError(f"{decl.name}: {'; '.join(problems)}")
        template = str(decl.settings.get(path_setting) or default or "")
        if decl.settings.get("person_key", "id") == "email":
            key = connect.email_of(self, person_id)  # type: ignore[arg-type]
            if not key:
                raise ToolError(f"{person_id} has no email address, which is how {decl.name} identifies people")
        else:
            key = person_id
        return str(decl.settings["base_url"]).rstrip("/") + template.replace("{person}", urllib.parse.quote(key, safe=""))

    def _send(self, method: str, url: str, body: Any = None) -> dict[str, Any]:
        decl = self.decl  # type: ignore[attr-defined]
        reply = TRANSPORT(method, url, headers=self._headers(), json_body=body)
        if reply.status >= 400:
            vendor = decl.settings.get("vendor") or decl.name
            raise ToolError(f"{vendor} refused the request ({reply.status}): {connect.error_detail(reply.body)}")
        return reply.body


class RestHris(_Rest, MockHris):
    """Staff records in any HR system with a REST API.

    The directory (who reports to whom) still comes from the company's people
    list; this connector reads and writes the fields it owns in the vendor's
    system, so onboarding readiness and record changes use the real values.
    """

    VENDOR = "Any HR system"
    PRODUCT = "Any HR system with an API (BambooHR, HiBob, Personio, Workday…)"
    REQUIRED_SETTINGS = ("base_url", "read_path", "fields")
    SECRETS = ("TOKEN",)
    METHODS = {"PATCH", "PUT", "POST"}

    @classmethod
    def check_settings(cls, decl) -> list[str]:  # noqa: ANN001
        problems = cls.rest_problems(decl)
        if not decl.settings.get("read_path"):
            problems.append("needs the setting 'read_path', e.g. /employees/{person}")
        fields = decl.settings.get("fields") or {}
        unmapped = [f for f in decl.owns_fields if f not in fields]
        if unmapped:
            problems.append(f"fields has no mapping for {', '.join(unmapped)}")
        if str(decl.settings.get("write_method", "PATCH")).upper() not in cls.METHODS:
            problems.append("write_method must be PATCH, PUT or POST")
        return problems

    @property
    def vendor(self) -> str:
        return str(self.decl.settings.get("vendor") or "the HR system")

    def _overrides(self, person_id: str) -> dict[str, Any]:
        data = self._send("GET", self._url("read_path", person_id))
        mapping: dict[str, str] = self.decl.settings.get("fields") or {}
        found = {}
        for field in self.decl.owns_fields:
            value = connect.dotted_get(data, mapping.get(field, field))
            if value not in (None, "", [], {}):
                found[field] = value if isinstance(value, str) else str(value)
        return found

    def preview(self, operation: str, **args: Any) -> Prepared:
        if operation != "prepare_record_change":
            return super().preview(operation, **args)
        person = self._person(args["person_id"])
        changes: dict[str, Any] = args["changes"]
        self._check_fields(changes)
        try:
            current = self._overrides(args["person_id"])
        except ToolError:
            current = None
        lines = [
            f"  {field}: "
            + ("(could not be read just now)" if current is None else (current.get(field) or "(not set)"))
            + f"  ->  {value}"
            for field, value in sorted(changes.items())
        ]
        return Prepared(
            preview=f"Change {person['name']}'s record in {self.vendor}:\n" + "\n".join(lines),
            args={"person_id": args["person_id"], "changes": changes},
        )

    def act(self, operation: str, **args: Any) -> dict[str, Any]:
        if operation != "commit_record_change":
            return super().act(operation, **args)
        changes: dict[str, Any] = args["changes"]
        self._check_fields(changes)
        mapping: dict[str, str] = self.decl.settings.get("fields") or {}
        body: dict[str, Any] = {}
        for field, value in changes.items():
            connect.dotted_set(body, mapping[field], value)
        if envelope := self.decl.settings.get("write_envelope"):
            body = {str(envelope): body}
        self._send(str(self.decl.settings.get("write_method", "PATCH")).upper(),
                   self._url("write_path", args["person_id"], default=self.decl.settings.get("read_path")), body)
        return {"updated": sorted(changes), "person_id": args["person_id"], "at": db.now(), "system": self.vendor}


class RestDocstore(_Rest, BaseAdapter):
    """Files a received document with any document system.

    The filing is also recorded here, because onboarding readiness asks "has
    this person's right-to-work check been filed" and must not depend on a
    second system being reachable to answer.
    """

    kind = "docstore"
    VENDOR = "Any document system"
    PRODUCT = "Any document system (SharePoint, Google Drive, Box…)"
    REQUIRED_SETTINGS = ("base_url", "file_path")
    SECRETS = ("TOKEN",)
    READS = ()
    WRITES = ("commit_file",)

    @classmethod
    def check_settings(cls, decl) -> list[str]:  # noqa: ANN001
        problems = cls.rest_problems(decl)
        if not decl.settings.get("file_path"):
            problems.append("needs the setting 'file_path', e.g. /documents")
        return problems

    @property
    def vendor(self) -> str:
        return str(self.decl.settings.get("vendor") or self.name)

    def preview(self, operation: str, **args: Any) -> Prepared:
        if operation != "prepare_file":
            return super().preview(operation, **args)
        expiry = f", expiring {args['expires_at']}" if args.get("expires_at") else ""
        name = next((p.name for p in self.cfg.people if p.id == args["person_id"]), args["person_id"])
        return Prepared(
            preview=f"File {args['document_type']} for {name} in {self.vendor}{expiry}",
            args={k: v for k, v in args.items() if k != "work_item_id"} | {"work_item_id": args["work_item_id"]},
        )

    def act(self, operation: str, **args: Any) -> dict[str, Any]:
        if operation != "commit_file":
            return super().act(operation, **args)
        person = next((p for p in self.cfg.people if p.id == args["person_id"]), None)
        sent = self._send("POST", self._url("file_path", args["person_id"]), {
            "person_id": args["person_id"], "person_email": person.email if person else None,
            "person_name": person.name if person else None, "document_type": args["document_type"],
            "expires_at": args.get("expires_at"), "template_version": args.get("template_version"),
            "idempotency_key": args.get("idempotency_key"),
        })
        reference = str(sent.get("id") or sent.get("reference") or args.get("idempotency_key"))
        self.conn.execute(
            "INSERT INTO filed_document (tenant_id, person_id, document_type, reference, filed_at, "
            "expires_at, filed_by, template_version) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(tenant_id, person_id, document_type) DO UPDATE SET "
            "reference = excluded.reference, filed_at = excluded.filed_at, "
            "expires_at = excluded.expires_at, filed_by = excluded.filed_by, "
            "template_version = excluded.template_version",
            (self.cfg.tenant_id, args["person_id"], args["document_type"], reference, db.now(),
             args.get("expires_at"), args.get("actor"), args.get("template_version")),
        )
        return {"filed": args["document_type"], "reference": reference,
                "expires_at": args.get("expires_at"), "system": self.vendor}


IMPLEMENTATIONS: dict[str, type[BaseAdapter]] = {
    "smtp_mail": SmtpMail,
    "webhook": Webhook,
    "rest_hris": RestHris,
    "rest_docstore": RestDocstore,
}
