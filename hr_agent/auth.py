"""Sign-in, for when the demo role switcher is off.

Deliberately small. What someone may do is already decided by the roles the
company directory gives them, so this answers one question only: which person
in that directory is making this request.

Three choices worth stating:

  * Roles are NOT stored here. Duplicating them would create a second place to
    keep in step with the pack, and the two would drift.
  * A session is a signed token rather than a row, so signing out is a client
    concern and there is no table to grow unbounded. The trade is that a token
    cannot be revoked before it expires; with short lifetimes that is the right
    side of the trade for this, and it is written down rather than discovered.
  * Passwords use scrypt from the standard library. Not because it is the best
    available, but because it is available everywhere this runs and needs no
    dependency anyone has to remember to install.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass

from . import db

SESSION_SECONDS = 8 * 60 * 60        # one working day
SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}
MIN_PASSWORD = 12


class AuthError(RuntimeError):
    """Sign-in failed, or a session is not usable."""


def secret() -> bytes:
    """The key sessions are signed with.

    From the environment in any real deployment. The generated fallback is
    per-process on purpose: restarting invalidates every session, which is the
    safe failure, and it makes a missing secret obvious rather than silent.
    """
    configured = os.environ.get("HR_SESSION_SECRET")
    if configured:
        return configured.encode("utf-8")
    global _EPHEMERAL
    if _EPHEMERAL is None:
        _EPHEMERAL = secrets.token_bytes(32)
    return _EPHEMERAL


_EPHEMERAL: bytes | None = None


def enabled() -> bool:
    """Auth is opt-in: the demo runs without it, bound to localhost only."""
    return os.environ.get("HR_AUTH", "off").lower() in {"on", "1", "true"}


# ---------------------------------------------------------------- passwords


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"a password must be at least {MIN_PASSWORD} characters")
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt.encode("utf-8"), **SCRYPT)
    return digest.hex(), salt


def verify_password(password: str, *, stored: str, salt: str) -> bool:
    try:
        candidate, _ = hash_password(password, salt)
    except AuthError:
        # A too-short password is simply wrong, not an error to report back:
        # saying "your password is too short" to someone guessing is a hint.
        return False
    return hmac.compare_digest(candidate, stored)


# ---------------------------------------------------------------- accounts


def create_account(
    conn: sqlite3.Connection, *, tenant_id: str, person_id: str, email: str, password: str
) -> None:
    digest, salt = hash_password(password)
    conn.execute(
        "INSERT INTO account (tenant_id, person_id, email, password_hash, salt, created_at) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(tenant_id, person_id) DO UPDATE SET "
        "email = excluded.email, password_hash = excluded.password_hash, salt = excluded.salt",
        (tenant_id, person_id, email.lower().strip(), digest, salt, db.now()),
    )


def sign_in(conn: sqlite3.Connection, *, tenant_id: str, email: str, password: str) -> str:
    """Return a session token, or raise. The message never says which half was wrong."""
    row = db.one(
        conn, "SELECT * FROM account WHERE tenant_id = ? AND email = ?",
        (tenant_id, email.lower().strip()),
    )
    wrong = AuthError("that email and password do not match an account")
    if row is None:
        # Spend the time anyway, so a missing account is not faster than a
        # wrong password and cannot be told apart by timing it.
        hash_password("x" * MIN_PASSWORD)
        raise wrong
    if not verify_password(password, stored=row["password_hash"], salt=row["salt"]):
        raise wrong
    conn.execute(
        "UPDATE account SET last_seen = ? WHERE tenant_id = ? AND person_id = ?",
        (db.now(), tenant_id, row["person_id"]),
    )
    return issue(tenant_id=tenant_id, person_id=row["person_id"])


# ---------------------------------------------------------------- sessions


@dataclass(frozen=True)
class Session:
    tenant_id: str
    person_id: str
    expires_at: int


def _sign(payload: bytes) -> str:
    return base64.urlsafe_b64encode(hmac.new(secret(), payload, hashlib.sha256).digest()).decode().rstrip("=")


def issue(*, tenant_id: str, person_id: str, seconds: int = SESSION_SECONDS) -> str:
    body = json.dumps(
        {"t": tenant_id, "p": person_id, "x": int(time.time()) + seconds}, sort_keys=True
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(body).decode().rstrip("=")
    return f"{encoded}.{_sign(body)}"


def read(token: str) -> Session:
    """Validate a token and return who it is. Raises on anything suspect."""
    try:
        encoded, signature = token.split(".", 1)
        body = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except Exception as exc:
        raise AuthError("that session token is not readable") from exc
    if not hmac.compare_digest(_sign(body), signature):
        raise AuthError("that session token has been tampered with")
    data = json.loads(body)
    if int(data["x"]) < int(time.time()):
        raise AuthError("that session has expired; sign in again")
    return Session(tenant_id=data["t"], person_id=data["p"], expires_at=int(data["x"]))
