# HR Operations Agent — Plan

Status: **working end to end — Steps 0-10 complete, 181 tests green, demo runnable**
Last updated: 2026-11-01

---

## 0. What this is

A policy-grounded, approval-gated HR operations agent that any company configures
with a declarative **tenant pack**, and that is structurally incapable of sending an
unverifiable or ungoverned answer.

Two properties define it:

1. It cannot send a claim it cannot cite to a policy version in force.
2. Everything a company differs on is config, not code.

---

## 1. Decisions locked

| Decision | Value |
|---|---|
| Language / stack | Python 3.12 + FastAPI (JSON API), SQLite in dev with a Postgres-compatible schema |
| Testing | pytest; deepeval for LLM evals later |
| Jurisdiction | **Jurisdiction-neutral core.** No country logic anywhere in the kernel |
| Scope v1 | Full HR ops: Q&A, personal enquiries, documents/forms, onboarding, offboarding, scheduling |
| Web UI | React 18 + Vite client, copying the design system of the user's finance-operations-agent (see section 7a) |
| Models | API-free by default. Local/templated first; provider interface keeps paid models an opt-in config line |
| Tenancy (SaaS vs self-host) | **Deferred.** Design keeps both open at no cost |

### What "jurisdiction-neutral" means concretely

- The agent **never computes a statutory entitlement.** It quotes the tenant's own
  policy text or hands over. No leave accrual, notice period, or holiday maths in code.
  This removes ~90% of the country-specific surface and is the correct legal posture.
- **Redaction is a declared pattern pack**, not hardcoded detectors. Generic classes only:
  `email, phone, national_id, bank_identifier, account_number, date_of_birth,
  street_address, medical_term, member_number`. A company adds its own ID formats as config.
- **Locale is tenant config**: timezone, date format, currency, week start, working hours,
  languages, holiday calendar file. Supplied, never assumed.
- **Statutory floors are an optional, empty-by-default overlay slot.** The mechanism exists;
  v1 ships no country pack. A company that needs one drops in a file, zero kernel changes.

### Prior build (context)

A previous attempt existed in this folder and was deleted. Its `.pytest_cache` recorded
506 tests across kernel, rules, policy_service, classify, verify, security, approvals,
audit, redaction, forms, tools, scheduler, metrics and web. The ideas were sound. The fatal
flaw: it was built for one company in one country, with company specifics baked into code and
fixtures. This plan inverts that — kernel versus tenant pack.

---

## 2. The gap we are solving

### Short version

1. **Every HR AI tool is built to answer. None are built to refuse.** The market optimises
   deflection rate ("we auto-resolved 70% of tickets") and nobody measures how many were
   wrong, because the architecture provides no point at which the system can stop itself.
   In HR, a confident wrong answer is a legal event, not a UX problem.
2. **They all do chat. Nobody does the actual work** — chasing forms, verifying a new starter
   is ready, offboarding checklists, expiring documents, scheduling panels. That is where
   HR's hours actually go, and it needs real writes behind real approval gates.
3. **"Adapts to your company" is sold, not engineered.** Every deployment is a 3-6 month
   services engagement because customer specifics live in code, prompts, and a consultant's
   head.

### How the existing market works

| Archetype | Examples | Mechanism | Where it stops |
|---|---|---|---|
| HCM-native agents | Workday (Illuminate/Sana), SAP SuccessFactors Joule, Oracle, Dayforce, Darwinbox | Agents inside the HCM; internal APIs, inherited security model, vendor business-process framework; surfaced in Teams/M365 | Sees only that vendor's data. Useless for a company on BambooHR plus spreadsheets. Config is consultant work |
| Employee service desks | Moveworks (now the ServiceNow conversational layer), Leena AI, Aisera, Espressive, Atomicwork | Slack/Teams front end, intent classification, RAG over intranet, connectors to 100-200 systems, ticket fallback. Leena's orchestrator fans one prompt out to per-app agents | Optimised for deflection, not correctness. Answer quality equals intranet quality. "Approval" is an ITSM ticket, not an HR process gate with ownership rules |
| Recruiting agents | Paradox, HiredScore, Eightfold, AI interviewers | Conversational screening, calendar solvers, ranking models | Hiring only, and the most heavily regulated slice (EU AI Act Annex III, NYC LL 144 bias audits) |
| Horizontal assistants | Glean, M365 Copilot agents, Gemini Enterprise, LangGraph/n8n builds | ACL-aware RAG plus hand-wired tools | No HR semantics, no sensitivity model, no entitlement reasoning, no HR-grade evidence trail |
| SMB HRIS copilots | BambooHR, Rippling, Deel, Gusto | Good inside their own schema, templated content | Shallow, single-vendor, not configurable |

