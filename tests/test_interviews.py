"""Interview scheduling.

The thing that makes interviews different from everything else the agent does is
the outside party. A policy answer that is wrong wastes somebody's afternoon; an
interview that is wrong puts a stranger in a reception at the wrong hour, or
leaves them waiting for an invitation that was never sent. So the tests here are
weighted towards the failures that are quiet:

  * a candidate confused with a colleague who shares a first name
  * a time proposed that was already taken by the interview booked before it
  * an invitation "sent" through a system that cannot reach the recipient
  * a reschedule that leaves the original still live in four calendars
  * a reply that claims an address came from the message when it came from a record

Every one of those is a test below, because none of them announces itself.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from hr_agent import approvals, classify, db, ics, interviews, kernel, scheduler, tools
from hr_agent.packs import loader
from hr_agent.packs.models import InterviewConfig, InterviewStage

from .conftest_agent import TODAY, build


@pytest.fixture
def acme():
    return build("acme_labs")


@pytest.fixture
def clinic():
    return build("harbour_clinic")


def run(world, message, requester="emp_021", **kw):
    conn, cfg = world
    return kernel.run(conn, cfg, message=message, requester_id=requester, on=TODAY, **kw)


def passes(outcome):
    """Assert the verify gate let it through, and say what failed if not."""
    failures = [c for c in outcome.checks if c["status"] == "fail"]
    assert outcome.verify_passed, failures
    return outcome


def approve(world, outcome, actor="emp_020"):
    conn, cfg = world
    return approvals.decide(
        conn, cfg, approval_id=outcome.approval_id, actor_id=actor, outcome="approved", on=TODAY
    )


def parse(world, message, requester="emp_021"):
    """Parse as the kernel does: directory names come from the classifier."""
    conn, cfg = world
    person = next((p for p in cfg.people if p.id == requester), None)
    found = classify.classify(message, cfg, requester_id=requester)
    return interviews.parse(
        message, cfg, requester=person,
        named_ids=list(found.entities.get("people") or []), today=TODAY,
    )


# ---------------------------------------------------------------- reading the request


def test_the_kind_of_interview_comes_from_the_company_pack(acme):
    """Stage names are config. A company with its own words needs no code change."""
    assert parse(acme, "Set up a technical interview for the Head of Finance role.").stage.id == "technical"
    assert parse(acme, "Book a phone screen with Lena Olofsson.").stage.id == "screening"
    assert parse(acme, "Arrange the final interview.").stage.id == "final"


def test_the_stage_decides_the_length_unless_the_message_says_otherwise(acme):
    _, cfg = acme
    technical = cfg.interviews.stage("technical")
    assert parse(acme, "Set up a technical interview.").minutes == technical.duration_minutes
    stated = parse(acme, "Set up a 25 minute technical interview.")
    assert stated.minutes == 25
    assert any("normally" in note for note in stated.notes), stated.notes


def test_an_absurd_duration_is_not_believed(acme):
    """'a 2 minute interview' is a typo, and so is 'a 9 hour interview'."""
    _, cfg = acme
    default = cfg.interviews.default_duration_minutes
    assert parse(acme, "Arrange a 2 minute interview.").minutes == default
    assert parse(acme, "Arrange a 9 hour interview.").minutes == default


def test_a_day_is_read_only_when_the_message_names_one(acme):
    assert parse(acme, "Arrange an interview on 2026-11-19.").on == date(2026, 11, 19)
    assert parse(acme, "Arrange an interview tomorrow.").on == TODAY + timedelta(days=1)
    # TODAY is a Monday, so the next Thursday is three days later.
    assert parse(acme, "Arrange an interview on Thursday.").on == date(2026, 11, 5)
    assert parse(acme, "Arrange an interview next Thursday.").on == date(2026, 11, 12)
    assert parse(acme, "Arrange an interview on 19 November.").on == date(2026, 11, 19)
    # "soon" is not a date, and treating it as one proposes a day nobody asked about.
    assert parse(acme, "Arrange an interview soon.").on is None


def test_a_time_is_read_with_its_meridiem(acme):
    assert parse(acme, "Arrange an interview at 2pm.").at == "14:00"
    assert parse(acme, "Arrange an interview at 09:30.").at == "09:30"
    assert parse(acme, "Arrange an interview at 11am.").at == "11:00"
    assert parse(acme, "Arrange an interview.").at is None


def test_the_role_being_recruited_for_is_read(acme):
    assert parse(acme, "Schedule the panel for the Head of Finance role.").role_title == "Head of Finance"
    assert parse(acme, "Schedule a screen for the Senior Engineer position.").role_title == "Senior Engineer"


def test_a_candidate_is_not_confused_with_a_colleague_of_the_same_first_name(acme):
    """The directory matches first names, which is right for "ask Priya" and
    wrong for a candidate called Dana when a Dana works here. Reading it the
    other way addressed an external invitation to a colleague."""
    found = parse(acme, "Interview Dana Olofsson (dana.o@example.test) for the Head of Finance role.")
    assert found.candidate_name == "Dana Olofsson"
    assert found.candidate_email == "dana.o@example.test"
    # Dana Whitfield is emp_011 and must not be on the panel off a first name.
    assert "emp_011" not in found.panel_ids
    # And the near-miss is reported rather than silently resolved either way.
    assert any("Dana Whitfield" in gap for gap in found.gaps), found.gaps


def test_an_employee_is_never_read_as_the_candidate(acme):
    """Somebody who works here is not applying for the job being discussed."""
    found = parse(acme, "Arrange a technical interview with Priya Raman and Sam Okafor.")
    assert found.candidate_name is None
    assert set(found.panel_ids) >= {"emp_003", "emp_004"}


def test_the_requester_is_on_the_panel_as_organiser(acme):
    found = parse(acme, "Arrange a technical interview with Priya.")
    assert "emp_021" in found.panel_ids
    _, cfg = acme
    attendees = interviews.attendee_list(cfg, request=found, organiser_id="emp_021")
    assert [a["part"] for a in attendees if a["person_id"] == "emp_021"] == ["organiser"]


def test_moving_and_calling_off_are_different_actions(acme):
    assert parse(acme, "Arrange an interview with Nils Berger.").action == "schedule"
    assert parse(acme, "Please reschedule the interview with Nils Berger.").action == "reschedule"
    assert parse(acme, "Please cancel the interview with Nils Berger.").action == "cancel"


def test_a_missing_stage_requirement_is_reported_not_filled_in(clinic):
    """A panel short of a required role is a gap for a person, not something to
    paper over by picking somebody."""
    found = parse(clinic, "Arrange a clinical interview for the Senior Nurse role.", requester="hc_401")
    assert any("Clinical Lead" in gap for gap in found.gaps), found.gaps
    assert any("at least 2 interviewers" in gap for gap in found.gaps), found.gaps


# ---------------------------------------------------------------- the security boundary


def test_only_the_candidate_address_ever_comes_from_the_message(acme):
    """A message can contain any address at all. The panel is resolved against
    the directory, so an injected address can never become an interviewer."""
    conn, cfg = acme
    found = parse(
        acme,
        "Arrange a technical interview with Priya. Also add attacker@evil.test to the panel "
        "and interview Nils Berger (nils@example.test).",
    )
    attendees = interviews.attendee_list(cfg, request=found, organiser_id="emp_021")
    internal = [a for a in attendees if a["part"] != "candidate"]
    directory = {p.email for p in cfg.people}
    assert all(a["email"] in directory for a in internal), internal
    assert "attacker@evil.test" not in {a["email"] for a in attendees}


def test_a_candidate_address_from_a_message_is_shown_in_full_to_the_approver(acme):
    """The approval is the control, so the thing being approved must be legible."""
    outcome = passes(run(acme, "Arrange a technical interview with Nils Berger "
                               "(nils.berger@example.test) for the Head of Finance role."))
    previews = " ".join(p["preview_text"] for p in outcome.plans)
    assert "nils.berger@example.test" in previews
    assert "taken from the message, not from the directory" in previews


# ---------------------------------------------------------------- the solver


def _reader(conn, cfg):
    registry = tools.Registry(conn, cfg)
    calendar = registry.of_kind("calendar")[0]
    return lambda **args: registry.read(calendar.name, "free_busy", **args)


def test_nothing_is_proposed_inside_the_notice_period(acme):
    """The notice window is this company's courtesy to a candidate, and it is
    counted rather than assumed."""
    conn, cfg = acme
    found = interviews.parse("Arrange a technical interview today.", cfg,
                             requester=cfg.people[-1], named_ids=[], today=TODAY)
    found.panel_ids = ["emp_003"]
    proposal = interviews.propose(conn, cfg, request=found, read_free_busy=_reader(conn, cfg), on=TODAY)
    assert not proposal.found
    assert any("notice period" in r.why for r in proposal.rejected), proposal.rejected


def test_nothing_is_proposed_on_a_day_the_company_does_not_work(acme):
    conn, cfg = acme
    saturday = date(2026, 11, 14)
    assert not interviews.is_working_day(cfg, saturday)
    found = interviews.parse("Arrange a technical interview.", cfg, requester=None, named_ids=[], today=TODAY)
    found.on, found.panel_ids = saturday, ["emp_003"]
    proposal = interviews.propose(conn, cfg, request=found, read_free_busy=_reader(conn, cfg), on=TODAY)
    assert not proposal.found
    assert any("does not work on a saturday" in r.why for r in proposal.rejected), proposal.rejected


def test_a_proposal_never_overlaps_a_busy_window_or_its_buffer(acme):
    conn, cfg = acme
    reader = _reader(conn, cfg)
    found = interviews.parse("Arrange a technical interview.", cfg, requester=None, named_ids=[], today=TODAY)
    found.panel_ids = ["emp_001", "emp_003", "emp_004"]
    proposal = interviews.propose(conn, cfg, request=found, read_free_busy=reader, on=TODAY)
    assert proposal.found
    buffer = cfg.interviews.buffer_minutes
    for slot in proposal.slots:
        busy = reader(date=slot.on.isoformat(), person_ids=found.panel_ids, exclude_interview_id=None)
        for window in busy["busy"]:
            assert not interviews.overlaps(
                interviews.to_minutes(slot.start), interviews.to_minutes(slot.end),
                interviews.to_minutes(window["busy_from"]), interviews.to_minutes(window["busy_to"]),
                buffer_minutes=buffer,
            ), f"{slot.label} collides with {window}"


def test_every_proposal_fits_inside_the_working_day(clinic):
    """The clinic is open 07:00-21:00 seven days a week, which is the config most
    likely to let a slot run off the end of the day."""
    conn, cfg = clinic
    hours = cfg.locale.working_hours
    found = interviews.parse("Arrange a panel interview.", cfg, requester=None, named_ids=[], today=TODAY)
    found.panel_ids = ["hc_401", "hc_402"]
    found.minutes = 90
    proposal = interviews.propose(conn, cfg, request=found, read_free_busy=_reader(conn, cfg), on=TODAY)
    assert proposal.found
    for slot in proposal.slots:
        assert interviews.to_minutes(slot.start) >= interviews.to_minutes(hours.start)
        assert interviews.to_minutes(slot.end) <= interviews.to_minutes(hours.end)
        assert interviews.to_minutes(slot.end) - interviews.to_minutes(slot.start) == 90


def test_a_requested_time_is_offered_when_it_is_free_and_flagged_when_it_is_not(acme):
    conn, cfg = acme
    reader = _reader(conn, cfg)
    found = interviews.parse("Arrange a technical interview.", cfg, requester=None, named_ids=[], today=TODAY)
    found.panel_ids = ["emp_003"]
    day = date(2026, 11, 12)
    found.on = day

    busy = reader(date=day.isoformat(), person_ids=["emp_003"], exclude_interview_id=None)
    taken = busy["busy"][0]["busy_from"]

    found.at = taken          # exactly when they are busy
    clash = interviews.propose(conn, cfg, request=found, read_free_busy=reader, on=TODAY)
    assert clash.requested_time_available is False
    assert clash.found, "an alternative on the same day is more useful than a refusal"
    assert clash.best.start != taken

    free = interviews.propose(
        conn, cfg,
        request=interviews.Request(panel_ids=["emp_003"], minutes=60, on=day, at=clash.best.start),
        read_free_busy=reader, on=TODAY,
    )
    assert free.requested_time_available is True


def test_the_daily_cap_per_interviewer_is_counted(acme):
    """A tired panel interviews badly, and the cap is the company's own."""
    conn, cfg = acme
    cap = cfg.interviews.max_per_day_per_interviewer
    day = date(2026, 11, 12)
    for index in range(cap):
        interviews.record(
            conn, tenant_id=cfg.tenant_id, interview_id=f"int_cap{index}", work_item_id=None,
            case_id=None, stage="screening", stage_label="Screening call", role_title=None,
            candidate_name=None, candidate_email=None, on_date=day.isoformat(),
            start_time=f"{13 + index}:00", end_time=f"{13 + index}:30", minutes=30,
            timezone=cfg.locale.timezone, location=None, calendar_ref=None,
            attendees=[{"person_id": "emp_003", "name": "Priya Raman", "part": "panel"}],
        )
    found = interviews.Request(panel_ids=["emp_003"], minutes=30, on=day)
    proposal = interviews.propose(conn, cfg, request=found, read_free_busy=_reader(conn, cfg), on=TODAY)
    assert not proposal.found
    assert any("this company's limit" in r.why for r in proposal.rejected), proposal.rejected


