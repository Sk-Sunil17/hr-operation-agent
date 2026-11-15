"""How HR adjusts the rules from the dashboard, within limits the pack sets.

The pack is the floor. Nothing here can make a rule looser than the pack makes
it, so the guarantees the pack carries -- restricted matters go to a person,
records change only with approval -- cannot be undone from a screen.

Within that floor:

  * Anyone in HR may make a rule stricter, and it applies at once. Stricter is
    always the safe direction, so it should never wait for anybody.
  * Relaxing a rule back towards the pack needs a second HR person: not the one
    who tightened it. One person cannot quietly open what they closed.
  * Every change carries a note and lands in the audit trail.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import audit, db
from .packs.loader import config_hash
from .packs.models import MODES, ResolvedConfig, restriction
from .redact import Redactor


class RuleChangeError(RuntimeError):
    """The change cannot be made as asked."""


def settings(conn: sqlite3.Connection, tenant_id: str) -> dict[str, dict[str, Any]]:
    return {
        r["rule_id"]: dict(r)
        for r in db.rows(conn, "SELECT * FROM rule_setting WHERE tenant_id = ?", (tenant_id,))
    }


_applied: dict[tuple[str, tuple[tuple[str, str], ...]], ResolvedConfig] = {}


def apply(conn: sqlite3.Connection, cfg: ResolvedConfig) -> ResolvedConfig:
    """The pack's configuration with HR's settings on top, fingerprinted again."""
    current = tuple(sorted((rule_id, s["mode"]) for rule_id, s in settings(conn, cfg.tenant_id).items()))
    if not current:
        return cfg
    key = (cfg.config_hash, current)
    if key not in _applied:
        provenance = {**cfg.provenance, **{f"rules.{rule_id}.mode": "hr" for rule_id, _ in current}}
        adjusted = cfg.model_copy(update={"rule_settings": dict(current), "provenance": provenance})
        adjusted.config_hash = config_hash(adjusted)
        _applied[key] = adjusted
    return _applied[key]


def may_change(cfg: ResolvedConfig, actor_id: str | None) -> bool:
    person = next((p for p in cfg.people if p.id == actor_id and p.active), None)
    return bool(person and set(person.roles) & set(cfg.approvals.hr_roles))


def change(
    conn: sqlite3.Connection,
    cfg: ResolvedConfig,
    *,
    rule_id: str,
    mode: str,
    actor_id: str,
    note: str,
) -> dict[str, Any]:
    """Set one rule's mode. `cfg` is the pack's configuration, without settings."""
    rule = cfg.rule(rule_id)
    if rule is None:
        raise RuleChangeError(f"There is no rule {rule_id!r}.")
    if mode not in MODES:
        raise RuleChangeError(f"Unknown mode {mode!r}; expected one of {', '.join(MODES)}.")
    if not may_change(cfg, actor_id):
        raise RuleChangeError("Only someone in an HR role may change how the agent handles requests.")
    note = (note or "").strip()
    if len(note) < 5:
        raise RuleChangeError("Say why, in a few words: the note is what an auditor reads later.")
    if restriction(mode) < restriction(rule.mode):
        raise RuleChangeError(
            f"The company rulebook sets this to {rule.mode!r} at the least, and nothing on this "
            "screen can make it looser than that."
        )

    existing = settings(conn, cfg.tenant_id).get(rule_id)
    current = existing["mode"] if existing else rule.mode
    if mode == current:
        raise RuleChangeError("That is already how this rule is set.")
    relaxing = restriction(mode) < restriction(current)
    if relaxing and existing and existing["changed_by"] == actor_id:
        raise RuleChangeError(
            "You made this rule stricter, so a second HR person has to relax it. "
            "One person cannot reopen what they closed."
        )

    if mode == rule.mode:
        conn.execute("DELETE FROM rule_setting WHERE tenant_id = ? AND rule_id = ?", (cfg.tenant_id, rule_id))
    else:
        conn.execute(
            "INSERT INTO rule_setting (tenant_id, rule_id, mode, note, changed_by, changed_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(tenant_id, rule_id) DO UPDATE SET "
            "mode = excluded.mode, note = excluded.note, changed_by = excluded.changed_by, "
            "changed_at = excluded.changed_at",
            (cfg.tenant_id, rule_id, mode, note, actor_id, db.now()),
        )

    adjusted = apply(conn, cfg)
    audit.append(
        conn, tenant_id=cfg.tenant_id, actor=actor_id, actor_kind="person", stage="decide",
        action=f"rule {rule_id} {'relaxed' if relaxing else 'tightened'} from {current} to {mode}",
        redactor=Redactor(cfg.redaction),
        detail={"rule_id": rule_id, "from": current, "to": mode, "pack_mode": rule.mode, "note": note},
        config_hash=adjusted.config_hash,
    )
    return {"rule_id": rule_id, "from": current, "to": mode, "relaxed": relaxing, "config_hash": adjusted.config_hash}


def with_setting(cfg: ResolvedConfig, rule_id: str, mode: str) -> ResolvedConfig:
    """A what-if copy with one rule set differently. Writes nothing."""
    rule = cfg.rule(rule_id)
    chosen = {k: v for k, v in cfg.rule_settings.items() if k != rule_id}
    if rule is not None and restriction(mode) > restriction(rule.mode):
        chosen[rule_id] = mode
    adjusted = cfg.model_copy(update={"rule_settings": chosen})
    adjusted.config_hash = config_hash(adjusted)
    return adjusted
