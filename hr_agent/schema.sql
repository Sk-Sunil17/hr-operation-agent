-- HR Operations Agent: the whole data model.
--
-- Three rules this schema exists to enforce:
--   1. Every row belongs to exactly one tenant. Nothing is ever read across tenants.
--   2. Every decision records the configuration that produced it (config_hash),
--      so months later you can prove what the system was told to do.
--   3. The audit table is append-only and hash-chained. Editing or deleting a
--      row breaks the chain visibly.
--
-- Written for SQLite but deliberately Postgres-compatible: no SQLite-only types,
-- timestamps are ISO-8601 TEXT in UTC, structured values are TEXT holding JSON.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- tenancy

CREATE TABLE IF NOT EXISTS tenant (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  pack_path     TEXT NOT NULL,
  config_hash   TEXT NOT NULL,      -- hash of the resolved pack in force
  active        INTEGER NOT NULL DEFAULT 1,
  created_at    TEXT NOT NULL
);

-- The exact resolved configuration behind a config_hash. This is what makes
-- "we can prove which config produced this decision" true rather than claimed.
CREATE TABLE IF NOT EXISTS config_snapshot (
  hash          TEXT PRIMARY KEY,
  tenant_id     TEXT NOT NULL REFERENCES tenant(id),
  captured_at   TEXT NOT NULL,
  resolved_json TEXT NOT NULL
);

-- ---------------------------------------------------------------- directory
-- A local mirror for facts and routing. Adapters own the truth; nothing here is
-- authoritative, and no pay or access field is ever stored.

CREATE TABLE IF NOT EXISTS employee (
  tenant_id       TEXT NOT NULL REFERENCES tenant(id),
  id              TEXT NOT NULL,
  name            TEXT NOT NULL,
  email           TEXT,
  manager_id      TEXT,
  entity          TEXT,             -- legal entity, for multi-entity companies
  employment_type TEXT,             -- full_time | part_time | casual | contractor | ...
  segment_keys    TEXT NOT NULL DEFAULT '[]',   -- JSON array, drives segment overlays
  roles           TEXT NOT NULL DEFAULT '[]',   -- JSON array of pack-defined roles
  timezone        TEXT,
  start_date      TEXT,
  end_date        TEXT,
  active          INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (tenant_id, id)
);

CREATE INDEX IF NOT EXISTS employee_manager ON employee (tenant_id, manager_id);

-- ---------------------------------------------------------------- policy corpus

CREATE TABLE IF NOT EXISTS policy_doc (
  tenant_id     TEXT NOT NULL REFERENCES tenant(id),
  id            TEXT NOT NULL,
  title         TEXT NOT NULL,
  owner_role    TEXT,
  review_months INTEGER,
  PRIMARY KEY (tenant_id, id)
);

-- status: draft | scheduled | in_force | retired
-- Exactly one version of a doc may be in_force for a given day and scope.
-- Two in force is a data fault: the agent refuses to answer rather than choosing.
CREATE TABLE IF NOT EXISTS policy_version (
  tenant_id      TEXT NOT NULL,
  doc_id         TEXT NOT NULL,
  version        TEXT NOT NULL,
  status         TEXT NOT NULL,
  effective_from TEXT,
  effective_to   TEXT,
  scope_json     TEXT NOT NULL DEFAULT '{}',  -- segment/entity scoping, never a country constant
  published_at   TEXT,
  published_by   TEXT,
  source_sha     TEXT,
  PRIMARY KEY (tenant_id, doc_id, version),
  FOREIGN KEY (tenant_id, doc_id) REFERENCES policy_doc (tenant_id, id)
);

CREATE INDEX IF NOT EXISTS policy_version_force ON policy_version (tenant_id, status, effective_from);

