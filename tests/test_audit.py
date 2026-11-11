"""The audit trail. A log you can edit is not evidence.

These tests are the difference between the claim and the property: they tamper
with stored rows and require that the chain says so, naming the row and
distinguishing an edit from a removal.
"""

from __future__ import annotations

import pytest

from hr_agent import audit, config, db, redact
from hr_agent.packs import loader

TENANTS = ("acme_labs", "meridian_group")


@pytest.fixture
def world(conn):
    """A database with both companies registered, and a redactor for each."""
    configs = {t: loader.load_resolved(t) for t in TENANTS}
    for tenant_id, cfg in configs.items():
        db.insert(
            conn,
            "tenant",
            {
                "id": tenant_id,
                "name": cfg.manifest.name,
                "pack_path": "packs/tenants/" + tenant_id,
                "config_hash": cfg.config_hash,
                "created_at": db.now(),
            },
        )
    return conn, configs, {t: redact.for_tenant(c) for t, c in configs.items()}


def write(conn, tenant_id, redactor, cfg, **overrides):
    return audit.append(
        conn,
        tenant_id=tenant_id,
        actor=overrides.pop("actor", "agent"),
        actor_kind=overrides.pop("actor_kind", "agent"),
        stage=overrides.pop("stage", "receive"),
        action=overrides.pop("action", "message received"),
        redactor=redactor,
        config_hash=cfg.config_hash,
        **overrides,
    )


# ---------------------------------------------------------------- the chain


def test_an_empty_trail_verifies(world):
    conn, _, _ = world
    result = audit.verify(conn)
    assert result.ok and result.checked == 0


def test_a_chain_verifies_and_links(world):
    conn, configs, redactors = world
    for _ in range(4):
        write(conn, "acme_labs", redactors["acme_labs"], configs["acme_labs"])
    rows = db.rows(conn, "SELECT * FROM audit ORDER BY seq")
    assert rows[0]["prev_hash"] is None
    assert [r["prev_hash"] for r in rows[1:]] == [r["row_hash"] for r in rows[:-1]]
    assert audit.verify(conn).ok


def test_each_tenant_has_its_own_chain(world):
    """Interleaved writes must not braid two companies' trails together."""
    conn, configs, redactors = world
    for _ in range(3):
        for tenant_id in TENANTS:
            write(conn, tenant_id, redactors[tenant_id], configs[tenant_id])
    for tenant_id in TENANTS:
        rows = db.rows(conn, "SELECT * FROM audit WHERE tenant_id = ? ORDER BY seq", (tenant_id,))
        assert rows[0]["prev_hash"] is None
        assert [r["prev_hash"] for r in rows[1:]] == [r["row_hash"] for r in rows[:-1]]
    assert audit.verify(conn).ok
    assert audit.verify(conn, "acme_labs").checked == 3


def test_an_edited_row_breaks_the_chain(world):
    conn, configs, redactors = world
    for _ in range(4):
        write(conn, "acme_labs", redactors["acme_labs"], configs["acme_labs"])
    conn.execute("UPDATE audit SET action = 'nothing happened' WHERE seq = 3")
    result = audit.verify(conn)
    assert not result.ok
    assert result.broken_at == 3
    assert "changed since it was written" in result.reason


def test_an_edited_detail_breaks_the_chain(world):
    conn, configs, redactors = world
    write(conn, "acme_labs", redactors["acme_labs"], configs["acme_labs"], detail={"body": "the original"})
    conn.execute("UPDATE audit SET detail_json = '{\"body\":\"something else\"}' WHERE seq = 1")
    assert not audit.verify(conn).ok


def test_a_deleted_row_breaks_the_chain(world):
    conn, configs, redactors = world
    for _ in range(4):
        write(conn, "acme_labs", redactors["acme_labs"], configs["acme_labs"])
    conn.execute("DELETE FROM audit WHERE seq = 3")
    result = audit.verify(conn)
    assert not result.ok
    assert "removed or reordered" in result.reason


def test_a_removed_last_row_is_not_detectable_and_that_is_stated(world):
    """An honest limit: truncating the end of a chain leaves a valid chain.

    Detecting that needs an external anchor (a published head, or an append-only
    store). Until there is one, the limitation is recorded here rather than
    implied away.
    """
    conn, configs, redactors = world
    for _ in range(4):
        write(conn, "acme_labs", redactors["acme_labs"], configs["acme_labs"])
    conn.execute("DELETE FROM audit WHERE seq = 4")
    assert audit.verify(conn).ok


