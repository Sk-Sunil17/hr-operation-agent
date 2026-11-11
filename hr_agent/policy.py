"""The policy corpus: versioned, effective-dated, scoped, and citable.

Three things make this different from pointing a search index at an intranet:

  * Only a version whose status is `in_force` and whose dates cover today may
    ever be quoted. A retired or scheduled version is invisible to retrieval.
  * Scope resolves which version applies to a given person. Two versions in
    force for one policy with no scope is a data fault, and the agent refuses to
    answer rather than choosing one.
  * A citation carries the document, the version, the section and the exact
    words used, so the verify gate can check the draft really quotes them.

Retrieval is lexical (BM25 over sections). That is a feature here: an auditor can
be told why a passage matched.
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from . import db
from .packs.loader import tenant_dir
from .packs.models import PolicyDecl, Person, PolicyVersionDecl, ResolvedConfig

# ---------------------------------------------------------------- tokenising

STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "of", "to", "in", "on", "at", "for", "with", "from",
    "by", "as", "is", "are", "was", "were", "be", "been", "being", "do", "does", "did", "have", "has",
    "had", "i", "me", "my", "we", "our", "you", "your", "he", "she", "it", "they", "them", "their",
    "this", "that", "these", "those", "there", "here", "what", "when", "where", "which", "who", "whom",
    "how", "why", "can", "could", "may", "might", "will", "would", "shall", "should", "must", "not",
    "no", "any", "some", "all", "each", "please", "thanks", "thank", "hi", "hello", "get", "got",
    "need", "want", "know", "tell", "say", "said", "about", "into", "than", "then", "so", "up", "out",
}

# Question frames carry no meaning. "How many days do I get" is about days.
FRAMES = (
    r"^\s*(?:hi|hello|hey)\b[,.! ]*",
    r"\b(?:could|can|would) you (?:please )?(?:tell me|let me know|confirm)\b",
    r"\bi (?:would like to|want to|need to) know\b",
    r"\bplease (?:could you|can you)\b",
)


def _stem(word: str) -> str:
    """A deliberately small stemmer. Enough to join days/day and entitled/entitlement."""
    for suffix in ("ements", "ement", "ations", "ation", "ings", "ing", "ies", "ied", "ers", "er", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def tokenise(text: str) -> list[str]:
    lowered = text.lower()
    for frame in FRAMES:
        lowered = re.sub(frame, " ", lowered)
    words = re.findall(r"[a-z][a-z'-]{1,}", lowered)
    return [_stem(w) for w in words if w not in STOPWORDS and len(w) > 2]


# ---------------------------------------------------------------- parsing


@dataclass
class ParsedSection:
    section_id: str
    heading: str
    body: str
    ordinal: int


HEADING = re.compile(r"^##\s+(?:(\d+(?:\.\d+)*)\.?\s+)?(.+?)\s*$", re.M)


def parse_markdown(text: str) -> list[ParsedSection]:
    """`## 3. Requesting annual leave` becomes a citable section with id `3`.

    Text before the first heading is kept as a preamble section rather than lost,
    because policies often carry their purpose there.
    """
    matches = list(HEADING.finditer(text))
    sections: list[ParsedSection] = []

    preamble = text[: matches[0].start()] if matches else text
    preamble = re.sub(r"^#\s+.*$", "", preamble, flags=re.M).strip()
    if preamble:
        sections.append(ParsedSection("0", "Preamble", preamble, 0))

    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end].strip()
        sections.append(
            ParsedSection(
                section_id=match.group(1) or str(index + 1),
                heading=match.group(2).strip(),
                body=body,
                ordinal=len(sections),
            )
        )
    return sections


def load_corpus(conn: sqlite3.Connection, cfg: ResolvedConfig) -> int:
    """Read a company's policy files into the database. Idempotent."""
    root = tenant_dir(cfg.tenant_id) / "policies"
    forget_corpus(cfg.tenant_id)
    conn.execute("DELETE FROM policy_section WHERE tenant_id = ?", (cfg.tenant_id,))
    conn.execute("DELETE FROM policy_version WHERE tenant_id = ?", (cfg.tenant_id,))
    conn.execute("DELETE FROM policy_doc WHERE tenant_id = ?", (cfg.tenant_id,))

    sections = 0
    for policy in cfg.policies:
        db.insert(conn, "policy_doc", {
            "tenant_id": cfg.tenant_id, "id": policy.id, "title": policy.title,
            "owner_role": policy.owner_role, "review_months": policy.review_months,
        })
        for version in policy.versions:
            db.insert(conn, "policy_version", {
                "tenant_id": cfg.tenant_id, "doc_id": policy.id, "version": version.version,
                "status": version.status, "effective_from": version.effective_from,
                "effective_to": version.effective_to, "scope_json": version.scope,
                "published_at": None, "published_by": None, "source_sha": None,
            })
            for section in parse_markdown((root / version.file).read_text(encoding="utf-8")):
                db.insert(conn, "policy_section", {
                    "tenant_id": cfg.tenant_id, "doc_id": policy.id, "version": version.version,
                    "section_id": section.section_id, "heading": section.heading,
                    "body": section.body, "ordinal": section.ordinal,
                })
                sections += 1
    return sections