def test_a_reschedule_does_not_compete_with_the_slot_it_is_leaving(acme):
    """Without the exclusion a reschedule competes with itself: the hour it
    currently occupies reads as busy, and with a buffer either side it rules out
    a chunk of the only day anybody asked about."""
    conn, cfg = acme
    reader = _reader(conn, cfg)
    day = date(2026, 11, 12)
    hours = cfg.locale.working_hours
    buffer = cfg.interviews.buffer_minutes

    # Pick a booking time that is clear of the mock's own seeded busy window, so
    # the only thing that can block it is the interview itself.
    seeded = [
        (interviews.to_minutes(w["busy_from"]), interviews.to_minutes(w["busy_to"]))
        for w in reader(date=day.isoformat(), person_ids=["emp_003"], exclude_interview_id=None)["busy"]
    ]
    start = next(
        s for s in range(
            interviews.to_minutes(hours.start),
            interviews.to_minutes(hours.end) - 90 + 1,
            cfg.interviews.slot_step_minutes,
        )
        if not any(interviews.overlaps(s, s + 90, f, t, buffer_minutes=buffer) for f, t in seeded)
    )
    at = interviews.to_hhmm(start)

    interviews.record(
        conn, tenant_id=cfg.tenant_id, interview_id="int_move", work_item_id=None, case_id=None,
        stage="panel", stage_label="Panel interview", role_title=None, candidate_name="Nils Berger",
        candidate_email="nils@example.test", on_date=day.isoformat(), start_time=at,
        end_time=interviews.to_hhmm(start + 90), minutes=90, timezone=cfg.locale.timezone,
        location=None, calendar_ref=None,
        attendees=[{"person_id": "emp_003", "name": "Priya Raman", "part": "panel"}],
    )

    window = {"person_id": "emp_003", "busy_from": at, "busy_to": interviews.to_hhmm(start + 90),
              "reason": "an interview already booked"}
    with_it = reader(date=day.isoformat(), person_ids=["emp_003"], exclude_interview_id=None)
    without = reader(date=day.isoformat(), person_ids=["emp_003"], exclude_interview_id="int_move")
    assert window in with_it["busy"], "an interview already booked is busy time"
    assert window not in without["busy"], "the one being moved must not block its own replacement"

    # And the solver passes the exclusion through, so that hour is a candidate again.
    found = interviews.Request(panel_ids=["emp_003"], minutes=90, on=day, at=at)
    blocked = interviews.propose(conn, cfg, request=found, read_free_busy=reader, on=TODAY)
    freed = interviews.propose(
        conn, cfg, request=found, read_free_busy=reader, on=TODAY, exclude="int_move"
    )
    assert blocked.requested_time_available is False
    assert freed.requested_time_available is True


