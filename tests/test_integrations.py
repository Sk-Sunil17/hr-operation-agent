"""Different companies, different products: the kernel must not care which.

Each connector is driven through a fake transport, so what is asserted is the
request each vendor would receive -- the part that fails in a way no mock can
catch -- and that swapping the product changes nothing above the adapter.
"""

from __future__ import annotations

import base64
import warnings
from email import message_from_bytes

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hr_agent import approvals, gworkspace, interviews, kernel, microsoft365, readiness, standard, tools, web
from hr_agent.connect import Response
from hr_agent.packs.models import Adapter
from hr_agent.packs.validate import validate

from .conftest_agent import TODAY, build

warnings.filterwarnings("ignore", category=DeprecationWarning)

INTERVIEW = ("Arrange a 60 minute panel interview with Nils Berger (nils.berger@example.test) "
             "for the Head of Finance role with Priya on 2026-11-19.")


def world_with(tenant="acme_labs", **swaps):
    """A company whose adapters are re-pointed at other products, by adapter name."""
    conn, cfg = build(tenant)
    cfg = cfg.model_copy(deep=True)
    for adapter in cfg.adapters:
        if adapter.name in swaps:
            adapter.implementation, adapter.settings = swaps[adapter.name]
    return conn, cfg


def run(world, message, requester="emp_021"):
    conn, cfg = world
    outcome = kernel.run(conn, cfg, message=message, requester_id=requester, on=TODAY)
    assert outcome.verify_passed, [c for c in outcome.checks if c["status"] == "fail"]
    return outcome


def approve(world, outcome, actor="emp_020"):
    conn, cfg = world
    return approvals.decide(conn, cfg, approval_id=outcome.approval_id, actor_id=actor, outcome="approved", on=TODAY)


# ---------------------------------------------------------------- Microsoft 365


@pytest.fixture
def microsoft(monkeypatch):
    for name, value in (("TENANT_ID", "contoso-tenant"), ("CLIENT_ID", "app"), ("CLIENT_SECRET", "s3cret")):
        monkeypatch.setenv(f"HR_MS_{name}", value)
    microsoft365.TOKENS.clear()
    calls: list[tuple[str, str, dict | None, dict | None]] = []

    def transport(method, url, *, headers=None, json_body=None, form=None):
        calls.append((method, url, json_body or form, headers))
        if "login.microsoftonline.com" in url:
            return Response(200, {"access_token": "ms-token", "expires_in": 3600})
        if url.endswith("/getSchedule"):
            day = json_body["startTime"]["dateTime"][:10]
            return Response(200, {"value": [
                {"scheduleId": email, "scheduleItems": [
                    {"status": "busy", "start": {"dateTime": f"{day}T09:00:00.0000000"},
                     "end": {"dateTime": f"{day}T10:00:00.0000000"}},
                    {"status": "free", "start": {"dateTime": f"{day}T13:00:00"}, "end": {"dateTime": f"{day}T14:00:00"}},
                ]} for email in json_body["schedules"]
            ]})
        if url.endswith("/events") and method == "POST":
            return Response(201, {"id": "outlook_evt_1", "webLink": "https://outlook.office.com/evt/1",
                                  "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/1"}})
        return Response(202, {})

    monkeypatch.setattr(microsoft365, "TRANSPORT", transport)
    world = world_with(
        calendars=("microsoft_calendar", {"organizer": "recruiting@acme.test", "teams_meeting": True,
                                          "buffer_minutes": 15}),
        email=("outlook_mail", {"from_address": "people@acme.test"}),
    )
    yield world, calls
    microsoft365.TOKENS.clear()


def test_outlook_books_the_same_interview_google_would(microsoft):
    world, calls = microsoft
    outcome = run(world, INTERVIEW)
    assert (outcome.rule_id, outcome.mode) == ("I1", "prepare")
    # Busy 09:00-10:00 plus a 15 minute buffer: same answer as with Google.
    assert "at 10:15" in outcome.draft
    assert not any(url.endswith("/events") for _, url, _, _ in calls), "nothing is booked before approval"

    approve(world, outcome)
    conn, cfg = world
    row = interviews.scheduled(conn, cfg)[0]
    assert row["calendar_ref"] == "outlook_evt_1"
    assert row["conference_url"].startswith("https://teams.microsoft.com/")