Common pipeline across all of them: channel -> classify -> retrieve -> tool-call -> human
handoff. Governance is typically disclaimers plus logs.

### The specific gaps

| # | Gap | What we do instead |
|---|---|---|
| G1 | Correctness unenforced; generate-then-maybe-check | A verify gate between draft and send. Failing verification escalates; it never degrades into sending |
| G2 | Policy treated as documents, not governed objects | Versioned, effective-dated, scoped policy objects. Two versions in force means refuse to answer. Retired versions unquotable |
| G3 | Autonomy is binary (chatbot or ticket) | Four-rung ladder per case type: act alone / prepare for a human / hand over / refuse with reason |
| G4 | Sensitivity blindness on grievance, harassment, injury, underpayment, mental health | Restricted is structural: acknowledge, route to a named human, draft nothing, ask nothing. The model may raise sensitivity, never lower it |
| G5 | Logs, not evidence. Unlogged model parameters; contradictory answers indistinguishable afterwards | Hash-chained decision records: rule id, facts, citation, actor, reason, config hash, model identity |
| G6 | PII leakage; raw HR text sent to models | Redaction before any model call; server refuses to boot with redaction disabled |
| G7 | Non-idempotent side effects; duplicate sends and double writes on retry | prepare -> commit under mandatory idempotency keys; replayed events have no repeated effect |
| G8 | Prompt injection through documents (resumes, certificates, policy PDFs) | Mode is never model-decided. Injected text can shift wording at most, never autonomy |
| G9 | Adaptability is a consulting project | Declarative tenant pack plus a validation and simulation harness. Onboarding is config plus green tests |
| G10 | ROI unprovable; deflection percentage is not hours saved | Honest metrics: escalated cases count review time, not savings. Mandatory caveats, small-sample warnings, no hard-coded savings target |
| G11 | Everyone does Q&A; nobody does HR back-office ops | Forms intake, onboarding readiness, offboarding, expiry watch, scheduling |

### Worked examples

**"How many public holidays do I get?"**
Routine -> facts resolve the person's segment -> rule permits answering alone if cited ->
Holiday Policy v2.1 section 3, in force -> template reply quoting it verbatim -> gate
confirms the quote is present and no personal entitlement is claimed -> sent in seconds,
audit row written.

**"My manager has been making racist remarks."**
Restricted -> rule fires before any retrieval -> no policy lookup, no draft, no clarifying
question -> warm acknowledgement plus the name of the human handling it, within seconds ->
case opened and routed -> audit records that the agent deliberately did not answer, and why.

The second example is the product in miniature: the most valuable thing the agent does is
not answer.

---

## 3. Non-goals for v1 (deliberate, not oversights)

- No candidate ranking, scoring, or screening (EU AI Act Annex III high-risk).
- No performance or termination recommendations.
- No payroll system-of-record calculation.
- No legal, medical, or financial advice.
- No tool anywhere that can write pay or grant access — not even behind an approval.

---

## 4. Runtime flow — how the agent works

```
  Employee message / uploaded document / scheduled job
                      |
   1  RECORD IT        persisted before anything else happens
                      |
   2  REDACT           strip identifiers; nothing leaves unredacted
                      |
   3  CLASSIFY         what is it about, how sensitive
                      |    model may RAISE sensitivity, never lower it
   4  GATHER FACTS     who, their manager, open case, record ownership
                      |    facts come from systems, not from the message
  === 5 RULE ENGINE ===  decides HOW MUCH THE AGENT MAY DO
                      |    the model is never asked this
   6  FIND POLICY      only versions in force today, scoped to this person
                      |    two in force -> refuse and report the data problem
   7  DRAFT            template for most traffic, model for the rest
                      |
  === 8 VERIFY GATE ===  pass or fail only; failure escalates, never sends
                      |
        +-------------+-------------+--------------+
     SEND IT       QUEUE IT     HAND OVER       REFUSE
     (auto)      (needs human) (to a person)  (with reason)
        +-------------+-------------+--------------+
                      |
   9  AUDIT           tamper-evident chained record of every step
```

