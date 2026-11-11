"""Sign-in, and pagination.

The question authentication answers here is narrow: which person in the company
directory is making this request. What they may then do is already decided by
the roles the directory gives them, which is why no role is stored alongside a
password.
"""

from __future__ import annotations

import warnings

import pytest

from hr_agent import auth, db

warnings.filterwarnings("ignore", category=DeprecationWarning)

PASSWORD = "correct horse battery"


@pytest.fixture
def conn():
    conn = db.open_db(":memory:")
    db.insert(conn, "tenant", {
        "id": "acme_labs", "name": "Acme", "pack_path": "x", "config_hash": "cfg_1",
        "created_at": db.now(),
    })
    auth.create_account(conn, tenant_id="acme_labs", person_id="emp_021",
                        email="hana@acme.test", password=PASSWORD)
    yield conn
    conn.close()


# ---------------------------------------------------------------- passwords


def test_a_password_is_never_stored_in_the_clear(conn):
    row = db.one(conn, "SELECT * FROM account WHERE person_id = 'emp_021'")
    assert PASSWORD not in row["password_hash"]
    assert row["salt"] and row["salt"] not in row["password_hash"]


def test_the_same_password_hashes_differently_for_two_people(conn):
    auth.create_account(conn, tenant_id="acme_labs", person_id="emp_020",
                        email="joel@acme.test", password=PASSWORD)
    hashes = {r["password_hash"] for r in db.rows(conn, "SELECT password_hash FROM account")}
    assert len(hashes) == 2, "a shared salt would make two identical passwords look identical"


def test_a_short_password_is_refused(conn):
    with pytest.raises(auth.AuthError, match="at least"):
        auth.create_account(conn, tenant_id="acme_labs", person_id="emp_001",
                            email="maya@acme.test", password="short")


def test_sign_in_works_and_is_case_insensitive_on_email(conn):
    assert auth.sign_in(conn, tenant_id="acme_labs", email="Hana@Acme.TEST", password=PASSWORD)


@pytest.mark.parametrize(
    ("email", "password"),
    [("hana@acme.test", "wrong password here"), ("nobody@acme.test", PASSWORD)],
    ids=["wrong-password", "no-such-account"],
)
def test_a_failure_never_says_which_half_was_wrong(conn, email, password):
    """Telling someone the email exists is telling them half the answer."""
    with pytest.raises(auth.AuthError) as first:
        auth.sign_in(conn, tenant_id="acme_labs", email=email, password=password)
    assert "do not match an account" in str(first.value)


# ---------------------------------------------------------------- sessions


def test_a_session_names_the_person_and_the_company(conn):
    session = auth.read(auth.sign_in(conn, tenant_id="acme_labs", email="hana@acme.test", password=PASSWORD))
    assert session.person_id == "emp_021"
    assert session.tenant_id == "acme_labs"


def test_a_tampered_token_is_refused(conn):
    token = auth.sign_in(conn, tenant_id="acme_labs", email="hana@acme.test", password=PASSWORD)
    with pytest.raises(auth.AuthError, match="tampered"):
        auth.read(token[:-4] + "aaaa")


def test_a_forged_token_is_refused():
    """Signed, not just encoded: a readable payload is not a valid one."""
    import base64
    import json

    body = json.dumps({"t": "acme_labs", "p": "emp_020", "x": 9999999999}).encode()
    forged = base64.urlsafe_b64encode(body).decode().rstrip("=") + ".notasignature"
    with pytest.raises(auth.AuthError):
        auth.read(forged)


def test_an_expired_session_is_refused():
    with pytest.raises(auth.AuthError, match="expired"):
        auth.read(auth.issue(tenant_id="acme_labs", person_id="emp_021", seconds=-1))


def test_no_role_is_stored_with_the_account(conn):
    """Roles live in the directory. A second copy would drift from the first."""
    columns = {r["name"] for r in db.rows(conn, "PRAGMA table_info(account)")}
    assert not (columns & {"role", "roles", "is_admin", "permissions"})


# ---------------------------------------------------------------- the API


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from hr_agent.web import create_app

    return TestClient(create_app())


def test_role_switching_is_only_on_while_auth_is_off(client):
    health = client.get("/api/health").json()
    assert health["demo"]["role_switching"] is not health["auth"]["enabled"]


def test_signing_in_is_refused_when_auth_is_off(client):
    response = client.post("/api/auth/login?tenant=acme_labs",
                           json={"email": "x@y.test", "password": PASSWORD})
    assert response.status_code == 400
    assert "demo role switcher" in response.json()["detail"]


def test_who_rejects_a_session_for_another_company(monkeypatch):
    """A valid token must not be usable sideways."""
    from fastapi.testclient import TestClient

    from hr_agent import web

    monkeypatch.setattr(auth, "enabled", lambda: True)
    client = TestClient(web.create_app())
    token = auth.issue(tenant_id="meridian_group", person_id="mg_4001")
    response = client.get("/api/queue?tenant=acme_labs", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_an_unsigned_request_is_refused_when_auth_is_on(monkeypatch):
    from fastapi.testclient import TestClient

    from hr_agent import web

    monkeypatch.setattr(auth, "enabled", lambda: True)
    client = TestClient(web.create_app())
    assert client.get("/api/queue?tenant=acme_labs").status_code == 401


def test_a_body_cannot_choose_who_approved_when_auth_is_on(monkeypatch):
    """The one thing the queue exists to prevent."""
    from fastapi.testclient import TestClient

    from hr_agent import web

    monkeypatch.setattr(auth, "enabled", lambda: True)
    client = TestClient(web.create_app())
    token = auth.issue(tenant_id="acme_labs", person_id="emp_001")
    response = client.post(
        "/api/approvals/appr_nope/decide?tenant=acme_labs",
        json={"outcome": "approved", "actor": "emp_021"},     # claiming to be somebody else
        headers={"Authorization": f"Bearer {token}"},
    )
    # Rejected as the signed-in person (who cannot decide it), never as emp_021.
    assert response.status_code in (404, 409)
    assert "emp_021" not in response.text


# ---------------------------------------------------------------- pagination


def test_a_page_carries_a_cursor_to_the_next_one(client):
    first = client.get("/api/work-items?tenant=acme_labs&limit=3").json()
    assert len(first["items"]) <= 3
    if first["nextCursor"]:
        second = client.get(
            f"/api/work-items?tenant=acme_labs&limit=3&before={first['nextCursor']}"
        ).json()
        assert not ({i["id"] for i in first["items"]} & {i["id"] for i in second["items"]})


def test_the_last_page_has_no_cursor(client):
    page = client.get("/api/work-items?tenant=acme_labs&limit=200").json()
    assert page["nextCursor"] is None


def test_an_unreadable_cursor_is_a_bad_request_not_a_crash(client):
    assert client.get("/api/work-items?tenant=acme_labs&before=nonsense").status_code == 400
