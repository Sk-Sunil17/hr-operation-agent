"""Where things live, and the few settings that are not per-tenant.

Anything that varies by company belongs in a pack, not here.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PACKS_DIR = Path(os.environ.get("HR_PACKS_DIR", ROOT / "packs"))
BASE_PACK_DIR = PACKS_DIR / "base"
TENANTS_DIR = PACKS_DIR / "tenants"

VAR_DIR = Path(os.environ.get("HR_VAR_DIR", ROOT / "var"))
DB_PATH = Path(os.environ.get("HR_DB_PATH", VAR_DIR / "hr.db"))

# Redaction is not optional. The kernel refuses to run with it off; the setting
# exists only so a test can prove the refusal happens.
REDACTION_ENABLED = os.environ.get("HR_REDACTION", "on").lower() not in {"off", "0", "false"}


def ensure_dirs() -> None:
    VAR_DIR.mkdir(parents=True, exist_ok=True)