CREATE TABLE IF NOT EXISTS policy_section (
  tenant_id   TEXT NOT NULL,
  doc_id      TEXT NOT NULL,
  version     TEXT NOT NULL,
  section_id  TEXT NOT NULL,       -- stable, citable: "3.2"
  heading     TEXT NOT NULL,
  body        TEXT NOT NULL,
  ordinal     INTEGER NOT NULL,
  PRIMARY KEY (tenant_id, doc_id, version, section_id),
  FOREIGN KEY (tenant_id, doc_id, version) REFERENCES policy_version (tenant_id, doc_id, version)
);

-- ---------------------------------------------------------------- work

-- status: open | queued | answered | handed_over | refused | closed | failed
-- mode is what the rule engine allowed: auto | prepare | hand_over | refuse
CREATE TABLE IF NOT EXISTS work_item (
  id           TEXT PRIMARY KEY,
  tenant_id    TEXT NOT NULL REFERENCES tenant(id),
  parent_id    TEXT REFERENCES work_item(id),   -- a compound message splits into children
  channel      TEXT NOT NULL,
  requester_id TEXT,              -- who asked
  subject_id   TEXT,              -- whose record it is about (often the same person)
  intent       TEXT,
  sensitivity  TEXT,
  mode         TEXT,
  rule_id      TEXT,
  case_id      TEXT,
  status       TEXT NOT NULL,
  config_hash  TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  closed_at    TEXT
);

