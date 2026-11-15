"""Starting a leaver: HR opens the exit case, then the agent can prepare the checklist."""

from __future__ import annotations

import warnings
from datetime import date

import pytest
from fastapi.testclient import TestClient

from hr_agent import audit, cases, db, kernel, web

from .conftest_agent import TODAY, build

warnings.filterwarnings("ignore", category=DeprecationWarning)

HR, OTHER_HR, EMPLOYEE, LEAVER = "emp_021", "emp_020", "emp_001", "emp_002"


@pytest.fixture
def world():
    return build("acme_labs")


def ask(conn, cfg):
    return kernel.run(conn, cfg, message="Tomas Brandt is leaving, what happens next?", requester_id=HR, on=TODAY)


def test_without_a_case_the_agent_declines(world):
    conn, cfg = world
    assert ask(conn, cfg).rule_id == "X1"


def test_once_hr_opens_the_case_the_checklist_is_prepared(world):
    conn, cfg = world
    cases.open_exit(conn, cfg, subject_id=LEAVER, actor_id=HR, last_day=date(2026, 11, 28), note="Resigned today")
    outcome = ask(conn, cfg)
    assert (outcome.rule_id, outcome.mode) == ("X2", "prepare")
    assert "departure checklist" in outcome.draft


def test_only_hr_opens_one(world):
    conn, cfg = world
    with pytest.raises(cases.CaseError, match="HR role"):
        cases.open_exit(conn, cfg, subject_id=LEAVER, actor_id=EMPLOYEE, last_day=None, note="Resigned today")


def test_nobody_opens_their_own(world):
    conn, cfg = world
    with pytest.raises(cases.CaseError, match="own exit case"):
        cases.open_exit(conn, cfg, subject_id=HR, actor_id=HR, last_day=None, note="I am leaving")


def test_one_open_case_per_person(world):
    conn, cfg = world
    cases.open_exit(conn, cfg, subject_id=LEAVER, actor_id=HR, last_day=None, note="Resigned today")
    with pytest.raises(cases.CaseError, match="already has"):
        cases.open_exit(conn, cfg, subject_id=LEAVER, actor_id=OTHER_HR, last_day=None, note="Resigned today")


def test_closing_ends_it_and_both_are_audited(world):
    conn, cfg = world
    opened = cases.open_exit(conn, cfg, subject_id=LEAVER, actor_id=HR, last_day=None, note="Resigned today")
    cases.close_exit(conn, cfg, case_id=opened["case_id"], actor_id=OTHER_HR, note="Departure complete")
    assert ask(conn, cfg).rule_id == "X1"
    actions = [r["action"] for r in conn.execute(
        "SELECT action FROM audit WHERE tenant_id = ? AND action LIKE 'exit case%'", (cfg.tenant_id,))]
    assert actions == ["exit case opened for Tomas Brandt", "exit case closed"]
    assert audit.verify(conn, cfg.tenant_id).ok


def test_the_api_refuses_rather_than_failing(tmp_path):
    path = str(tmp_path / "hr.db")
    build("acme_labs", path)[0].close()
    client = TestClient(web.create_app(db_path=path))
    refused = client.post("/api/cases/exit?tenant=acme_labs",
                          json={"actor": EMPLOYEE, "subject": LEAVER, "note": "Resigned today"})
    assert refused.status_code == 409
    bad_day = client.post("/api/cases/exit?tenant=acme_labs",
                          json={"actor": HR, "subject": LEAVER, "last_day": "soon", "note": "Resigned today"})
    assert bad_day.status_code == 400
    opened = client.post("/api/cases/exit?tenant=acme_labs",
                         json={"actor": HR, "subject": LEAVER, "last_day": "2026-11-28", "note": "Resigned today"})
    assert opened.status_code == 200
    listed = client.get("/api/cases/exit?tenant=acme_labs").json()["items"]
    assert [(c["subject_name"], c["last_day"]) for c in listed] == [("Tomas Brandt", "2026-11-28")]


def test_an_older_database_gains_the_last_day_column():
    conn = db.open_db(":memory:")
    conn.executescript("DROP TABLE hr_case; CREATE TABLE hr_case (id TEXT PRIMARY KEY, tenant_id TEXT, kind TEXT,"
                       " subject_id TEXT, owner_role TEXT, owner_id TEXT, status TEXT, taken_over INTEGER,"
                       " opened_at TEXT, closed_at TEXT);")
    db.init(conn)
    assert "last_day" in {r[1] for r in conn.execute("PRAGMA table_info(hr_case)")}
