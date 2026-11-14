"""One server: the dashboard and the API on one origin, and what it must refuse.

Also the regressions found while making it one server: an approval decided
twice by two requests racing, bad dates answered as server faults, and every
read route left open while authentication was on.
"""

from __future__ import annotations

import warnings

import pytest
from fastapi.testclient import TestClient

from hr_agent import approvals, auth, config, db, kernel, tools, web

from .conftest_agent import TODAY, build

warnings.filterwarnings("ignore", category=DeprecationWarning)


# ---------------------------------------------------------------- the dashboard


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>dashboard</title>", encoding="utf-8")
    (dist / "assets" / "app-123.js").write_text("console.log('hi')", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("not for the browser", encoding="utf-8")
    monkeypatch.setattr(config, "DASHBOARD_DIR", dist)
    return TestClient(web.create_app())


def test_the_root_is_the_dashboard(dashboard):
    response = dashboard.get("/")
    assert response.status_code == 200
    assert "dashboard" in response.text
    assert response.headers["cache-control"] == "no-cache"


def test_any_screen_path_is_the_dashboard(dashboard):
    assert "dashboard" in dashboard.get("/approvals/appr_123").text


def test_built_files_are_served(dashboard):
    assert dashboard.get("/assets/app-123.js").text == "console.log('hi')"
    assert dashboard.get("/favicon.svg").text == "<svg/>"


def test_the_api_is_on_the_same_server(dashboard):
    assert dashboard.get("/api/health").json()["ok"] is True


def test_an_unknown_api_path_is_json_not_the_dashboard(dashboard):
    response = dashboard.get("/api/no-such-thing")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize("path", ["/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt"])
def test_nothing_outside_the_build_is_reachable(dashboard, path):
    assert "not for the browser" not in dashboard.get(path).text


def test_security_headers_are_set(dashboard):
    headers = dashboard.get("/").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "SAMEORIGIN"


def test_an_unbuilt_dashboard_says_how_to_build_it(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_DIR", tmp_path / "missing")
    response = TestClient(web.create_app()).get("/")
    assert response.status_code == 503
    assert "npm run build" in response.text


# ---------------------------------------------------------------- bad input


@pytest.fixture
def client():
    return TestClient(web.create_app())


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("POST", "/api/simulate?tenant=acme_labs&on=garbage"),
        ("POST", "/api/jobs/all?tenant=acme_labs&on=2026-13-01"),
        ("GET", "/api/interviews?tenant=acme_labs&from=nope"),
        ("GET", "/api/interviews/availability?tenant=acme_labs&panel=emp_001&on=xx"),
    ],
)
def test_a_bad_date_is_a_bad_request_not_a_crash(client, method, url):
    response = client.request(method, url)
    assert response.status_code == 400
    assert "must be a date" in response.json()["detail"]


def test_an_interview_cannot_last_negative_minutes(client):
    url = "/api/interviews/availability?tenant=acme_labs&panel=emp_001&minutes=-5"
    assert client.get(url).status_code == 422


# ---------------------------------------------------------------- authentication


@pytest.fixture
def signed(monkeypatch):
    monkeypatch.setattr(auth, "enabled", lambda: True)
    return TestClient(web.create_app())


@pytest.mark.parametrize(
    "url",
    ["/api/work-items?tenant=acme_labs", "/api/audit?tenant=acme_labs", "/api/metrics?tenant=acme_labs",
     "/api/people?tenant=acme_labs", "/api/interviews?tenant=acme_labs"],
)
def test_every_company_read_needs_a_session_when_auth_is_on(signed, url):
    assert signed.get(url).status_code == 401


def test_a_session_reads_its_own_company_only(signed):
    token = auth.issue(tenant_id="acme_labs", person_id="emp_021")
    headers = {"Authorization": f"Bearer {token}"}
    assert signed.get("/api/people?tenant=acme_labs", headers=headers).status_code == 200
    assert signed.get("/api/people?tenant=meridian_group", headers=headers).status_code == 403


@pytest.mark.parametrize("url", ["/api/health", "/api/tenants", "/api/reason-codes"])
def test_what_a_sign_in_screen_needs_stays_open(signed, url):
    assert signed.get(url).status_code == 200


PASSWORD = "correct horse battery"


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    """A server with authentication on and one account, on its own database."""
    monkeypatch.setattr(auth, "enabled", lambda: True)
    monkeypatch.setattr(auth, "_failures", {})
    path = str(tmp_path / "hr.db")
    conn = db.open_db(path)
    db.insert(conn, "tenant", {"id": "acme_labs", "name": "Acme", "pack_path": "x",
                               "config_hash": "cfg", "created_at": db.now()})
    auth.create_account(conn, tenant_id="acme_labs", person_id="emp_021",
                        email="hana@acme.test", password=PASSWORD)
    conn.close()
    return TestClient(web.create_app(db_path=path))


def sign_in(client, password=PASSWORD):
    return client.post("/api/auth/login?tenant=acme_labs",
                       json={"email": "hana@acme.test", "password": password})