### What the verify gate checks

- Is the policy it claims to cite actually quoted in the text?
- Does it tell the person what *they personally* get ("you are entitled to 14 weeks")? FAIL
- Does it give legal, medical, or financial advice? FAIL
- Is it a write with no idempotency key? FAIL
- Is it committing something only approved to be *prepared*? FAIL
- Is it quoting two versions of one policy? FAIL
- Is it trivially short or substanceless? FAIL

### The four outcomes

| Outcome | When | What the employee sees |
|---|---|---|
| Send it | Routine, policy in force found, citation verified | An immediate answer quoting the policy |
| Queue it | Personal, or any real-world write | "HR will confirm shortly"; a human sees draft plus citation and approves, edits, or sends back |
| Hand over | Restricted, or verification failed | A warm acknowledgement and a named human, immediately. No answer drafted at all |
| Refuse | Asking about someone else's record, or no governing policy exists | A plain explanation of why, and what to do instead |

---

## 5. Adaptability architecture

```
+---------------------------------------------------------+
|  KERNEL  (code - identical for every customer)          |
|  ingest -> classify -> facts -> RULE ENGINE -> retrieve |
|  -> draft -> VERIFY GATE -> act -> prepare/commit -> audit |
+---------------+-----------------------------------------+
                | loads, validates, hash-stamps
   +------------+--------------+------------------+
   v            v              v                  v
JURISDICTION  TENANT PACK   SEGMENT          ADAPTERS (SPI)
PACK          (per company) OVERLAYS         HRIS / ATS / ITSM
optional,     policies      entity,          calendar / e-sign
empty in v1   cases         employment type  doc store / notify
              approvals     union            each declares the
              tone, SLAs    contractor       FIELDS IT OWNS and
              channels                       OPS it supports
```

### Seven axes of variation, each with a declarative home

1. Jurisdiction and statutory floor (optional overlay, empty in v1)
2. Policy corpus and versions
3. Org, roles, approval matrix, delegation
4. Case taxonomy and autonomy per case
5. Systems of record
6. Channels
7. Voice and operations: tone, languages, SLAs, working hours, escalation targets

### Four rules that make this real rather than aspirational

- **Monotonic composition.** Base pack sets a ceiling of autonomy; overlays may only tighten.
  Resolution order: jurisdiction floor -> base -> tenant -> segment. Never per-user.
  A lattice test proves no overlay can loosen a refusal.
- **Config is versioned data, hash-stamped into every audit row.** You can prove which
  configuration produced a given decision months later.
- **Adapters declare ownership.** The kernel refuses to write a field no adapter owns, and
  refuses pay and access fields from every path including an approval.
- **Tenant packs are testable artifacts.** `hrctl validate` proves coherence; `hrctl simulate`
  runs that company's own golden cases and reports case-matrix and citation coverage.
  Adaptability is a green test run, not a claim.

### The honesty mechanism

Development runs against two deliberately mismatched seed companies from Step 1:

- `acme_labs` — 60 people, flat, two approvers, Slack, mock BambooHR-style HRIS, loose autonomy
- `meridian_group` — 2,500 people, three legal entities, segment overlays, deep approval
  chains, Teams plus email, mock Workday-style HRIS plus ITSM, tight autonomy

If a feature cannot be expressed for both without touching kernel code, the abstraction is
wrong and gets fixed in that step — not at customer number five.

---

## 6. Model strategy

### Hardware reality (this machine)

Intel i7-11850H, 16 GB RAM, Intel integrated graphics only — **no NVIDIA GPU, no CUDA** —
124 GB free, Docker and Python 3.12 present. CPU-only inference.

### Where models touch the system

| Call site | Volume | Requirement | Decision |
|---|---|---|---|
| Redaction / PII | Every message | Runs before any egress by definition | Local always. Pattern packs; optional local NER later |
| Classify | Every inbound | High recall on restricted, low latency | Rules and lexicon first; model only as fallback when the taxonomy is unsure |
| Retrieve | Every answer | Must be explainable for audit | BM25 plus small local embeddings plus cross-encoder reranker. (No embeddings API involved) |
| Draft | Only cases reaching draft | Tone, multilingual, constraint-following | Template for 70-80% of traffic; small local model for the rest |
| Verify | Every draft | Independence from the drafter | Deterministic checks plus a local NLI entailment model; never sees the raw user message |
| Document extraction | Uploads | Layout-aware with provenance | OCR plus layout rules locally |