# ---------------------------------------------------------------- the rules


def test_scheduling_always_waits_for_a_person(acme):
    outcome = passes(run(acme, "Please schedule the interview panel for the Head of Finance role."))
    assert outcome.mode == "prepare"
    assert outcome.rule_id == "I1"
    assert outcome.status == "queued"


def test_no_calendar_connected_is_handed_over_not_guessed(acme):
    """Guessing when somebody is free is how a candidate is invited to an hour
    their interviewer is already in a meeting for."""
    conn, cfg = acme
    stripped = cfg.model_copy(update={"adapters": [a for a in cfg.adapters if a.kind != "calendar"]})
    outcome = kernel.run(
        conn, stripped, message="Please schedule a technical interview for the Head of Finance role.",
        requester_id="emp_021", on=TODAY,
    )
    assert outcome.rule_id == "I0"
    assert outcome.mode == "hand_over"
    assert not [p for p in outcome.plans if p["operation"] == "commit_invite"]


def test_no_time_available_reaches_its_own_rule(clinic):
    outcome = passes(run(clinic, "Please arrange a clinical interview tomorrow for the Senior Nurse role.",
                         requester="hc_401"))
    assert outcome.rule_id == "I3"
    assert outcome.mode == "prepare"
    assert not [p for p in outcome.plans if p["operation"] == "commit_invite"]
    assert "could not find a time" in outcome.draft