# ---------------------------------------------------------------- in force


def scope_matches(scope: dict[str, Any], person: Person | None) -> bool:
    """A version with no scope applies to everyone. A scoped one must match."""
    if not scope:
        return True
    if person is None:
        return False
    for key, expected in scope.items():
        actual = getattr(person, key, None)
        if isinstance(actual, list):
            if expected not in actual:
                return False
        elif actual != expected:
            return False
    return True


# A version that is approved but not yet live. It becomes quotable on its own
# effective date, without anyone having to remember to change its status --
# which is what an effective date is for.
LIVE_STATUSES = {"in_force", "scheduled"}


def in_force(policy: PolicyDecl, *, on: date, person: Person | None) -> list[PolicyVersionDecl]:
    """Versions of one policy that may be quoted on a given day, for this person.

    Dates decide, not statuses. A `scheduled` version becomes quotable the day
    it takes effect, and the version it replaces stops being quotable the same
    day -- without anyone editing `effective_to` on the old one. Requiring a
    human to flip a status on the morning a policy lands is exactly the kind of
    manual step that leaves an agent quoting last year's rules.

    `draft` and `retired` are never quotable whatever their dates say: those are
    deliberate human decisions, not a function of the calendar.

    Returning more than one version means the data is genuinely ambiguous: two
    versions claiming the same start date for the same population. The agent
    refuses to answer rather than choosing.
    """
    candidates = []
    for version in policy.versions:
        if version.status not in LIVE_STATUSES:
            continue
        if version.effective_from and date.fromisoformat(version.effective_from) > on:
            continue        # not yet
        if version.effective_to and date.fromisoformat(version.effective_to) < on:
            continue        # finished
        if not scope_matches(version.scope, person):
            continue        # somebody else's population
        candidates.append(version)
    if not candidates:
        return []

    def starts(version: PolicyVersionDecl) -> date:
        return date.fromisoformat(version.effective_from) if version.effective_from else date.min

    latest = max(starts(v) for v in candidates)
    return [v for v in candidates if starts(v) == latest]


# ---------------------------------------------------------------- retrieval


@dataclass
class Citation:
    doc_id: str
    doc_title: str
    version: str
    section_id: str
    heading: str
    quoted_text: str
    score: float = 0.0
    matched_terms: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.doc_title} v{self.version}, section {self.section_id} ({self.heading})"

    def as_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id, "doc_title": self.doc_title, "version": self.version,
            "section_id": self.section_id, "heading": self.heading, "quoted_text": self.quoted_text,
            "score": round(self.score, 3), "matched_terms": self.matched_terms,
        }


