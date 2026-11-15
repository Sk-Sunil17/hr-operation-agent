"""HR changing how a rule is handled, from the dashboard rather than a pack file.

The pack stays the floor: a setting may only make a rule stricter than the
company's rulebook, relaxing needs a second HR person, and every change is
on the audit chain.
"""

from __future__ import annotations

import warnings

import pytest
from fastapi.testclient import TestClient

from hr_agent import audit, kernel, rulebook, web

from .conftest_agent import TODAY, build

warnings.filterwarnings("ignore", category=DeprecationWarning)

HR, OTHER_HR, EMPLOYEE = "emp_021", "emp_020", "emp_001"
ENQUIRY = "How many days of annual leave do I get in a year?"


@pytest.fixture
def world():
    return build("acme_labs")


def ask(conn, cfg):
    return kernel.run(conn, cfg, message=ENQUIRY, requester_id=EMPLOYEE, on=TODAY)


def test_hr_can_make_a_rule_stricter_and_the_agent_follows(world):
    conn, pack = world
    assert ask(conn, rulebook.apply(conn, pack)).mode == "auto"
    rulebook.change(conn, pack, rule_id="E1", mode="prepare", actor_id=HR, note="reviewing answers this month")
    cfg = rulebook.apply(conn, pack)
    assert cfg.config_hash != pack.config_hash
    assert ask(conn, cfg).mode == "prepare"


def test_nothing_goes_looser_than_the_pack(world):
    conn, pack = world
    with pytest.raises(rulebook.RuleChangeError, match="at the least"):
        rulebook.change(conn, pack, rule_id="R1", mode="auto", actor_id=HR, note="please let it through")


def test_only_hr_may_change_a_rule(world):
    conn, pack = world
    with pytest.raises(rulebook.RuleChangeError, match="HR role"):
        rulebook.change(conn, pack, rule_id="E1", mode="prepare", actor_id=EMPLOYEE, note="I would like this")


def test_a_reason_is_required(world):
    conn, pack = world
    with pytest.raises(rulebook.RuleChangeError):
        rulebook.change(conn, pack, rule_id="E1", mode="prepare", actor_id=HR, note="")


def test_relaxing_takes_a_second_hr_person(world):
    conn, pack = world
    rulebook.change(conn, pack, rule_id="E1", mode="hand_over", actor_id=HR, note="new policy pending")
    with pytest.raises(rulebook.RuleChangeError, match="second HR person"):
        rulebook.change(conn, pack, rule_id="E1", mode="auto", actor_id=HR, note="policy is live now")
    result = rulebook.change(conn, pack, rule_id="E1", mode="auto", actor_id=OTHER_HR, note="policy is live now")
    assert result["relaxed"] is True
    assert rulebook.settings(conn, pack.tenant_id) == {}
    assert rulebook.apply(conn, pack).config_hash == pack.config_hash


def test_every_change_is_on_the_audit_chain(world):
    conn, pack = world
    rulebook.change(conn, pack, rule_id="E1", mode="prepare", actor_id=HR, note="reviewing answers this month")
    rows = conn.execute(
        "SELECT action, actor FROM audit WHERE tenant_id = ? AND action LIKE 'rule E1%'", (pack.tenant_id,)
    ).fetchall()
    assert [(r["action"], r["actor"]) for r in rows] == [("rule E1 tightened from auto to prepare", HR)]
    assert audit.verify(conn, pack.tenant_id).ok


def test_settings_belong_to_one_company(world):
    conn, pack = world
    rulebook.change(conn, pack, rule_id="E1", mode="prepare", actor_id=HR, note="reviewing answers this month")
    assert rulebook.settings(conn, "meridian_group") == {}


@pytest.fixture
def client(tmp_path):
    path = str(tmp_path / "hr.db")
    build("acme_labs", path)[0].close()
    return TestClient(web.create_app(db_path=path))


def test_the_rules_screen_says_who_may_change_what(client):
    hr = client.get("/api/rules?tenant=acme_labs&actor=emp_021").json()
    assert hr["can_change"] is True
    r1 = next(r for r in hr["items"] if r["id"] == "R1")
    assert "auto" not in r1["allowed_modes"]
    assert client.get("/api/rules?tenant=acme_labs&actor=emp_001").json()["can_change"] is False


def test_a_preview_changes_nothing(client):
    preview = client.post("/api/rules/E1/preview?tenant=acme_labs", json={"mode": "prepare"}).json()
    assert preview["changed"] and all(c["after"] == "prepare" for c in preview["changed"])
    assert next(r for r in client.get("/api/rules?tenant=acme_labs").json()["items"] if r["id"] == "E1")["mode"] == "auto"


def test_a_refused_change_is_a_conflict_not_a_fault(client):
    response = client.post("/api/rules/R1?tenant=acme_labs",
                           json={"actor": "emp_021", "mode": "auto", "note": "let it through"})
    assert response.status_code == 409


def test_a_change_through_the_api_is_what_the_agent_then_does(client):
    changed = client.post("/api/rules/E1?tenant=acme_labs",
                          json={"actor": HR, "mode": "prepare", "note": "reviewing answers this month"})
    assert changed.status_code == 200
    reply = client.post("/api/chat?tenant=acme_labs", json={"message": ENQUIRY, "requester": EMPLOYEE}).json()
    assert reply["item"]["status"] == "queued"