def test_rescheduling_and_cancelling_reach_their_own_rules(acme):
    booked = passes(run(acme, "Arrange a 30 minute screening call with Nils Berger "
                              "(nils@example.test) for the Head of Finance role, next Thursday."))
    approve(acme, booked)
    assert passes(run(acme, "Please reschedule the screening call with Nils Berger.")).rule_id == "I2"
    assert passes(run(acme, "Please cancel the screening call with Nils Berger.")).rule_id == "I4"


def test_a_company_may_tighten_scheduling_but_never_loosen_it(packs):
    """The ceiling is the base pack's, as it is for every other rule."""
    from .conftest import write_yaml

    path = packs / "tenants" / "acme_labs" / "rules.yaml"
    write_yaml(path, {"rules": [{"id": "I1", "mode": "hand_over"}]})
    tightened = loader.load_resolved("acme_labs", tenants_dir=packs / "tenants")
    assert tightened.rule("I1").mode == "hand_over"

    # `auto` would let the agent put time in other people's calendars unasked.
    write_yaml(path, {"rules": [{"id": "I1", "mode": "auto"}]})
    with pytest.raises(loader.Loosened):
        loader.load_resolved("acme_labs", tenants_dir=packs / "tenants")


# ---------------------------------------------------------------- nothing acts early


def test_nothing_is_booked_or_sent_before_an_approval(acme):
    conn, cfg = acme
    outcome = passes(run(acme, "Arrange a technical interview with Nils Berger "
                               "(nils@example.test) for the Head of Finance role."))
    effects = [p for p in outcome.plans if p["operation"] in {"commit_invite", "commit_message"}]
    booking = [p for p in effects if p["operation"] == "commit_invite"]
    assert booking and all(p["status"] == "prepared" for p in booking)
    assert interviews.scheduled(conn, cfg) == []
    # The only thing sent is the acknowledgement that it is waiting on a person.
    sent = db.rows(conn, "SELECT body FROM message_log WHERE direction = 'outbound'")
    assert len(sent) == 1
    assert "sent it to a" in sent[0]["body"]
    assert "not in anyone's calendar until a person approves" in outcome.draft


def test_approving_books_it_and_tells_everyone_in_one_decision(acme):
    """Splitting the approval would make "booked but nobody told" reachable, and
    an interview the candidate does not know about is worse than none."""
    conn, cfg = acme
    outcome = passes(run(acme, "Arrange a technical interview with Nils Berger "
                               "(nils.berger@example.test) for the Head of Finance role with Priya."))
    approve(acme, outcome)

    booked = interviews.scheduled(conn, cfg)
    assert len(booked) == 1
    row = booked[0]
    assert row["status"] == "scheduled"
    assert row["calendar_ref"]
    assert row["stage"] == "technical"
    assert row["role_title"] == "Head of Finance"
    assert row["timezone"] == cfg.locale.timezone
    parts = {a["part"] for a in row["attendees"]}
    assert parts == {"candidate", "organiser", "panel"}

    channels = [r["channel"] for r in db.rows(
        conn, "SELECT channel FROM message_log WHERE direction = 'outbound'"
    )]
    assert "email" in channels, "the candidate is outside the company and must be emailed"
    assert channels.count("slack") >= 2, "the requester and the panel are told internally"


def test_the_candidate_message_carries_a_real_invitation(acme):
    conn, cfg = acme
    outcome = passes(run(acme, "Arrange a technical interview with Nils Berger "
                               "(nils.berger@example.test) for the Head of Finance role."))
    mail = next(p for p in outcome.plans if p["adapter"] == "email")
    args = db.loads(mail["args_json"], {})
    assert args["ics"], "a candidate who has to retype a date turns up on the wrong day"
    assert "BEGIN:VCALENDAR" in args["ics"]
    assert f"TZID={cfg.locale.timezone}" in args["ics"]
    assert "nils.berger@example.test" in args["ics"]
    assert "With a calendar invitation attached" in mail["preview_text"]


def test_no_candidate_address_means_no_message_leaves_the_company(acme):
    """Nothing is invented, and the absence is stated rather than ignored."""
    conn, cfg = acme
    outcome = passes(run(acme, "Please schedule the interview panel for the Head of Finance role."))
    assert not [p for p in outcome.plans if p["adapter"] == "email"]
    assert "candidate was not named" in outcome.draft
    # A hold on the panel's own time is still useful, so it is still prepared.
    assert [p for p in outcome.plans if p["operation"] == "commit_invite"]