### What runs well on this machine, free, today

| Component | Model class | Size | CPU latency |
|---|---|---|---|
| Redaction | no model, pattern packs | — | instant |
| Retrieval | BM25 plus bge-small / e5-small | 33-110M | milliseconds |
| Rerank | MiniLM cross-encoder | 22-110M | ~50 ms |
| Classify | fine-tuned DeBERTa-v3 / ModernBERT | 180-400M | ~30 ms |
| Entailment | DeBERTa-v3-MNLI | 184M | ~50 ms |
| Documents | Tesseract / PaddleOCR plus layout rules | — | seconds |

Total RAM for all of the above: roughly 1.5-2 GB. Five of six model call sites covered,
offline, at zero cost.

### Generative drafting on CPU (estimates, to be benchmarked)

| Model | RAM (4-bit) | Est. speed | 600-token reply |
|---|---|---|---|
| Qwen3-4B / Llama-3.2-3B / Phi-4-mini | ~2.5 GB | ~8-15 tok/s | ~45-75 s |
| Qwen3-8B / Llama-3.1-8B / Mistral-7B | ~5 GB | ~3-8 tok/s | ~1.5-3 min |
| 14B+ | ~9 GB | ~2 tok/s | unusable, will not fit |

### Why that matters less than it appears

**The design barely needs a generative model, because it quotes instead of paraphrasing.**

- Routine policy question -> verbatim quote plus a sentence of framing. A template.
- Restricted -> a fixed acknowledgement. Never model-written, even with a frontier model.
- Personal -> verbatim quote plus "a human will confirm your situation." A template.
- Admin, documents, onboarding, offboarding -> structured checklists. No prose generation.

Roughly **70-80% of traffic needs no generative model at all.** The remainder is tone
smoothing and long-document summarising, which a local 3-4B handles adequately.

**And the verify gate means a weaker model cannot produce a wrong answer — it produces more
escalations.** Model quality is a throughput dial, not a correctness dial:

```
strong model -> ~90% of drafts clear the gate -> HR reviews ~10%
weak   model -> ~60% of drafts clear the gate -> HR reviews ~40%
                   nobody receives a wrong answer either way
```

A deflection chatbot cannot make this trade. We can, because the gate is load-bearing.
This is the strongest argument that local-first is viable for this product specifically.

### Can we create our own model?

| Interpretation | Verdict |
|---|---|
| Pretrain an LLM from scratch | No. Millions of dollars; result worse than a free 3B download |
| Fine-tune an open 7-8B (LoRA/QLoRA) | Not on this laptop (needs ~16 GB VRAM). Free on Colab/Kaggle T4 when we have data |
| **Fine-tune a small encoder classifier (50-400M)** | **Yes, on this laptop, CPU, under an hour.** Highest-value model we would ever own |
| Run open weights locally | Yes. Small models fly; generative models crawl |
| "Our own model" = pack plus rules plus gate plus audit | **This is the actual product**, and the only part nobody can copy by buying API credits |

The decisive argument: **our gaps are architectural, not capability gaps.** None of G1-G11 is
caused by a model being insufficiently smart. A better model does not prevent an uncited
send; the gate does. Also, training requires data we will not have until we have customers,
and HR data carries special-category restrictions.

### Where owning a model does pay, ranked with triggers

1. **Redaction detectors — own from day one.** Non-optional; runs before egress.
2. **Reranker — own soon.** Off-the-shelf weights, CPU, large retrieval-quality win.
3. **Sensitivity classifier — distill when labels exist.** Trigger: 10k+ human verdicts, or a
   tenant needing the guardrail to survive an outage.
4. **Citation-entailment verifier — distill from stronger judgments.** Trigger: verify cost
   becomes material, or a tenant forbids external calls.
5. **Drafting — never, on current evidence.**

### The one decision that cannot be deferred

The approval queue is a labelling machine, and the data is unrecoverable if not captured from
Step 0:

| Human action | Training signal |
|---|---|
| Approve unchanged | Positive example |
| Edit then approve | Preference pair |
| Send back with reason | Error label with cause |
| Take over | Escalation-boundary label |
| Verify gate rejected the draft | Hard negative — the highest-value rows we will ever have |

So the `decision` table records: input, retrieved context with doc versions, model id, prompt
hash, config hash, the draft, the verdict, the edit diff, the reason code.

### Model posture and policy

- **Provider interface** with implementations: `null` (no model), `local` (open weights),
  and optional hosted providers a tenant may enable on their own account.
- **Per-tenant model routing is pack config, not code.** Residency becomes a config line.
- **No-model mode must genuinely work**: rules plus retrieval plus templates — degraded but
  correct. This is how pilots run before an AI committee approves anything, and how we
  survive outages.
- **We do not train on tenant data.** Opt-in only, never pooled across tenants.
- **Pin model identities; never float.** A model or retrieval change is a config change that
  must re-run the eval suite before promotion. (Documented industry failure: a hallucination
  rate drifting 3.2% -> 7.1% after a retrieval rebuild with no re-assessment.)

---

## 7. Build flow — the order of construction

```
 STEP 0   skeleton + data model + pack schema          -+
 STEP 1   two seed companies as config                  |  foundations
 STEP 2   audit chain + redaction                      -+  (no AI, no deps)
 STEP 3   rule engine ..................... first thing you can play with
 STEP 4   kernel loop wired end-to-end .... * WORKING AGENT, ZERO AI
 STEP 5   policy service (versions, citations)
 STEP 6   verify gate, complete
 STEP 7   classification (+ optional local model)
 STEP 8   tool adapters, prepare/commit
 STEP 9   approvals + roles
 STEP 10  web UI + API ................... * DEMO-ABLE IN A BROWSER
 STEP 11  the five HR ops workflows
 STEP 12  third company, config-only ..... * ADAPTABILITY PROVEN
 STEP 13  metrics, evals, hardening
```

### 7a. UI design system (decided 2026-11-01)

The UI copies the user's finance-operations-agent client, which lives outside this repo at
`~/Downloads/finance-operations-agent (1) (1)/finance-operations-agent (1)/finance-agent/client`.

| Element | What to copy |
|---|---|
| Stack | React 18 + Vite, one plain `src/styles.css`, no router (tab state in `App.jsx`) |
| Colour | navy `#0b1f33` topbar, gold `#c9a227` on the active nav item, `#f5f7fa` page, `#dde3ea` lines |
| Type | Manrope for display, DM Sans for body |
| Shell | navy topbar with brand, who-is-signed-in and role; horizontal `.mainnav` of workflows under it |
| Screen layout | `.layout` grid `340px minmax(0,1fr)`: `<aside class="side">` cards on the left, `<main class="main">` detail on the right |
| Shared pieces | `Rail` (numbered pipeline stations), `Checks`, `RecordList`, `AuditTrail`, `useBusy`, `useRecords` |
| Status tones | ok / wait / err / neutral, as coloured dots |
| API shape | `api.js` fetch wrapper, request header for CSRF, lists paginate as `{items, nextCursor}` |

Their six-stage `Rail` is a direct fit for our pipeline, so the work-item screen uses it with our
own stations: **Receive, Understand, Decide, Retrieve, Draft, Verify, Outcome**. Our `Checks`
list is the verify gate, per check, which is exactly why that gate records per-check results.

### Step 0 — Skeleton, data model, pack schema (~1 day)

Tables: `tenant, employee, work_item, event, decision, citation, plan, approval, audit,
policy_doc, policy_version, policy_section, case, message_log, idempotency_ledger,
metric_snapshot`.

Pack schema (Pydantic): `manifest, cases, rules_overlay, approvals, adapters, redaction,
locale, segments`.

Run: `hrctl init` -> database exists, schema validated.

### Step 1 — Two seed companies (~2 days)

```
packs/tenants/acme_labs/            packs/tenants/meridian_group/
  manifest.yaml                       manifest.yaml
  policies/*.md + versions.yaml       policies/*.md + versions.yaml
  cases.yaml                          cases.yaml
  approvals.yaml                      approvals.yaml     (deep chains)
  adapters.yaml                       adapters.yaml      (HRIS + ITSM)
  redaction.yaml                      redaction.yaml
  locale.yaml                         locale.yaml
  people.yaml                         people.yaml        (3 entities)
  golden_cases.yaml                   segments.yaml      (overlays)
                                      golden_cases.yaml
```