def test_the_requests_microsoft_receives_are_correct(microsoft):
    world, calls = microsoft
    approve(world, run(world, INTERVIEW))
    _, cfg = world

    token = next(body for _, url, body, _ in calls if "login.microsoftonline.com" in url)
    assert token["grant_type"] == "client_credentials"
    assert token["scope"] == "https://graph.microsoft.com/.default"
    assert any("/contoso-tenant/oauth2/v2.0/token" in url for _, url, _, _ in calls)

    _, url, schedule, headers = next(c for c in calls if c[1].endswith("/getSchedule"))
    assert "/users/recruiting@acme.test/calendar/getSchedule" in url
    assert headers["Prefer"] == f'outlook.timezone="{cfg.locale.timezone}"'
    assert headers["Authorization"] == "Bearer ms-token"

    event = next(body for method, url, body, _ in calls if url.endswith("/events") and method == "POST")
    assert event["start"] == {"dateTime": "2026-11-19T10:15:00", "timeZone": cfg.locale.timezone}
    assert event["sensitivity"] == "private" and event["hideAttendees"] is True
    assert event["onlineMeetingProvider"] == "teamsForBusiness"
    assert event["transactionId"].startswith("int_"), "Graph's idempotency key stops a retry booking twice"
    assert "nils.berger@example.test" in {a["emailAddress"]["address"] for a in event["attendees"]}

    mail = next(body for _, url, body, _ in calls if url.endswith("/sendMail"))
    assert "/users/people@acme.test/sendMail" in next(u for _, u, _, _ in calls if u.endswith("/sendMail"))
    assert mail["message"]["toRecipients"] == [{"emailAddress": {"address": "nils.berger@example.test"}}]
    attachment = mail["message"]["attachments"][0]
    assert attachment["contentType"] == "text/calendar; method=REQUEST"
    assert b"BEGIN:VCALENDAR" in base64.b64decode(attachment["contentBytes"])


def test_cancelling_cancels_in_outlook_too(microsoft):
    world, calls = microsoft
    approve(world, run(world, "Arrange a 30 minute screening call with Nils Berger (nils@example.test) "
                              "for the Head of Finance role on 2026-11-19."))
    conn, cfg = world
    booked = interviews.scheduled(conn, cfg)[0]
    approve(world, run(world, "Please cancel the screening call with Nils Berger."))
    assert any(url.endswith("/events/outlook_evt_1/cancel") for _, url, _, _ in calls)
    assert interviews.by_id(conn, cfg.tenant_id, booked["id"])["status"] == "cancelled"


def test_without_microsoft_credentials_nothing_is_booked_and_setup_says_what_is_missing(monkeypatch):
    for name in ("TENANT_ID", "CLIENT_ID", "CLIENT_SECRET"):
        monkeypatch.delenv(f"HR_MS_{name}", raising=False)
    microsoft365.TOKENS.clear()
    conn, cfg = world_with(calendars=("microsoft_calendar", {"organizer": "recruiting@acme.test"}))
    outcome = kernel.run(conn, cfg, message=INTERVIEW, requester_id="emp_021", on=TODAY)
    assert not [p for p in outcome.plans if p["operation"] == "commit_invite"]
    assert interviews.scheduled(conn, cfg) == []

    calendar = next(a for a in cfg.adapters if a.name == "calendars")
    state = microsoft365.OutlookCalendar.connection(calendar)
    assert state["state"] == "needs_setup"
    assert "HR_MS_CLIENT_SECRET" in state["detail"]


# ---------------------------------------------------------------- per-company credentials


def test_each_company_can_use_its_own_google_account(monkeypatch):
    monkeypatch.delenv("HR_GOOGLE_CLIENT_ID", raising=False)
    for name, value in (("CLIENT_ID", "acme-app"), ("CLIENT_SECRET", "x"), ("REFRESH_TOKEN", "acme-refresh")):
        monkeypatch.setenv(f"ACME_GOOGLE_{name}", value)
    gworkspace.forget_credentials()
    seen = []

    def transport(method, url, *, headers=None, json_body=None, form=None):
        if "oauth2" in url:
            seen.append(form["refresh_token"])
            return Response(200, {"access_token": "t", "expires_in": 3600})
        return Response(200, {"calendars": {}})

    monkeypatch.setattr(gworkspace, "TRANSPORT", transport)
    world = world_with(calendars=("google_calendar", {"credentials": "ACME_GOOGLE"}))
    run(world, INTERVIEW)
    assert seen == ["acme-refresh"]
    gworkspace.forget_credentials()


# ---------------------------------------------------------------- any mail provider


class FakeSmtp:
    sent: list = []
    logins: list = []

    def __init__(self, host, port, security):
        self.where = (host, port, security)

    def login(self, user, password):
        FakeSmtp.logins.append((user, password))

    def send_message(self, message):
        FakeSmtp.sent.append(message)
        return {}

    def quit(self):
        pass


