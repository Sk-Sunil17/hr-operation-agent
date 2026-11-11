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

# Serving a PUBLIC demo with the role switcher on.
#
# The role switcher lets anyone act as anyone in the directory, which is why the
# server refuses to bind to a public interface while it is on. For a hosted
# demo of fictional companies that refusal is the wrong answer -- but it must
# never be switched off by accident, or by a deployment platform's default
# environment, so acknowledging it means typing this exact sentence:
#
#   HR_PUBLIC_DEMO="anyone-can-act-as-anyone"
#
# A truthy value is deliberately NOT enough. Nobody sets this believing it
# means something else, and nobody sets it by copying a template.
PUBLIC_DEMO_ACK = "anyone-can-act-as-anyone"
PUBLIC_DEMO = os.environ.get("HR_PUBLIC_DEMO", "") == PUBLIC_DEMO_ACK

# Browser origins allowed to call this API. The dashboard is served from
# somewhere else in a hosted deployment, so this cannot be a constant.
CORS_ORIGINS = [
    o.strip() for o in os.environ.get(
        "HR_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",") if o.strip()
]


def ensure_dirs() -> None:
    VAR_DIR.mkdir(parents=True, exist_ok=True)