Run: `hrctl validate` green on both. `hrctl show config --tenant meridian_group` prints every
resolved setting and which layer set it.

### Step 2 — Audit chain + redaction (~1.5 days)

Append-only audit, each row hashing the previous. Redaction pattern-pack engine with an
`assert_clean()` guard; refuses to boot if disabled.

Run: `hrctl audit verify` -> chain intact. Hand-edit a row and it reports where it broke.

### Step 3 — Rule engine (~2 days)

The case matrix as data:

```yaml
- id: E1
  when: {intent: enquiry, sensitivity: routine, policy_found: true}
  mode: auto
  needs_citation: true
  reason: "A routine policy question with a current policy may be answered directly."

- id: E-RESTRICTED
  when: {sensitivity: restricted}
  mode: hand_over
  needs_citation: false
  reason: "Restricted matters go to a named human without a drafted answer."
```

Ordered, first match wins, final rule catches everything and hands over. Tenant overlays may
only tighten, proven by a lattice test.

Run: `hrctl explain "I want to raise a grievance about my manager" --tenant acme_labs`
-> the rule that fires, the facts, the mode, the human-readable reason.

### Step 4 — Kernel loop end to end (~2 days) *

Whole pipeline wired with stubs where the clever parts are not built yet: lexicon-only
classification, simple retrieval, pure-template drafting, deterministic verification.

Run: `hrctl run "How many public holidays do I get?" --as emp_001 --tenant acme_labs`
-> full trail: classification, facts, rule, citation, draft, verify result, outcome, audit ids.

**A working HR agent with no AI in it at all.** Everything after this makes it better, not
makes it exist.

### Step 5 — Policy service (~3 days)

Markdown to citable sections. `versions.yaml` carries effective dates, status, owner, review
cadence. In-force resolution, segment scoping, BM25 retrieval, citation objects (version plus
section plus exact quoted text). Two in force means refuse. Retired versions unquotable.

Run: `hrctl policy search "parental leave" --tenant X` -> ranked citations with versions.

### Step 6 — Verify gate, complete (~2 days)

Every check from section 4. Run: `hrctl verify --work-item 42` -> per-check pass/fail, so a
failure is explainable rather than mysterious.

### Step 7 — Classification (~3 days)

Lexicon and rules first, confidence scoring, compound-request splitting, entity extraction,
sensitivity ladder. Then an optional local-model fallback behind the provider interface,
default off.

Run: `hrctl classify "..."` with the model on and off; both must pass the same tests.

### Step 8 — Tool adapters (~4 days)

The SPI: each adapter declares owned fields and supported operations. `prepare()` returns a
faithful preview and writes nothing; `commit()` writes once under an idempotency key. Mock
HRIS, calendar, ITSM, document store, notifier. No adapter can write pay or grant access.

Run: `hrctl tools list` -> capability matrix.

### Step 9 — Approvals and roles (~3 days)

Queue filtered by role; approve, send back, take over; delegation; nobody approves their own
request; and **re-check the citation at approval time**, so a draft written against a policy
that has since changed is blocked rather than sent.

Run: full lifecycle from message to approved send, via CLI.

### Step 10 — Web UI and API (~4 days) *

FastAPI serves JSON only. The UI is a React 18 + Vite client that copies the design system of
the user's existing finance-operations-agent (section 7a). Screens: chat, approval queue,
work-item trail, policies, rules, tools, audit, metrics, upload. Locked-down headers, escaped
output, role gating, refuses a public bind while role-switching is on.

Run: `hrctl serve` plus `npm run dev` -> open a browser -> actually use it. This is the demo.

### Step 11 — The five ops workflows (~5 days)

Document and forms intake (complete versus missing versus illegible, old-template detection);
onboarding readiness inside a lead window; offboarding checklist; expiry scanning with
reminder caps; interview scheduling with free/busy, buffers and local hours. Each reports gaps
rather than silently fixing them.

### Step 12 — Third company, config only (~2 days) *

`hrctl simulate --tenant X` runs golden cases and reports case-matrix coverage, citation
coverage, and any rule that could auto-send uncited. Then a **third** company is stood up from
config alone. If it needs one line of kernel code, the abstraction failed and is fixed here.