CREATE INDEX IF NOT EXISTS work_item_tenant ON work_item (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS work_item_status ON work_item (tenant_id, status);

-- The raw inbound payload, persisted before any processing. dedupe_key makes a
-- replayed delivery a no-op rather than a repeated effect.
CREATE TABLE IF NOT EXISTS event (
  id            TEXT PRIMARY KEY,
  tenant_id     TEXT NOT NULL REFERENCES tenant(id),
  work_item_id  TEXT REFERENCES work_item(id),
  kind          TEXT NOT NULL,
  payload_json  TEXT NOT NULL,    -- redacted before storage
  dedupe_key    TEXT NOT NULL,
  received_at   TEXT NOT NULL,
  UNIQUE (tenant_id, dedupe_key)
);

CREATE TABLE IF NOT EXISTS message_log (
  id              TEXT PRIMARY KEY,
  tenant_id       TEXT NOT NULL REFERENCES tenant(id),
  work_item_id    TEXT NOT NULL REFERENCES work_item(id),
  direction       TEXT NOT NULL,  -- inbound | outbound
  channel         TEXT NOT NULL,
  body            TEXT NOT NULL,
  idempotency_key TEXT,
  suppressed      INTEGER NOT NULL DEFAULT 0,
  at              TEXT NOT NULL,
  UNIQUE (tenant_id, idempotency_key)
);

-- ---------------------------------------------------------------- decisions
--
-- THE FLYWHEEL TABLE. Every column after draft_text exists so that the approval
-- queue doubles as a labelling machine. These rows are the only training data we
-- will ever have, and they are unrecoverable if not captured from the start:
--
--   approved      -> positive example
--   edited        -> preference pair (draft_text rejected, edit_diff chosen)
--   sent_back     -> error label, with the cause in human_reason_code
--   taken_over    -> escalation-boundary label
--   verify failed -> hard negative (verify_passed = 0)

CREATE TABLE IF NOT EXISTS decision (
  id                TEXT PRIMARY KEY,
  tenant_id         TEXT NOT NULL REFERENCES tenant(id),
  work_item_id      TEXT NOT NULL REFERENCES work_item(id),
  created_at        TEXT NOT NULL,

  -- what the rule engine saw and chose
  facts_json        TEXT NOT NULL,
  rule_id           TEXT NOT NULL,
  mode              TEXT NOT NULL,
  needs_citation    INTEGER NOT NULL,
  rule_reason       TEXT NOT NULL,          -- human-readable, from the pack

  -- what was retrieved, with versions, so an answer can be reproduced
  retrieval_json    TEXT NOT NULL DEFAULT '[]',

  -- exactly what produced the draft
  provider          TEXT NOT NULL DEFAULT 'template',
  model_id          TEXT,
  model_params_json TEXT NOT NULL DEFAULT '{}',
  prompt_hash       TEXT,
  pack_hash         TEXT NOT NULL,

  draft_text        TEXT,
  verify_json       TEXT NOT NULL DEFAULT '[]',   -- per-check pass/fail, so failure is explainable
  verify_passed     INTEGER,

  -- the human verdict: approved | edited | sent_back | taken_over
  human_verdict     TEXT,
  human_actor_id    TEXT,
  human_reason_code TEXT,
  human_note        TEXT,
  edit_diff         TEXT,
  decided_at        TEXT
);

CREATE INDEX IF NOT EXISTS decision_item ON decision (tenant_id, work_item_id);
CREATE INDEX IF NOT EXISTS decision_verdict ON decision (tenant_id, human_verdict);

CREATE TABLE IF NOT EXISTS citation (
  id           TEXT PRIMARY KEY,
  tenant_id    TEXT NOT NULL REFERENCES tenant(id),
  work_item_id TEXT NOT NULL REFERENCES work_item(id),
  decision_id  TEXT NOT NULL REFERENCES decision(id),
  doc_id       TEXT NOT NULL,
  version      TEXT NOT NULL,
  section_id   TEXT NOT NULL,
  quoted_text  TEXT NOT NULL      -- the exact words used, checked by the verify gate
);

-- ---------------------------------------------------------------- actions
--
-- Nothing reaches the world except through an action_plan. prepare() writes a
-- row and nothing else; commit() is the only thing that acts, once, under a key.
-- status: prepared | committed | cancelled | duplicate | failed

CREATE TABLE IF NOT EXISTS action_plan (
  id              TEXT PRIMARY KEY,
  tenant_id       TEXT NOT NULL REFERENCES tenant(id),
  work_item_id    TEXT NOT NULL REFERENCES work_item(id),
  decision_id     TEXT REFERENCES decision(id),
  adapter         TEXT NOT NULL,
  operation       TEXT NOT NULL,
  args_json       TEXT NOT NULL,
  preview_text    TEXT NOT NULL,   -- what a human approving this is shown
  idempotency_key TEXT NOT NULL,
  status          TEXT NOT NULL,
  prepared_at     TEXT NOT NULL,
  committed_at    TEXT,
  cancelled_at    TEXT,
  receipt_json    TEXT,
  UNIQUE (tenant_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS idempotency_ledger (
  tenant_id     TEXT NOT NULL REFERENCES tenant(id),
  key           TEXT NOT NULL,
  plan_id       TEXT NOT NULL REFERENCES action_plan(id),
  first_seen_at TEXT NOT NULL,
  result_json   TEXT,
  PRIMARY KEY (tenant_id, key)
);

-- ---------------------------------------------------------------- approvals
-- status: open | decided | withdrawn ; outcome: approved | sent_back | taken_over

CREATE TABLE IF NOT EXISTS approval (
  id            TEXT PRIMARY KEY,
  tenant_id     TEXT NOT NULL REFERENCES tenant(id),
  work_item_id  TEXT NOT NULL REFERENCES work_item(id),
  decision_id   TEXT NOT NULL REFERENCES decision(id),
  assigned_role TEXT NOT NULL,
  assigned_to   TEXT,
  requested_by  TEXT,             -- nobody decides their own request
  status        TEXT NOT NULL,
  outcome       TEXT,
  note          TEXT,
  opened_at     TEXT NOT NULL,
  decided_at    TEXT,
  decided_by    TEXT
);

CREATE INDEX IF NOT EXISTS approval_queue ON approval (tenant_id, status, assigned_role);

CREATE TABLE IF NOT EXISTS hr_case (
  id          TEXT PRIMARY KEY,
  tenant_id   TEXT NOT NULL REFERENCES tenant(id),
  kind        TEXT NOT NULL,
  subject_id  TEXT,
  owner_role  TEXT,
  owner_id    TEXT,
  status      TEXT NOT NULL,
  taken_over  INTEGER NOT NULL DEFAULT 0,   -- the agent stops for good on this case
  opened_at   TEXT NOT NULL,
  closed_at   TEXT
);

-- ---------------------------------------------------------------- audit
--
-- Append-only and hash-chained per tenant:
--   row_hash = H(prev_hash, tenant_id, at, actor, stage, action, detail_json)
-- detail_json is redacted before it is written. There are no UPDATE or DELETE
-- statements against this table anywhere in the codebase, and a test asserts it.

CREATE TABLE IF NOT EXISTS audit (
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id    TEXT NOT NULL REFERENCES tenant(id),
  work_item_id TEXT,
  at           TEXT NOT NULL,
  actor        TEXT NOT NULL,      -- "agent", "rule:E1", or an employee id
  actor_kind   TEXT NOT NULL,      -- agent | rule | person | scheduler
  stage        TEXT NOT NULL,      -- receive | understand | decide | retrieve | draft | verify | act | handover
  action       TEXT NOT NULL,
  detail_json  TEXT NOT NULL DEFAULT '{}',
  config_hash  TEXT,
  prev_hash    TEXT,
  row_hash     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS audit_item ON audit (tenant_id, work_item_id, seq);

-- ---------------------------------------------------------------- scheduler & metrics

CREATE TABLE IF NOT EXISTS job_run (
  id          TEXT PRIMARY KEY,
  tenant_id   TEXT NOT NULL REFERENCES tenant(id),
  job         TEXT NOT NULL,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  report_json TEXT
);

CREATE TABLE IF NOT EXISTS metric_snapshot (
  id           TEXT PRIMARY KEY,
  tenant_id    TEXT NOT NULL REFERENCES tenant(id),
  at           TEXT NOT NULL,
  window_from  TEXT NOT NULL,
  window_to    TEXT NOT NULL,
  data_json    TEXT NOT NULL,
  caveats_json TEXT NOT NULL      -- never absent; honesty is structural
);

-- ---------------------------------------------------------------- mock adapter state
--
-- Only the mock adapters use this. A real HRIS adapter talks to the real system
-- and stores nothing here; this exists so the demo has somewhere to put a change
-- that was actually committed, and so a committed change is visibly different
-- from a prepared one.

CREATE TABLE IF NOT EXISTS adapter_record (
  tenant_id  TEXT NOT NULL REFERENCES tenant(id),
  adapter    TEXT NOT NULL,
  person_id  TEXT NOT NULL,
  field      TEXT NOT NULL,
  value      TEXT,
  updated_at TEXT NOT NULL,
  updated_by TEXT,
  PRIMARY KEY (tenant_id, adapter, person_id, field)
);

-- A document that has actually been filed for a person.
--
-- Readiness and expiry scanning both need to ask "what does this person have,
-- and when does it run out". Reading that back out of committed action_plan
-- arguments worked but was archaeology; this is the record.

CREATE TABLE IF NOT EXISTS filed_document (
  tenant_id     TEXT NOT NULL REFERENCES tenant(id),
  person_id     TEXT NOT NULL,
  document_type TEXT NOT NULL,
  reference     TEXT,
  filed_at      TEXT NOT NULL,
  expires_at    TEXT,
  filed_by      TEXT,
  template_version TEXT,
  PRIMARY KEY (tenant_id, person_id, document_type)
);

CREATE INDEX IF NOT EXISTS filed_document_expiry ON filed_document (tenant_id, expires_at);

-- How often a person has been chased about one thing.
--
-- Every company sets its own cap. Without a count there is nothing to cap, and
-- an agent that chases indefinitely is worse than one that never chases.

CREATE TABLE IF NOT EXISTS reminder (
  tenant_id  TEXT NOT NULL REFERENCES tenant(id),
  subject_id TEXT NOT NULL,
  about      TEXT NOT NULL,
  sent_count INTEGER NOT NULL DEFAULT 0,
  last_sent  TEXT,
  PRIMARY KEY (tenant_id, subject_id, about)
);

-- Sign-in, for when the demo role switcher is turned off.
--
-- An account is tied to a person in the company directory, so roles come from
-- the directory rather than being a second thing to keep in step. There is no
-- "admin" flag here: what someone may do is decided by the roles they hold.

CREATE TABLE IF NOT EXISTS account (
  tenant_id     TEXT NOT NULL REFERENCES tenant(id),
  person_id     TEXT NOT NULL,
  email         TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  salt          TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  last_seen     TEXT,
  PRIMARY KEY (tenant_id, person_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS account_email ON account (tenant_id, email);

CREATE TABLE IF NOT EXISTS schema_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- ---------------------------------------------------------------- interviews
--
-- An interview that has actually been put in a calendar, plus everyone who was
-- invited to it. Three reasons this is a table rather than a committed plan's
-- arguments:
--
--   1. Free/busy has to include the interviews THIS agent booked. Without that,
--      the second request of the morning proposes a time the first one just
--      took, and two candidates arrive to meet the same panel.
--   2. A reschedule has to know what it is moving, and a cancellation has to
--      know what to withdraw. Reading that back out of action_plan arguments is
--      archaeology, and the plan rows are keyed by effect, not by subject.
--   3. "How many interviews is this person already doing that day" is a cap a
--      company sets, and a cap needs something to count.
--
-- A candidate is usually NOT in the employee table: they do not work here yet.
-- Their name and address therefore live on this row, and are the one place in
-- the schema where an external person's details are held.

CREATE TABLE IF NOT EXISTS interview (
  id              TEXT PRIMARY KEY,
  tenant_id       TEXT NOT NULL REFERENCES tenant(id),
  work_item_id    TEXT REFERENCES work_item(id),
  case_id         TEXT,
  stage           TEXT,              -- a stage id from this company's interviews.yaml
  stage_label     TEXT,
  role_title      TEXT,              -- the vacancy, as the requester described it
  candidate_name  TEXT,
  candidate_email TEXT,
  on_date         TEXT NOT NULL,     -- ISO date, in the company's own timezone
  start_time      TEXT NOT NULL,     -- HH:MM, local to that timezone
  end_time        TEXT NOT NULL,
  minutes         INTEGER NOT NULL,
  timezone        TEXT NOT NULL,     -- never assumed; taken from the company's locale
  location        TEXT,
  status          TEXT NOT NULL,     -- scheduled | cancelled | superseded
  calendar_ref    TEXT,              -- the id the calendar system gave the event
  conference_url  TEXT,
  supersedes      TEXT,              -- the interview this one replaces, when rescheduled
  created_at      TEXT NOT NULL,
  created_by      TEXT,
  cancelled_at    TEXT,
  cancel_reason   TEXT
);

CREATE INDEX IF NOT EXISTS interview_day ON interview (tenant_id, on_date, status);
CREATE INDEX IF NOT EXISTS interview_item ON interview (tenant_id, work_item_id);

-- part: panel | candidate | organiser
CREATE TABLE IF NOT EXISTS interview_attendee (
  id           TEXT PRIMARY KEY,
  tenant_id    TEXT NOT NULL REFERENCES tenant(id),
  interview_id TEXT NOT NULL REFERENCES interview(id),
  person_id    TEXT,              -- NULL for a candidate, who does not work here
  name         TEXT,
  email        TEXT,
  part         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS interview_attendee_of ON interview_attendee (tenant_id, interview_id);
CREATE INDEX IF NOT EXISTS interview_attendee_person ON interview_attendee (tenant_id, person_id);