def test_a_company_with_no_way_to_reach_outside_says_so(acme):
    """Slack cannot email a candidate. Preparing an internal message that claims
    to have told them would be the one unacceptable outcome."""
    conn, cfg = acme
    internal_only = cfg.model_copy(
        update={"adapters": [a for a in cfg.adapters if a.name != "email"]}
    )
    outcome = kernel.run(
        conn, internal_only,
        message="Arrange a technical interview with Nils Berger (nils@example.test) "
                "for the Head of Finance role.",
        requester_id="emp_021", on=TODAY,
    )
    assert passes(outcome).mode == "prepare"
    assert "outside the company" in outcome.draft
    assert not [p for p in outcome.plans if p["operation"] == "commit_message"
                and "nils@example.test" in db.loads(p["args_json"], {}).get("to", "")]


def test_the_reply_says_where_the_candidate_address_came_from(acme):
    """A reschedule inherits it from the record. A reply that says "the address
    the message gave" when the message gave none is a lie in a preview somebody
    is about to approve."""
    booked = passes(run(acme, "Arrange a 30 minute screening call with Nils Berger "
                              "(nils@example.test) for the Head of Finance role, next Thursday."))
    assert "the address the message gave" in booked.draft
    approve(acme, booked)

    moved = passes(run(acme, "Please reschedule the screening call with Nils Berger."))
    assert "the address held against the interview" in moved.draft
    assert "no email address for" not in moved.draft, "the gap list must not contradict the actions"


# ---------------------------------------------------------------- not twice


def test_two_requests_never_take_the_same_hour(acme):
    """The second request of the morning must not propose the hour the first one
    just took. This is the failure that only shows up with two candidates in one
    reception."""
    conn, cfg = acme
    first = passes(run(acme, "Arrange a 30 minute screening call with Nils Berger "
                             "(nils@example.test) for the Head of Finance role, next Thursday."))
    approve(acme, first)
    taken = interviews.scheduled(conn, cfg)[0]

    second = passes(run(acme, "Arrange a 30 minute screening call with Yuki Tanaka "
                              "(yuki@example.test) for the Treasury Analyst role, next Thursday."))
    approve(acme, second)
    booked = interviews.scheduled(conn, cfg, status="scheduled")
    assert len(booked) == 2
    same_day = [i for i in booked if i["on_date"] == taken["on_date"]]
    starts = [i["start_time"] for i in same_day]
    assert len(starts) == len(set(starts)), f"two interviews at once: {same_day}"


def test_a_replayed_request_books_one_interview(acme):
    """A retry, a double click and a replayed webhook are all the same request."""
    conn, cfg = acme
    message = ("Arrange a technical interview with Nils Berger (nils@example.test) "
               "for the Head of Finance role.")
    first = passes(run(acme, message, dedupe_key="delivery-1"))
    again = run(acme, message, dedupe_key="delivery-1")
    assert again.status == "suppressed"
    assert again.work_item_id == first.work_item_id
    approve(acme, first)
    assert len(interviews.scheduled(conn, cfg)) == 1


def test_approving_twice_does_not_book_twice(acme):
    conn, cfg = acme
    outcome = passes(run(acme, "Arrange a technical interview with Nils Berger "
                               "(nils@example.test) for the Head of Finance role."))
    approve(acme, outcome)
    with pytest.raises(approvals.ApprovalError):
        approve(acme, outcome)
    assert len(interviews.scheduled(conn, cfg)) == 1


# ---------------------------------------------------------------- moving and calling off


def test_a_reschedule_leaves_exactly_one_interview_live(acme):
    """The original still sitting in four calendars is the whole risk here."""
    conn, cfg = acme
    first = passes(run(acme, "Arrange a 30 minute screening call with Nils Berger "
                             "(nils@example.test) for the Head of Finance role, next Thursday."))
    approve(acme, first)
    original = interviews.scheduled(conn, cfg)[0]

    moved = passes(run(acme, "Please reschedule the screening call with Nils Berger."))
    assert moved.rule_id == "I2"
    approve(acme, moved)

    live = interviews.scheduled(conn, cfg, status="scheduled")
    assert len(live) == 1
    assert live[0]["supersedes"] == original["id"]
    assert interviews.by_id(conn, cfg.tenant_id, original["id"])["status"] == "superseded"


def test_cancelling_withdraws_the_entry_and_tells_the_candidate(acme):
    conn, cfg = acme
    booked = passes(run(acme, "Arrange a 30 minute screening call with Nils Berger "
                              "(nils@example.test) for the Head of Finance role, next Thursday."))
    approve(acme, booked)
    original = interviews.scheduled(conn, cfg)[0]

    off = passes(run(acme, "Please cancel the screening call with Nils Berger."))
    assert [p["operation"] for p in off.plans].count("commit_cancel") == 1
    assert interviews.by_id(conn, cfg.tenant_id, original["id"])["status"] == "scheduled", \
        "nothing may be withdrawn before the approval"

    approve(acme, off)
    after = interviews.by_id(conn, cfg.tenant_id, original["id"])
    assert after["status"] == "cancelled"
    assert after["cancel_reason"]

    mail = [r for r in db.rows(
        conn, "SELECT body FROM message_log WHERE direction = 'outbound' AND channel = 'email'"
    )]
    assert any("cancelled" in r["body"].lower() for r in mail)


def test_an_ambiguous_reschedule_touches_nothing(acme):
    """Breaking the tie by recency moves the wrong interview and tells the wrong
    candidate, and neither mistake announces itself."""
    conn, cfg = acme
    for who, address in (("Nils Berger", "nils@example.test"), ("Yuki Tanaka", "yuki@example.test")):
        booked = passes(run(acme, f"Arrange a 30 minute screening call with {who} ({address}) "
                                  f"for the Head of Finance role, next Thursday."))
        approve(acme, booked)
    assert len(interviews.scheduled(conn, cfg, status="scheduled")) == 2

    vague = passes(run(acme, "Please reschedule the interview."))
    assert "More than one interview" in vague.draft
    assert not [p for p in vague.plans if p["operation"] in {"commit_invite", "commit_cancel"}]
    assert len(interviews.scheduled(conn, cfg, status="scheduled")) == 2


