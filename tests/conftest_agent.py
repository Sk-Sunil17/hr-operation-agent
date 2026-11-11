"""Helpers for tests that need a running agent."""

from __future__ import annotations

from datetime import date

from hr_agent import db, policy
from hr_agent.packs import loader

TODAY = date(2026, 11, 2)


def build(tenant_id: str):
    """A fresh in-memory world for one company: directory, corpus, config."""
    conn = db.open_db(":memory:")
    cfg = loader.load_resolved(tenant_id)
    db.insert(conn, "tenant", {
        "id": tenant_id, "name": cfg.manifest.name, "pack_path": "packs",
        "config_hash": cfg.config_hash, "active": 1, "created_at": db.now(),
    })
    for person in cfg.people:
        db.insert(conn, "employee", {
            "tenant_id": tenant_id, "id": person.id, "name": person.name, "email": person.email,
            "manager_id": person.manager_id, "entity": person.entity,
            "employment_type": person.employment_type, "segment_keys": person.segment_keys,
            "roles": person.roles, "timezone": person.timezone, "start_date": person.start_date,
            "end_date": person.end_date, "active": int(person.active),
        })
    policy.load_corpus(conn, cfg)
    return conn, cfg