def test_any_mail_provider_can_send_the_candidate_their_invitation(monkeypatch):
    monkeypatch.setenv("ACME_SMTP_USERNAME", "people@acme.test")
    monkeypatch.setenv("ACME_SMTP_PASSWORD", "app-password")
    FakeSmtp.sent, FakeSmtp.logins = [], []
    monkeypatch.setattr(standard, "SMTP_FACTORY", FakeSmtp)
    world = world_with(email=("smtp_mail", {"host": "smtp.zoho.test", "from_address": "people@acme.test",
                                            "credentials": "ACME_SMTP"}))
    approve(world, run(world, INTERVIEW))

    assert FakeSmtp.logins == [("people@acme.test", "app-password")]
    message = message_from_bytes(FakeSmtp.sent[0].as_bytes())
    assert message["To"] == "nils.berger@example.test"
    assert message["From"] == "people@acme.test"
    assert any(part.get_content_type() == "text/calendar" for part in message.walk())


def test_unencrypted_mail_is_refused_unless_the_relay_is_on_this_machine():
    remote = Adapter(name="email", kind="notify", implementation="smtp_mail", operations=["prepare_message"],
                     settings={"host": "smtp.example.test", "from_address": "a@b.test", "security": "none"})
    assert any("unencrypted" in p for p in standard.SmtpMail.check_settings(remote))
    local = remote.model_copy(update={"settings": {**remote.settings, "host": "localhost"}})
    assert standard.SmtpMail.check_settings(local) == []


# ---------------------------------------------------------------- chat channels


