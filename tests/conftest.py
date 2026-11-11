"""Shared fixtures.

Tests run against copies of the real shipped packs. A test that wants to prove a
rule is enforced edits the copy and asserts the loader refuses it, rather than
asserting against a hand-built fixture that could drift from reality.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from hr_agent import config, db

REAL_PACKS = config.PACKS_DIR
TENANTS = ("acme_labs", "meridian_group")


@pytest.fixture
def packs(tmp_path: Path) -> Path:
    """A writable copy of the shipped packs."""
    target = tmp_path / "packs"
    shutil.copytree(REAL_PACKS, target)
    return target


@pytest.fixture
def conn():
    conn = db.open_db(":memory:")
    yield conn
    conn.close()


def write_yaml(path: Path, data: Any) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def edit_yaml(path: Path, mutate) -> None:
    """Load a pack file, let the test change it, write it back."""
    data = read_yaml(path)
    mutate(data)
    write_yaml(path, data)