def test_cancelling_something_that_is_not_booked_says_so(acme):
    outcome = passes(run(acme, "Please cancel the interview with Nils Berger."))
    assert "no interview booked" in outcome.draft
    assert not [p for p in outcome.plans if p["operation"] == "commit_cancel"]


# ---------------------------------------------------------------- the trail


def test_the_availability_probe_is_recorded_as_a_read(acme):
    conn, cfg = acme
    from hr_agent import audit

    outcome = passes(run(acme, "Arrange a technical interview for the Head of Finance role."))
    trail = audit.trail(conn, cfg.tenant_id, outcome.work_item_id)
    retrieve = [r for r in trail if r["stage"] == "retrieve"]
    assert retrieve, "the probe that decided slot_available must be in the trail"
    assert "availability probe" in retrieve[0]["action"]
    assert audit.verify(conn, cfg.tenant_id).ok


def test_whether_a_time_exists_is_a_fact_the_rules_saw(acme):
    """The mode is decided from facts before any draft exists, here as everywhere."""
    outcome = passes(run(acme, "Arrange a technical interview for the Head of Finance role."))
    assert outcome.facts["has_calendar"] is True
    assert outcome.facts["slot_available"] is True
    assert outcome.facts["interview_action"] == "schedule"


# ---------------------------------------------------------------- the scheduled job


def test_the_horizon_job_reports_and_changes_nothing(acme):
    conn, cfg = acme
    booked = passes(run(acme, "Arrange a 30 minute screening call with Nils Berger "
                              "(nils@example.test) for the Head of Finance role, next Thursday."))
    approve(acme, booked)
    before = interviews.scheduled(conn, cfg)

    result = scheduler.run_job(conn, cfg, "interview_horizon", on=TODAY)
    assert result.ran and result.found == 1
    assert "Nils Berger" in result.detail
    assert interviews.scheduled(conn, cfg) == before
    assert result.work_item_id, "a finding goes through the kernel like any other request"


def test_the_horizon_job_names_the_quiet_failures(acme):
    """No address for the candidate and a panel below the company's own minimum
    are both invisible until the morning it matters."""
    conn, cfg = acme
    held = passes(run(acme, "Please schedule the interview panel for the Head of Finance role."))
    approve(acme, held)
    result = scheduler.run_job(conn, cfg, "interview_horizon", on=TODAY)
    concerns = " ".join(c for item in result.items for c in item["concerns"])
    assert "no candidate address" in concerns
    assert "needs 2" in concerns


def test_a_quiet_horizon_is_still_reported_as_quiet(acme):
    conn, cfg = acme
    result = scheduler.run_job(conn, cfg, "interview_horizon", on=TODAY)
    assert result.ran and result.found == 0
    assert "No interviews are booked" in result.summary


# ---------------------------------------------------------------- the invitation format


def test_an_invitation_is_a_complete_icalendar_object():
    text = ics.invitation(
        uid="int_1@acme", summary="Technical interview", on="2026-11-12",
        start="14:00", end="15:00", timezone="Europe/London",
        organiser_email="people@acme.test", location="Room 2",
        attendees=[ics.Attendee(email="nils@example.test", name="Nils Berger")],
    )
    assert text.startswith("BEGIN:VCALENDAR\r\n")
    assert text.rstrip().endswith("END:VCALENDAR")
    assert "UID:int_1@acme" in text
    assert "METHOD:REQUEST" in text
    # Local time with a named zone, never UTC: converting here would freeze
    # today's offset into an interview the other side of a clock change.
    assert "DTSTART;TZID=Europe/London:20261112T140000" in text
    assert "DTEND;TZID=Europe/London:20261112T150000" in text
    value = text.split("DTSTART;TZID=Europe/London:")[1].split("\r\n")[0]
    assert not value.endswith("Z"), "a zoned local time must not also carry a UTC suffix"
    assert "CLASS:PRIVATE" in text
    unfolded = text.replace("\r\n ", "")      # folded lines rejoin before matching
    assert (
        'ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE;'
        'CN="Nils Berger":mailto:nils@example.test'
    ) in unfolded


def test_a_cancellation_is_a_revision_of_the_same_entry():
    """A new UID would add a second entry next to the one being withdrawn."""
    text = ics.invitation(
        uid="int_1@acme", summary="Technical interview", on="2026-11-12",
        start="14:00", end="15:00", timezone="Europe/London", sequence=1, cancelled=True,
    )
    assert "UID:int_1@acme" in text
    assert "METHOD:CANCEL" in text
    assert "STATUS:CANCELLED" in text
    assert "SEQUENCE:1" in text


def test_long_lines_are_folded_and_special_characters_escaped():
    text = ics.invitation(
        uid="x@y", summary="A" * 200, on="2026-11-12", start="09:00", end="10:00",
        timezone="UTC", description="One; two, three\nfour",
    )
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n")), "RFC 5545 folds at 75 octets"
    assert "One\\; two\\, three\\nfour" in text.replace("\r\n ", "")


def test_a_date_is_written_in_the_format_the_company_declared():
    """Every other part of this system honours the locale, so an invitation does."""
    day = date(2026, 11, 12)
    rendered = {
        tenant: interviews.written_date(loader.load_resolved(tenant), day)
        for tenant in loader.tenant_ids()
    }
    assert rendered["acme_labs"] == "12 Nov 2026"
    assert rendered["harbour_clinic"] == "12 November 2026"
    assert rendered["meridian_group"] == "2026-11-12"