def test_staff_messages_can_go_to_a_teams_channel(monkeypatch):
    monkeypatch.setenv("ACME_TEAMS_URL", "https://acme.webhook.office.test/hook/1")
    posted = []

    def transport(method, url, *, headers=None, json_body=None, form=None):
        posted.append((url, json_body))
        return Response(200, {})

    monkeypatch.setattr(standard, "TRANSPORT", transport)
    world = world_with(slack=("webhook", {"format": "teams", "channel_label": "HR Help",
                                          "credentials": "ACME_TEAMS"}))
    outcome = run(world, "How many days of annual leave do I get in a year?", requester="emp_001")
    assert outcome.mode == "auto"
    url, card = posted[0]
    assert url == "https://acme.webhook.office.test/hook/1"
    assert card["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert "25 days" in card["attachments"][0]["content"]["body"][0]["text"]


def test_a_chat_channel_cannot_be_used_to_reach_a_candidate():
    assert standard.Webhook.REACHES_EXTERNAL is False


# ---------------------------------------------------------------- any HR system


HRIS_SETTINGS = {
    "vendor": "BambooHR", "base_url": "https://api.hr.test/v1", "auth": "basic", "credentials": "ACME_BAMBOO",
    "read_path": "/employees/{person}", "write_method": "POST",
    "fields": {"preferred_name": "preferredName", "personal_email": "homeEmail", "phone": "mobilePhone",
               "home_address": "address.line1", "emergency_contact": "emergency.name",
               "bank_details": "payroll.bankAccount"},
}


@pytest.fixture
def hris(monkeypatch):
    monkeypatch.setenv("ACME_BAMBOO_USERNAME", "api-key")
    monkeypatch.setenv("ACME_BAMBOO_PASSWORD", "x")
    calls = []
    record = {"homeEmail": "lena@home.test", "address": {"line1": "1 Old Street"},
              "payroll": {"bankAccount": "on file"}, "emergency": {"name": ""}}

    def transport(method, url, *, headers=None, json_body=None, form=None):
        calls.append((method, url, json_body, headers))
        return Response(200, record if method == "GET" else {"ok": True})

    monkeypatch.setattr(standard, "TRANSPORT", transport)
    return world_with(people_system=("rest_hris", HRIS_SETTINGS)), calls


def test_onboarding_readiness_reads_the_companys_own_hr_system(hris):
    (conn, cfg), calls = hris
    report = readiness.check(conn, cfg, person_id="emp_005", registry=tools.Registry(conn, cfg))
    found = {f.item.id: f.present for f in report.findings}
    assert found["bank_details"] is True and found["personal_email"] is True
    assert found["emergency_contact"] is False, "an empty value in the vendor's system is not a value"
    method, url, _, headers = calls[0]
    assert (method, url) == ("GET", "https://api.hr.test/v1/employees/emp_005")
    assert headers["Authorization"] == "Basic " + base64.b64encode(b"api-key:x").decode()


def test_an_approved_record_change_is_written_in_the_vendors_own_field_names(hris):
    world, calls = hris
    outcome = run(world, "I have moved. Please update my home address to 12 Oakfield Road, Bristol BS6 7JQ.",
                  requester="emp_001")
    assert outcome.mode == "prepare"
    assert not [c for c in calls if c[0] != "GET"], "nothing is written before approval"
    change = next(p for p in outcome.plans if p["operation"] == "commit_record_change")
    assert "BambooHR" in change["preview_text"]
    assert "1 Old Street" in change["preview_text"], "the approver sees the current value"

    approve(world, outcome, actor="emp_021")
    method, url, body, _ = next(c for c in calls if c[0] != "GET")
    assert (method, url) == ("POST", "https://api.hr.test/v1/employees/emp_001")
    assert body == {"address": {"line1": "12 Oakfield Road, Bristol BS6 7JQ"}}


def test_an_hr_system_mapping_must_cover_every_field_it_claims_to_own():
    decl = Adapter(name="hr", kind="hris", implementation="rest_hris", owns_fields=["phone", "home_address"],
                   settings={"base_url": "https://x.test", "read_path": "/e/{person}", "fields": {"phone": "mobile"}})
    assert any("home_address" in p for p in standard.RestHris.check_settings(decl))
    insecure = decl.model_copy(update={"settings": {**decl.settings, "base_url": "http://x.test"}})
    assert any("https" in p for p in standard.RestHris.check_settings(insecure))


def test_a_filed_document_reaches_the_document_system_and_counts_for_onboarding(monkeypatch):
    monkeypatch.setenv("ACME_FLOW_TOKEN", "flow-key")
    posted = []

    def transport(method, url, *, headers=None, json_body=None, form=None):
        posted.append((url, json_body, headers))
        return Response(200, {"id": "sharepoint-item-42"})

    monkeypatch.setattr(standard, "TRANSPORT", transport)
    conn, cfg = world_with(documents=("rest_docstore", {
        "vendor": "SharePoint via Power Automate", "base_url": "https://flow.acme.test", "file_path": "/hr-files",
        "auth": "header", "header_name": "X-Flow-Key", "credentials": "ACME_FLOW",
    }))
    registry = tools.Registry(conn, cfg)
    receipt = registry.get("documents").act("commit_file", person_id="emp_005", document_type="right_to_work",
                                            idempotency_key="idem_test", actor="emp_021")
    assert receipt["reference"] == "sharepoint-item-42"
    url, body, headers = posted[0]
    assert url == "https://flow.acme.test/hr-files" and headers["X-Flow-Key"] == "flow-key"
    assert body["document_type"] == "right_to_work" and body["person_name"] == "Lena Fischer"
    found = {f.item.id: f.present for f in readiness.check(conn, cfg, person_id="emp_005", registry=registry).findings}
    assert found["right_to_work"] is True


# ---------------------------------------------------------------- configuration guard rails


def test_a_pack_may_not_hold_a_secret():
    with pytest.raises(ValidationError, match="look like credentials"):
        Adapter(name="email", kind="notify", implementation="smtp_mail",
                settings={"host": "smtp.test", "password": "hunter2"})
    # Naming where the secret lives is fine.
    Adapter(name="email", kind="notify", implementation="smtp_mail", settings={"credentials": "ACME_SMTP"})


def test_validation_catches_a_wrong_or_unknown_connector():
    _, cfg = world_with(calendars=("gmail", {}), slack=("pigeon_post", {}))
    messages = [p.message for p in validate(cfg) if p.severity == "error"]
    assert any("gmail is a notify connector, not a calendar one" in m for m in messages)
    assert any("no connector is called 'pigeon_post'" in m for m in messages)


def test_validation_catches_missing_settings():
    _, cfg = world_with(calendars=("microsoft_calendar", {}))
    assert any("organizer" in p.message for p in validate(cfg) if p.severity == "error")


def test_the_dashboard_shows_what_is_connected_and_never_a_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("HR_SMTP_PASSWORD", "do-not-show-me")
    path = str(tmp_path / "hr.db")
    build("acme_labs", path)[0].close()
    body = TestClient(web.create_app(db_path=path)).get("/api/integrations?tenant=acme_labs")
    assert body.status_code == 200
    data = body.json()
    assert {i["state"] for i in data["items"]} == {"demo"}
    offered = {c["implementation"] for c in data["available"]}
    assert {"google_calendar", "microsoft_calendar", "gmail", "outlook_mail", "smtp_mail", "webhook",
            "rest_hris", "rest_docstore"} <= offered
    assert "do-not-show-me" not in body.text
