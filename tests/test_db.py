"""The data model holds the shape the kernel depends on."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from hr_agent import db

EXPECTED_TABLES = {
    "tenant", "config_snapshot", "employee",
    "policy_doc", "policy_version", "policy_section",
    "work_item", "event", "message_log",
    "decision", "citation",
    "action_plan", "idempotency_ledger",
    "approval", "hr_case",
    "audit", "job_run", "metric_snapshot", "schema_meta",
}

# The columns that make the approval queue a labelling machine. They cannot be
# added later for work that has already happened, so their absence is a defect.
FLYWHEEL_COLUMNS = {
    "facts_json", "rule_id", "mode", "needs_citation", "rule_reason",
    "retrieval_json", "provider", "model_id", "model_params_json",
    "prompt_hash", "pack_hash", "draft_text", "verify_json", "verify_passed",
    "human_verdict", "human_actor_id", "human_reason_code", "human_note",
    "edit_diff", "decided_at",
}


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in db.rows(conn, f"PRAGMA table_info({table})")}


def test_every_table_is_created(conn):
    assert EXPECTED_TABLES <= set(db.table_names(conn))


def test_the_decision_table_can_carry_a_human_verdict(conn):
    assert FLYWHEEL_COLUMNS <= columns(conn, "decision")


def test_initialising_twice_is_harmless(conn):
    db.init(conn)
    db.init(conn)
    assert db.one(conn, "SELECT value FROM schema_meta WHERE key = 'version'")["value"] == db.SCHEMA_VERSION


def test_timestamps_are_utc_iso_seconds():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", db.now())


def test_a_row_cannot_belong_to_a_tenant_that_does_not_exist(conn):
    with pytest.raises(sqlite3.IntegrityError):
        db.insert(conn, "employee", {"tenant_id": "ghost", "id": "e1", "name": "Nobody"})


def test_json_columns_round_trip(conn):
    db.insert(conn, "tenant", {"id": "t", "name": "T", "pack_path": "/x", "config_hash": "cfg_1", "created_at": db.now()})
    db.insert(conn, "employee", {"tenant_id": "t", "id": "e1", "name": "A", "roles": ["hr_lead"], "segment_keys": []})
    row = db.one(conn, "SELECT * FROM employee WHERE id = 'e1'")
    assert db.loads(row["roles"]) == ["hr_lead"]
    assert db.loads(row["segment_keys"]) == []
    assert db.loads(None, default={}) == {}


def test_an_idempotency_key_cannot_be_reused_in_one_tenant(conn):
    db.insert(conn, "tenant", {"id": "t", "name": "T", "pack_path": "/x", "config_hash": "cfg_1", "created_at": db.now()})
    db.insert(conn, "work_item", {"id": "w1", "tenant_id": "t", "channel": "web", "status": "open", "config_hash": "cfg_1", "created_at": db.now()})
    plan = {
        "tenant_id": "t", "work_item_id": "w1", "adapter": "notify", "operation": "commit_message",
        "args_json": "{}", "preview_text": "hello", "idempotency_key": "k-1", "status": "prepared",
        "prepared_at": db.now(),
    }
    db.insert(conn, "action_plan", {"id": "p1", **plan})
    with pytest.raises(sqlite3.IntegrityError):
        db.insert(conn, "action_plan", {"id": "p2", **plan})


def test_no_update_or_delete_statements_exist_against_the_audit_table():
    """The audit trail is append-only. This test is the enforcement."""
    offenders = []
    for path in Path("hr_agent").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for statement in re.findall(r"(?is)\b(update\s+audit\b|delete\s+from\s+audit\b)", text):
            offenders.append(f"{path}: {statement}")
    assert offenders == []