### Step 13 — Metrics, evals, hardening (~4 days)

Honest metrics (escalated cases count review time, not savings; mandatory caveats;
small-sample warnings); prompt-injection corpus; deepeval suite; model and retrieval upgrades
gated behind an eval re-run; retention and data-subject requests.

---

## 8. Checkpoints

| | When | What exists |
|---|---|---|
| 1 | end of Step 4, ~2 weeks | A working agent in the terminal: ask a question, watch the whole decision trail. No AI, no spend |
| 2 | end of Step 10, ~4-5 weeks | A browser app you can demo: chat, approval queue, audit trail, policy library |
| 3 | end of Step 12, ~6 weeks | A third company brought up by config alone — adaptability proven, not asserted |

Total roughly six weeks of focused work. Treat these as ordering, not a date commitment;
real velocity will be known after Step 3.

---

## 9. Why this order

- **Data model first**, because every later step writes to it and the flywheel columns are
  unrecoverable if added late.
- **Audit and redaction before anything that processes a message**, so no step ever runs
  unaudited or unredacted, even during development.
- **Rule engine before the kernel**, because the kernel's whole job is to obey it.
- **Templates before models**, so the system is provably correct with zero AI and the model
  becomes an upgrade rather than a dependency.
- **Verify gate before adapters**, so nothing can write to the world before the gate that
  guards writes exists.
- **Two companies from Step 1, a third at Step 12**, so adaptability is tested continuously
  and then proven.

---

## 10. Deferred decisions

| Question | When to decide | Why it can wait |
|---|---|---|
| Multi-tenant SaaS versus single-tenant self-host | Before Step 10 | `tenant_id` on every row and per-tenant config make both viable; the choice only affects deployment and isolation hardening |
| Whether to benchmark a local generative model on this machine | Before Step 7 | Nothing before Step 7 needs a model. A ~20 minute benchmark plus a 2.5 GB download turns the estimates in section 6 into measurements |
| Statutory or jurisdiction packs | When a customer requires one | The overlay slot exists and is empty; adding one is config |
| Real system integrations (versus mocks) | After Step 10 | The adapter SPI is the contract; mocks and real adapters implement the same interface |
| Channels beyond the web UI (Slack, Teams, email) | After Step 10 | Channels are adapters over the same kernel |

---

## 11. Environment notes

| Fact | Consequence |
|---|---|
| No `python3-venv` on this machine, and system Python is PEP 668 externally managed | System `pydantic 2.13.4`, `pyyaml`, `pytest 9`, `httpx`, `jinja2` are used directly. FastAPI and uvicorn are not installed yet and must be resolved before Step 10 (either `pip install --user --break-system-packages`, or `apt install python3.12-venv`) |
| Bare `node` on PATH is v5.12.0 (2015); Node 22 is installed via nvm | Client work must use `~/.nvm/versions/node/v22.23.4/bin` on PATH, or `nvm use 22`. Vite 6 requires Node 18+ |
| No NVIDIA GPU, CPU-only inference | Section 6 stands: small local models only, generative drafting is slow, and the templated path carries most traffic |

## 12. Progress log

### Step 0 — done (2026-11-01)

- `pyproject.toml`, `hr_agent/` package, `hr_agent/config.py`
- `hr_agent/schema.sql` — 19 tables. The `decision` table carries the flywheel columns from
  section 6, so the approval queue is a labelling machine from the first run.
- `hr_agent/db.py` — connection, schema loader, JSON helpers, UTC timestamps

### Step 1 — done (2026-11-01)

- `hr_agent/packs/models.py` — every pack file as a Pydantic model, `extra="forbid"` so a typo
  in a pack fails at load. `MODES` ordering is the tighten-only enforcement mechanism.
- `hr_agent/packs/loader.py` — layered resolution with provenance, `Loosened` guard, `config_hash`
- `hr_agent/packs/validate.py` — coherence checks: unknown facts, uncited auto-answers, escalation
  roles nobody holds, two unscoped versions in force, segments matching on pay, and more
- `packs/base/` — taxonomy (10 intents, 20 facts), 7 case types, **30 rules**, generic redaction pack
- `packs/tenants/acme_labs/` and `packs/tenants/meridian_group/` — the two mismatched seed companies
- `hr_agent/cli.py` — `hrctl init | tenants | validate | show config|rules|policies|cases|redaction`
- `tests/` — 46 tests green. Mutation-checked: disabling the tighten-only guard fails 5 of them.