@dataclass
class Probe:
    """The read-only result of asking "is there a policy for this?".

    This runs while facts are being gathered, before the rule engine decides,
    because `policy_found` and `policy_conflict` ARE facts. It reads and nothing
    else: no writes, no sends, no side effects of any kind.
    """

    found: bool
    conflict: bool
    citations: list[Citation] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    query_terms: list[str] = field(default_factory=list)
    searched: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "found": self.found, "conflict": self.conflict, "query_terms": self.query_terms,
            "sections_searched": self.searched, "conflicts": self.conflicts,
            "citations": [c.as_dict() for c in self.citations],
        }


# The tokenised corpus, document frequencies and lengths, per configuration.
#
# `search` used to re-tokenise every section and recompute every document
# frequency on each query: 1.4 ms at 36 sections, 35 ms at 1,080. The corpus
# only changes when the pack does, and the config hash already moves with it.
_CORPUS: dict[str, dict[str, Any]] = {}


def forget_corpus(tenant_id: str | None = None) -> None:
    """Drop the cached index. Called whenever the corpus is reloaded."""
    if tenant_id is None:
        _CORPUS.clear()
    else:
        for key in [k for k in _CORPUS if k.startswith(f"{tenant_id}:")]:
            del _CORPUS[key]


def _index(conn: sqlite3.Connection, cfg: ResolvedConfig) -> dict[str, Any]:
    key = f"{cfg.tenant_id}:{cfg.config_hash}"
    if key not in _CORPUS:
        rows = db.rows(
            conn,
            "SELECT s.*, d.title FROM policy_section s JOIN policy_doc d "
            "ON d.tenant_id = s.tenant_id AND d.id = s.doc_id WHERE s.tenant_id = ?",
            (cfg.tenant_id,),
        )
        # The heading counts twice: it is a short, deliberate summary of what
        # the section is for.
        docs = [tokenise(r["heading"]) * 2 + tokenise(r["body"]) for r in rows]
        lengths = [len(d) for d in docs]
        _CORPUS[key] = {
            "rows": rows,
            "counts": [Counter(d) for d in docs],
            "lengths": lengths,
            "avg_len": (sum(lengths) / len(lengths)) if lengths else 0.0,
            "frequency": Counter(term for d in docs for term in set(d)),
            "total": len(docs),
        }
    return _CORPUS[key]


MIN_SCORE = 1.2         # below this, nothing relevant was found

# How many distinct query terms a section must contain before it counts as an
# answer. One is not enough: "What is the parking arrangement?" matched a leave
# policy on the single word "arranged", scored above the floor, and would have
# been quoted at somebody asking about parking.
#
# This is the one failure the verify gate cannot catch. The gate checks that a
# citation is real, in force and quoted accurately -- it has no way to know the
# passage does not answer the question. So relevance has to be decided here.
MIN_TERMS = 2
QUOTE_CHARS = 420       # a citation quotes at most this much, so a draft stays readable


def _best_quote(body: str, terms: set[str]) -> tuple[str, list[str]]:
    """The sentences in a section that actually answer the question.

    The verify gate requires these exact words to appear in the draft, so this is
    what the agent will be held to.
    """
    sentences = [s.strip() for s in re.split(r"(?<=[.])\s+", body) if s.strip()]
    scored = []
    for index, sentence in enumerate(sentences):
        hits = {t for t in tokenise(sentence) if t in terms}
        scored.append((len(hits), -index, sentence, sorted(hits)))
    scored.sort(reverse=True)

    chosen: list[str] = []
    matched: set[str] = set()
    for hits, _, sentence, terms_hit in scored:
        if hits == 0 and chosen:
            break
        if sum(len(c) for c in chosen) + len(sentence) > QUOTE_CHARS and chosen:
            break
        chosen.append(sentence)
        matched.update(terms_hit)
        if len(chosen) == 2:
            break

    # Keep the original order so the quote reads as it does in the policy.
    chosen.sort(key=sentences.index)
    return " ".join(chosen) if chosen else body[:QUOTE_CHARS], sorted(matched)