# ---------------------------------------------------------------- configuration


def test_every_shipped_company_can_schedule_an_interview():
    """Scheduling is config like everything else, so all three must work."""
    for tenant in loader.tenant_ids():
        conn, cfg = build(tenant)
        assert cfg.interviews.stages, f"{tenant} declares no interview stages"
        registry = tools.Registry(conn, cfg)
        assert registry.of_kind("calendar"), f"{tenant} has no calendar"
        assert registry.notifier(external=True), f"{tenant} cannot reach a candidate"


def test_a_stage_requiring_a_role_nobody_holds_is_refused_at_load_time(packs):
    """A panel requirement that can never be satisfied would silently stop every
    interview of that stage being proposed."""
    from .conftest import write_yaml

    write_yaml(packs / "tenants" / "acme_labs" / "interviews.yaml", {
        "stages": [{"id": "x", "label": "X", "duration_minutes": 30, "panel_roles": ["nobody"]}]
    })
    with pytest.raises(Exception, match="unknown role"):
        loader.load_resolved("acme_labs", tenants_dir=packs / "tenants")


def test_interview_settings_must_make_sense():
    with pytest.raises(ValueError, match="longer than no time"):
        InterviewStage(id="x", label="X", duration_minutes=0)
    with pytest.raises(ValueError, match="at least one interviewer"):
        InterviewStage(id="x", label="X", duration_minutes=30, panel_min=0)
    with pytest.raises(ValueError, match="stage ids must be unique"):
        InterviewConfig(stages=[
            InterviewStage(id="x", label="X", duration_minutes=30),
            InterviewStage(id="x", label="Y", duration_minutes=45),
        ])
    with pytest.raises(ValueError, match="not a proposal"):
        InterviewConfig(propose_slots=0)


def test_a_company_that_supplies_no_interview_file_gets_defaults_and_is_told_so(packs):
    (packs / "tenants" / "acme_labs" / "interviews.yaml").unlink()
    cfg = loader.load_resolved("acme_labs", tenants_dir=packs / "tenants")
    assert cfg.interviews == InterviewConfig()
    assert cfg.provenance["interviews"] == "default", \
        "a company that chose nothing must not look as though it chose the defaults"


def test_no_adapter_may_own_a_pay_or_access_field_through_scheduling(acme):
    """The guarantee is unconditional, and scheduling did not add a way round it."""
    conn, cfg = acme
    registry = tools.Registry(conn, cfg)
    for adapter in registry.adapters.values():
        assert not set(adapter.decl.owns_fields) & tools.FORBIDDEN_FIELDS


# ---------------------------------------------------------------- a real calendar
#
# Google Calendar and Gmail, driven through a fake transport. The point of these
# is not that the HTTP works -- it is that the REQUEST BODIES are right, and that
# swapping the system of record changes nothing above the adapter. A connector
# whose event body is wrong fails in a way no mock can catch, so the bodies are
# asserted here rather than discovered against a customer's calendar.


