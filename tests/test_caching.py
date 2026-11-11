"""The caches must never serve stale answers.

Three things are cached now: the directory name index, the tokenised policy
corpus, and whether a database file's schema has been created. Each was a real
performance wall (131 ms to classify one message at 2,500 people). A cache that
returns yesterday's policy is a worse defect than the slowness it replaced, so
invalidation is tested harder than speed.
"""

from __future__ import annotations

import pytest

from hr_agent import classify, db, policy
from hr_agent.packs import loader

from .conftest_agent import build


@pytest.fixture(autouse=True)
def clean_caches():
    classify.forget_name_index()
    policy.forget_corpus()
    yield
    classify.forget_name_index()
    policy.forget_corpus()


# ---------------------------------------------------------------- name index


def test_two_companies_do_not_share_a_directory():
    acme = loader.load_resolved("acme_labs")
    meridian = loader.load_resolved("meridian_group")
    assert classify.classify("What is Priya's home address?", acme).entities.get("people") == ["emp_003"]
    assert "people" not in classify.classify("What is Priya's home address?", meridian).entities
    assert classify.classify("Ask Wei Lin Tan about it", meridian).entities.get("people") == ["mg_1001"]


def test_the_requester_is_never_matched_as_somebody_else():
    cfg = loader.load_resolved("acme_labs")
    found = classify.classify("Maya needs her address changed", cfg, requester_id="emp_001").entities
    assert "people" not in found


def test_the_longest_name_wins():
    """`Chan` must not beat `Chan Ka Wai` and resolve to the wrong person."""
    cfg = loader.load_resolved("meridian_group")
    assert classify.classify("please ask Chan Ka Wai", cfg).entities.get("people") == ["mg_1004"]


def test_a_changed_directory_is_seen_after_the_cache_is_dropped():
    cfg = loader.load_resolved("acme_labs")
    classify.classify("hello", cfg)                      # populate
    cfg.people = [p for p in cfg.people if p.id != "emp_003"]
    cfg.config_hash = "cfg_changed"                      # a real edit moves the hash
    assert "people" not in classify.classify("What is Priya's address?", cfg).entities


def test_the_index_is_only_built_once(monkeypatch):
    cfg = loader.load_resolved("acme_labs")
    built = []
    original = classify._name_index

    def counting(config, requester):
        if config.config_hash not in {c for c in built}:
            built.append(config.config_hash)
        return original(config, requester)

    monkeypatch.setattr(classify, "_name_index", counting)
    for _ in range(5):
        classify.classify("What is Priya's address?", cfg)
    assert len(built) == 1


# ---------------------------------------------------------------- policy corpus


def test_reloading_the_corpus_drops_the_cached_index():
    """The exact stale-cache risk: edit a policy, reload, still get the old text."""
    conn, cfg = build("acme_labs")
    before = policy.search(conn, cfg, "how many days of annual leave", person=cfg.people[0])
    assert "25 days" in before.citations[0].quoted_text

    conn.execute(
        "UPDATE policy_section SET body = ? WHERE tenant_id = ? AND doc_id = 'POL-LEAVE' AND section_id = '2'",
        ("Full-time employees receive 30 days of paid annual leave each calendar year.", cfg.tenant_id),
    )
    policy.forget_corpus(cfg.tenant_id)   # what load_corpus does

    after = policy.search(conn, cfg, "how many days of annual leave", person=cfg.people[0])
    assert "30 days" in after.citations[0].quoted_text


def test_load_corpus_invalidates_on_its_own():
    conn, cfg = build("acme_labs")
    policy.search(conn, cfg, "annual leave", person=cfg.people[0])      # populate
    conn.execute("UPDATE policy_section SET body = 'replaced entirely' WHERE tenant_id = ?", (cfg.tenant_id,))
    policy.load_corpus(conn, cfg)                                       # must clear
    result = policy.search(conn, cfg, "how many days of annual leave", person=cfg.people[0])
    assert "25 days" in result.citations[0].quoted_text, "load_corpus should have restored the real text"


def test_one_company_cannot_see_another_companys_corpus():
    conn_a, acme = build("acme_labs")
    conn_m, meridian = build("meridian_group")
    acme_hit = policy.search(conn_a, acme, "annual leave allowance", person=acme.people[0])
    meridian_hit = policy.search(conn_m, meridian, "annual leave", person=meridian.people[0])
    assert acme_hit.citations[0].doc_title == "Leave Policy"
    assert meridian_hit.citations[0].doc_title == "Group Leave Policy"


def test_scope_still_applies_through_the_cache():
    """The index covers the whole corpus; scoping happens per query, per person."""
    conn, cfg = build("meridian_group")
    sg = next(p for p in cfg.people if p.entity == "meridian_sg")
    hk = next(p for p in cfg.people if p.entity == "meridian_hk")
    assert policy.search(conn, cfg, "what must a new joiner submit", person=sg).citations[0].version == "2.0-sg"
    assert policy.search(conn, cfg, "what must a new joiner submit", person=hk).citations[0].version == "2.0-hk"


def test_a_retired_version_stays_invisible_through_the_cache():
    conn, cfg = build("acme_labs")
    for _ in range(3):
        result = policy.search(conn, cfg, "how many office days a week", person=cfg.people[0])
        remote = [c for c in result.citations if c.doc_id == "POL-REMOTE"]
        assert all(c.version == "2.0" for c in remote)


# ---------------------------------------------------------------- schema


def test_an_in_memory_database_is_always_created():
    """Each in-memory connection is its own database; caching would break them."""
    for _ in range(3):
        conn = db.open_db(":memory:")
        assert "work_item" in db.table_names(conn)
        conn.close()


def test_a_file_database_is_created_once_and_still_usable(tmp_path):
    path = tmp_path / "x.db"
    first = db.open_db(path)
    assert "work_item" in db.table_names(first)
    first.close()
    second = db.open_db(path)      # schema script skipped this time
    assert "work_item" in db.table_names(second)
    second.close()