### Step 2 — done (2026-11-02)

- `hr_agent/redact.py` — pattern-pack engine. `text()` masks identifiers keeping the class label;
  `mapping()` drops sensitive fields by name, matching whole words so `bank_holiday` and
  `tokenization_policy` survive while `api_token` and `bank_details_v2` go. `guard()` refuses to
  run at all with redaction off, and `assert_clean()` is the outbound check.
- `hr_agent/audit.py` — one hash chain per tenant. `verify()` recomputes every row and names the
  first inconsistency, distinguishing an edited row from a removed one. Detail is redacted before
  it is written, and the redactor is a required argument so there is no quiet path around it.
- `hrctl audit verify | audit trail | redact` added.
- 77 more tests (123 total).

Five real bugs were found and fixed while building this, all of them in the pattern pack rather
than the engine:

| Bug | Why it mattered |
|---|---|
| `phone` listed before `card_number` | A card number came out as `[phone] 1111`, leaking the last four digits |
| Sentence-final period defeated the phone lookahead | `call +44 7700 900123.` was not masked at all |
| Keyed patterns allowed no filler word | `account number is 12345678` was not masked |
| Keyed patterns replaced their own lead-in | `call me on 07700 900123` became a bare `[phone]`, destroying the sentence |
| Date-of-birth value used a greedy character class | `my DOB is 14/03/1991 and my payroll ref` masked as far as `payrol` |

Two honest limits are recorded in tests rather than hidden:
`test_a_removed_last_row_is_not_detectable_and_that_is_stated` (truncating the end of a chain
leaves a valid chain; detecting it needs an external anchor), and the deliberate decision that an
unkeyed date is never masked, because HR text is full of legitimate dates.

### Steps 3 to 10 — done (2026-11-02)

| Step | Module | Note |
|---|---|---|
| 3 rule engine | `engine.py` | First match wins; a predicate that cannot be evaluated closes the gate. A test reads the AST to prove nothing it imports or calls can reach a model |
| 4 kernel loop | `kernel.py` | The whole pipeline. Compound messages split into child work items with their own trails |
| 5 policy service | `policy.py` | Markdown to citable sections, in-force resolution, scope, BM25 retrieval, exact quotes |
| 6 verify gate | `verify.py` | Twelve checks, each pass/fail/skip with a reason, stored on the decision and drawn in the UI |
| 7 classification | `classify.py` | Rules and lexicons only. No model is called anywhere in this build |
| 8 adapters | `tools.py` | read / prepare / commit, idempotency keys, field ownership, pay and access unwritable |
| 9 approvals | `approvals.py` | Roles, delegation, no self-approval, citations re-checked at the moment of approval |
| 10 web | `web.py` + `client/` | JSON API and a React dashboard in the finance-operations-agent design system |

**Both companies pass all their own golden cases (13/13 and 12/12).** `README.md` has the
three commands to run the demo.

Things found and fixed while building, each one a real defect rather than a typo:

| Found | Why it mattered |
|---|---|
| No rule for HR changing another person's record | `A3` covered managers only, so an HR partner fell through to the catch-all and got no draft — the agent refusing to help the people it exists for. Added `A4`, plus golden case AC-13 |
| Dedupe keyed on message content | An employee could never ask the same question twice: the second was silently suppressed. Replay protection now keys on the delivery, which a channel supplies |
| `policy_found` is a fact, but retrieval came after the decision | The probe has to run first. It is read-only by construction, and the rail now draws that order rather than the tidy-looking wrong one |
| The decision row was written at the end | Prepared actions reference it, so a crash between decide and act lost the record of what was decided. It is written when the rule fires |
| A view remounted on every action | The React key included a counter that changed on each run, discarding the result it had just received |
| "Am I entitled to X" always read as personal | `How many days am I entitled to?` asks for the policy's figure. Reading it as personal sent routine questions to a human for no reason |

### Next

**Step 11** — the five ops workflows (forms intake, onboarding readiness, offboarding checklist,
expiry scanning, interview scheduling). **Step 12** — a third company brought up by config alone.
**Step 13** — evals, observability, retention.