def test_signing_in_gives_a_session_the_dashboard_can_use(accounts):
    response = sign_in(accounts)
    assert response.status_code == 200
    headers = {"Authorization": f"Bearer {response.json()['token']}"}
    me = accounts.get("/api/auth/me?tenant=acme_labs", headers=headers).json()
    assert me["authenticated"] is True and me["id"] == "emp_021"
    assert accounts.get("/api/people?tenant=acme_labs", headers=headers).status_code == 200


def test_repeated_wrong_passwords_lock_the_account(accounts):
    for _ in range(auth.MAX_FAILURES):
        assert sign_in(accounts, "wrong password here").status_code == 401
    locked = sign_in(accounts)
    assert locked.status_code == 429, "even the right password waits out the lockout"
    assert int(locked.headers["retry-after"]) > 0


def test_explain_runs_as_the_signed_in_person_not_the_body(accounts):
    token = sign_in(accounts).json()["token"]
    response = accounts.post(
        "/api/explain?tenant=acme_labs", headers={"Authorization": f"Bearer {token}"},
        json={"message": "how much leave do I have", "requester": "emp_001"},
    )
    assert response.status_code == 200
    assert response.json()["context"]["requester"] == "emp_021"


def test_policy_search_cannot_be_scoped_to_someone_else(accounts):
    token = sign_in(accounts).json()["token"]
    other = auth.issue(tenant_id="meridian_group", person_id="mg_4001")
    response = accounts.get("/api/policies/search?tenant=acme_labs&q=leave&as_person=emp_001",
                            headers={"Authorization": f"Bearer {other}"})
    assert response.status_code == 403
    mine = accounts.get("/api/policies/search?tenant=acme_labs&q=leave&as_person=emp_001",
                        headers={"Authorization": f"Bearer {token}"})
    assert mine.status_code == 200


def test_the_documented_secret_name_is_honoured(monkeypatch):
    monkeypatch.delenv("HR_SESSION_SECRET", raising=False)
    monkeypatch.setenv("HR_AUTH_SECRET", "from-the-deploy-guide")
    assert auth.secret() == b"from-the-deploy-guide"


# ---------------------------------------------------------------- deciding once


@pytest.fixture
def prepared():
    conn, cfg = build("acme_labs")
    outcome = kernel.run(conn, cfg, message="Please change my home address to 14 Example Road, Leeds.",
                         requester_id="emp_001", on=TODAY)
    return conn, cfg, outcome


def test_a_decision_racing_another_cannot_act_twice(prepared, monkeypatch):
    """The loser of a race read the approval while it was still open."""
    conn, cfg, outcome = prepared
    approvals.decide(conn, cfg, approval_id=outcome.approval_id, actor_id="emp_021",
                     outcome="approved", note="first", on=TODAY)

    real_one = db.one

    def read_before_the_first_finished(conn, sql, params=()):
        row = real_one(conn, sql, params)
        if sql.startswith("SELECT * FROM approval") and row is not None:
            return {**dict(row), "status": "open"}
        return row

    monkeypatch.setattr(db, "one", read_before_the_first_finished)
    with pytest.raises(approvals.ApprovalError, match="already been decided"):
        approvals.decide(conn, cfg, approval_id=outcome.approval_id, actor_id="emp_020",
                         outcome="approved", note="second", on=TODAY)
    monkeypatch.undo()

    row = db.one(conn, "SELECT decided_by, note FROM approval WHERE id = ?", (outcome.approval_id,))
    assert (row["decided_by"], row["note"]) == ("emp_021", "first")
    approved = db.rows(conn, "SELECT * FROM audit WHERE work_item_id = ? AND action LIKE 'approved%'",
                       (outcome.work_item_id,))
    assert len(approved) == 1


def test_an_action_that_fails_leaves_the_approval_open(prepared, monkeypatch):
    conn, cfg, outcome = prepared

    def broken(self, plan_id, *, actor, authorised):
        raise tools.ToolError("the people system is down")

    monkeypatch.setattr(tools.Registry, "commit", broken)
    with pytest.raises(tools.ToolError):
        approvals.decide(conn, cfg, approval_id=outcome.approval_id, actor_id="emp_021",
                         outcome="approved", note="try", on=TODAY)
    monkeypatch.undo()

    assert db.one(conn, "SELECT status FROM approval WHERE id = ?", (outcome.approval_id,))["status"] == "open"
    result = approvals.decide(conn, cfg, approval_id=outcome.approval_id, actor_id="emp_021",
                              outcome="approved", note="again", on=TODAY)
    assert result.committed


def test_a_plan_that_fails_to_act_can_be_committed_later(prepared, monkeypatch):
    conn, cfg, outcome = prepared
    registry = tools.Registry(conn, cfg)
    plan = next(p for p in registry.plans_for(outcome.work_item_id) if p["status"] == "prepared")
    tool = registry.get(plan["adapter"])

    def down(*args, **kwargs):
        raise tools.ToolError("down")

    monkeypatch.setattr(type(tool), "act", down)
    with pytest.raises(tools.ToolError):
        registry.commit(plan["id"], actor="emp_021", authorised=True)
    monkeypatch.undo()

    assert db.one(conn, "SELECT status FROM action_plan WHERE id = ?", (plan["id"],))["status"] == "prepared"
    assert registry.commit(plan["id"], actor="emp_021", authorised=True)["already"] is False
    assert registry.commit(plan["id"], actor="emp_021", authorised=True)["already"] is True