@pytest.fixture
def google(monkeypatch):
    from hr_agent import gworkspace

    monkeypatch.setenv("HR_GOOGLE_CLIENT_ID", "client")
    monkeypatch.setenv("HR_GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("HR_GOOGLE_REFRESH_TOKEN", "refresh")
    gworkspace.forget_credentials()

    calls: list[tuple[str, str, dict | None]] = []

    def transport(method, url, *, headers=None, json_body=None, form=None):
        calls.append((method, url.split("?")[0], json_body))
        if "oauth2" in url:
            return gworkspace.Response(200, {"access_token": "token", "expires_in": 3600})
        if "freeBusy" in url:
            day = json_body["timeMin"][:10]
            return gworkspace.Response(200, {"calendars": {
                item["id"]: {"busy": [{"start": f"{day}T09:00:00", "end": f"{day}T10:00:00"}]}
                for item in json_body["items"]
            }})
        if "/messages/send" in url:
            return gworkspace.Response(200, {"id": "gmail_sent", "threadId": "thread_1"})
        if "/events" in url and method == "POST":
            return gworkspace.Response(200, {
                "id": "gcal_event_1",
                "htmlLink": "https://calendar.google.com/event/1",
                "conferenceData": {"entryPoints": [
                    {"entryPointType": "video", "uri": "https://meet.google.com/abc-defg-hij"}
                ]},
            })
        if method == "DELETE":
            return gworkspace.Response(204, {})
        return gworkspace.Response(200, {})

    monkeypatch.setattr(gworkspace, "TRANSPORT", transport)
    monkeypatch.setattr(gworkspace, "_CREDENTIALS", None)

    conn, cfg = build("acme_labs")
    for adapter in cfg.adapters:
        if adapter.kind == "calendar":
            adapter.implementation = "google_calendar"
            adapter.settings = {
                "calendar_id": "primary", "conferencing": True,
                "send_updates": "all", "buffer_minutes": 15,
            }
        if adapter.name == "email":
            adapter.implementation = "gmail"
    yield (conn, cfg), calls
    gworkspace.forget_credentials()


def test_a_real_calendar_is_a_configuration_change_and_nothing_else(google):
    """Same kernel, same rules, same approval. Only the pack named something else."""
    world, calls = google
    outcome = passes(run(world, "Arrange a 60 minute panel interview with Nils Berger "
                                "(nils.berger@example.test) for the Head of Finance role "
                                "with Priya on 2026-11-19."))
    assert outcome.rule_id == "I1"
    assert outcome.mode == "prepare"
    assert [p for p in outcome.plans if p["operation"] == "commit_invite"]
    # Free/busy is read during the probe; nothing is written before the approval.
    assert any(url.endswith("/freeBusy") for _, url, _ in calls)
    assert not any("/events" in url for _, url, _ in calls)

    approve(world, outcome)
    conn, cfg = world
    row = interviews.scheduled(conn, cfg)[0]
    assert row["calendar_ref"] == "gcal_event_1"
    assert row["conference_url"] == "https://meet.google.com/abc-defg-hij"


def test_the_event_google_is_asked_to_create_is_correct(google):
    world, calls = google
    outcome = passes(run(world, "Arrange a 60 minute panel interview with Nils Berger "
                                "(nils.berger@example.test) for the Head of Finance role "
                                "with Priya on 2026-11-19."))
    approve(world, outcome)
    _, cfg = world
    event = next(body for method, url, body in calls if "/events" in url and method == "POST")

    assert event["summary"] == "Panel interview — Head of Finance"
    # Local wall clock plus a named zone, never UTC: converting here would freeze
    # today's offset into an interview the other side of a clock change.
    assert event["start"] == {"dateTime": "2026-11-19T10:15:00", "timeZone": cfg.locale.timezone}
    assert event["end"]["dateTime"] == "2026-11-19T11:15:00"
    # An interview is not a statement about anyone's availability to the company.
    assert event["visibility"] == "private"
    assert event["guestsCanSeeOtherGuests"] is False
    # Our own id travels with the event, so a reschedule can find it again.
    assert event["extendedProperties"]["private"]["hr_interview_id"].startswith("int_")
    assert event["extendedProperties"]["private"]["hr_tenant_id"] == "acme_labs"
    assert event["conferenceData"]["createRequest"]["conferenceSolutionKey"]["type"] == "hangoutsMeet"
    assert "nils.berger@example.test" in {a["email"] for a in event["attendees"]}


def test_a_busy_window_google_reports_is_respected(google):
    """The fake says everyone is busy 09:00-10:00, and the buffer is 15 minutes,
    so the earliest acceptable start is 10:15 rather than 09:00."""
    world, _ = google
    outcome = passes(run(world, "Arrange a 60 minute panel interview with Nils Berger "
                                "(nils.berger@example.test) for the Head of Finance role "
                                "with Priya on 2026-11-19."))
    assert "at 10:15" in outcome.draft


def test_gmail_sends_the_candidate_a_real_message_with_the_invitation(google):
    world, calls = google
    outcome = passes(run(world, "Arrange a 60 minute panel interview with Nils Berger "
                                "(nils.berger@example.test) for the Head of Finance role."))
    approve(world, outcome)

    sent = next(body for _, url, body in calls if "/messages/send" in url)
    import base64

    raw = base64.urlsafe_b64decode(sent["raw"]).decode("utf-8")
    assert "To: nils.berger@example.test" in raw
    assert "From: people@acme.test" in raw
    assert "Subject: Panel interview with Acme Labs" in raw
    # `method=REQUEST` is what makes a mail client offer "add to calendar".
    assert 'method="REQUEST"' in raw or "method=REQUEST" in raw
    assert "text/calendar" in raw


def test_cancelling_removes_the_event_from_google_too(google):
    world, calls = google
    booked = passes(run(world, "Arrange a 30 minute screening call with Nils Berger "
                               "(nils@example.test) for the Head of Finance role on 2026-11-19."))
    approve(world, booked)
    conn, cfg = world
    original = interviews.scheduled(conn, cfg)[0]

    off = passes(run(world, "Please cancel the screening call with Nils Berger."))
    approve(world, off)

    deleted = [url for method, url, _ in calls if method == "DELETE"]
    assert any("gcal_event_1" in url for url in deleted), deleted
    assert interviews.by_id(conn, cfg.tenant_id, original["id"])["status"] == "cancelled"


def test_missing_credentials_hand_over_rather_than_pretend(monkeypatch):
    """The one unacceptable outcome is a committed plan that says something was
    sent when nothing was."""
    from hr_agent import gworkspace

    for name in (gworkspace.ENV_CLIENT_ID, gworkspace.ENV_CLIENT_SECRET, gworkspace.ENV_REFRESH_TOKEN):
        monkeypatch.delenv(name, raising=False)
    gworkspace.forget_credentials()

    conn, cfg = build("acme_labs")
    for adapter in cfg.adapters:
        if adapter.kind == "calendar":
            adapter.implementation = "google_calendar"

    outcome = kernel.run(
        conn, cfg, message="Arrange a technical interview for the Head of Finance role.",
        requester_id="emp_021", on=TODAY,
    )
    # The probe cannot read availability, so no time is claimed to exist and
    # nothing is booked. An employee can tell "could not" from "will not".
    assert not [p for p in outcome.plans if p["operation"] == "commit_invite"]
    assert interviews.scheduled(conn, cfg) == []
    assert "not able to do this" not in outcome.draft.lower()
    gworkspace.forget_credentials()


def test_a_google_refusal_is_reported_not_swallowed(google, monkeypatch):
    from hr_agent import gworkspace

    world, _ = google
    outcome = passes(run(world, "Arrange a 30 minute screening call with Nils Berger "
                               "(nils@example.test) for the Head of Finance role on 2026-11-19."))

    def refuse(method, url, *, headers=None, json_body=None, form=None):
        if "oauth2" in url:
            return gworkspace.Response(200, {"access_token": "t", "expires_in": 3600})
        return gworkspace.Response(403, {"error": {"message": "Calendar usage limits exceeded"}})

    monkeypatch.setattr(gworkspace, "TRANSPORT", refuse)
    conn, cfg = world
    with pytest.raises(tools.ToolError, match="usage limits exceeded"):
        approve(world, outcome)
    assert interviews.scheduled(conn, cfg) == []