def test_two_identical_actions_still_get_different_hashes(world):
    conn, configs, redactors = world
    first = write(conn, "acme_labs", redactors["acme_labs"], configs["acme_labs"], action="same")
    second = write(conn, "acme_labs", redactors["acme_labs"], configs["acme_labs"], action="same")
    assert first != second  # the chain position is part of the hash


# ---------------------------------------------------------------- redaction


def test_detail_is_redacted_before_it_is_stored(world):
    conn, configs, redactors = world
    write(
        conn,
        "acme_labs",
        redactors["acme_labs"],
        configs["acme_labs"],
        detail={"body": "my email is maya@acme.test and my ref is PR-456123", "bank_details": "12345678"},
    )
    stored = db.one(conn, "SELECT detail_json FROM audit WHERE seq = 1")["detail_json"]
    assert "maya@acme.test" not in stored
    assert "PR-456123" not in stored
    assert "12345678" not in stored
    assert "[email]" in stored and "[dropped]" in stored


def test_a_row_cannot_be_written_with_redaction_disabled(world, monkeypatch):
    conn, configs, redactors = world
    monkeypatch.setattr(config, "REDACTION_ENABLED", False)
    with pytest.raises(redact.RedactionDisabled):
        write(conn, "acme_labs", redactors["acme_labs"], configs["acme_labs"])
    assert audit.count(conn) == 0


# ---------------------------------------------------------------- shape


@pytest.mark.parametrize("stage", audit.STAGES)
def test_every_stage_is_accepted(world, stage):
    conn, configs, redactors = world
    write(conn, "acme_labs", redactors["acme_labs"], configs["acme_labs"], stage=stage)
    assert audit.verify(conn).ok


def test_an_unknown_stage_is_refused(world):
    conn, configs, redactors = world
    with pytest.raises(ValueError, match="unknown audit stage"):
        write(conn, "acme_labs", redactors["acme_labs"], configs["acme_labs"], stage="vibes")
    assert audit.count(conn) == 0


def test_an_unknown_actor_kind_is_refused(world):
    conn, configs, redactors = world
    with pytest.raises(ValueError, match="unknown actor kind"):
        write(conn, "acme_labs", redactors["acme_labs"], configs["acme_labs"], actor_kind="robot")


def test_the_configuration_behind_a_row_is_recorded(world):
    """Which configuration produced this: the claim in the plan, as a column."""
    conn, configs, redactors = world
    write(conn, "acme_labs", redactors["acme_labs"], configs["acme_labs"])
    row = db.one(conn, "SELECT config_hash FROM audit WHERE seq = 1")
    assert row["config_hash"] == configs["acme_labs"].config_hash


def test_a_refusal_is_audited(world):
    """A decision not to act is a decision, and is recorded like any other."""
    conn, configs, redactors = world
    write(
        conn,
        "acme_labs",
        redactors["acme_labs"],
        configs["acme_labs"],
        stage="decide",
        actor="rule:W1",
        actor_kind="rule",
        action="refused: the request is about another person's record",
        work_item_id="w1",
    )
    row = db.one(conn, "SELECT * FROM audit WHERE seq = 1")
    assert row["actor_kind"] == "rule" and row["actor"] == "rule:W1"
    assert "refused" in row["action"]


def test_the_trail_for_one_work_item_is_in_order(world):
    conn, configs, redactors = world
    for stage in ("receive", "understand", "decide", "verify", "act"):
        write(conn, "acme_labs", redactors["acme_labs"], configs["acme_labs"], stage=stage, work_item_id="w1")
    write(conn, "acme_labs", redactors["acme_labs"], configs["acme_labs"], work_item_id="w2")
    trail = audit.trail(conn, "acme_labs", "w1")
    assert [r["stage"] for r in trail] == ["receive", "understand", "decide", "verify", "act"]


def test_the_stages_match_the_ones_the_ui_draws():
    """The trail and the work-item rail must not drift apart.

    Retrieve precedes decide, which is the real order: `policy_found` is a fact
    the rule engine tests, so the read-only probe has to run first.
    """
    assert audit.STAGES[:4] == ("receive", "understand", "retrieve", "decide")
    assert audit.STAGES.index("draft") < audit.STAGES.index("verify") < audit.STAGES.index("act")
    assert "handover" in audit.STAGES