def search(
    conn: sqlite3.Connection,
    cfg: ResolvedConfig,
    query: str,
    *,
    person: Person | None = None,
    on: date | None = None,
    limit: int = 3,
) -> Probe:
    """BM25 over the sections of every version in force for this person."""
    today = on or date.today()
    terms = tokenise(query)
    if not terms:
        return Probe(found=False, conflict=False, query_terms=[])

    # Which versions may be quoted at all, and whether that is ambiguous.
    usable: dict[str, str] = {}
    conflicts: list[str] = []
    for policy in cfg.policies:
        versions = in_force(policy, on=today, person=person)
        if len(versions) > 1:
            conflicts.append(
                f"{policy.title} has {len(versions)} versions in force for this person "
                f"({', '.join(v.version for v in versions)})"
            )
            continue
        if versions:
            usable[policy.id] = versions[0].version
    if conflicts:
        return Probe(found=False, conflict=True, conflicts=conflicts, query_terms=terms)
    if not usable:
        return Probe(found=False, conflict=False, query_terms=terms)

    index = _index(conn, cfg)
    # Which rows are quotable for this person right now. The index covers the
    # whole corpus; scoring only looks at the versions in force.
    live = [
        i for i, row in enumerate(index["rows"]) if usable.get(row["doc_id"]) == row["version"]
    ]
    if not live:
        return Probe(found=False, conflict=False, query_terms=terms)

    # BM25. Document frequencies come from the whole corpus, which is the usual
    # choice and keeps the index independent of who is asking.
    avg_len = index["avg_len"] or 1.0
    frequency = index["frequency"]
    total = index["total"]
    k1, b = 1.5, 0.75

    scored: list[tuple[float, Any, list[str]]] = []
    for i in live:
        row = index["rows"][i]
        counts = index["counts"][i]
        length = index["lengths"][i]
        score = 0.0
        for term in set(terms):
            if not (tf := counts.get(term, 0)):
                continue
            idf = math.log(1 + (total - frequency[term] + 0.5) / (frequency[term] + 0.5))
            score += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * length / avg_len))
        if score > 0:
            scored.append((score, row, sorted(set(terms) & set(counts))))

    scored.sort(key=lambda item: item[0], reverse=True)
    # A short query cannot be asked for two matching terms it does not have.
    needed = min(MIN_TERMS, len(set(terms)))
    top = [
        item for item in scored if item[0] >= MIN_SCORE and len(item[2]) >= needed
    ][:limit]
    if not top:
        return Probe(found=False, conflict=False, query_terms=terms, searched=len(live))

    citations = []
    for score, row, matched in top:
        quote, quote_terms = _best_quote(row["body"], set(terms))
        citations.append(
            Citation(
                doc_id=row["doc_id"], doc_title=row["title"], version=row["version"],
                section_id=row["section_id"], heading=row["heading"], quoted_text=quote,
                score=score, matched_terms=quote_terms or matched,
            )
        )
    return Probe(found=True, conflict=False, citations=citations, query_terms=terms, searched=len(live))


SECOND_CITATION_MARGIN = 0.55   # the runner-up must be at least this fraction of the top score


def best_citations(probe: Probe, limit: int = 2) -> list[Citation]:
    """The sections worth quoting, not just the best one.

    Plenty of real questions are answered by two sections -- the allowance and
    the carry-over rule, the entitlement and how to request it. Quoting only the
    top hit answered half the question. A second is included only when it is
    genuinely close, so a weak match is never padded in.

    Two versions of one policy are never both quoted: the verify gate rejects
    that, and it would be a contradiction rather than a fuller answer.
    """
    if not probe.citations:
        return []
    best = probe.citations[0]
    chosen = [best]
    for candidate in probe.citations[1:]:
        if len(chosen) >= limit:
            break
        if candidate.score < best.score * SECOND_CITATION_MARGIN:
            break
        if any(c.doc_id == candidate.doc_id and c.version != candidate.version for c in chosen):
            continue
        if any(c.doc_id == candidate.doc_id and c.section_id == candidate.section_id for c in chosen):
            continue
        chosen.append(candidate)
    return chosen
