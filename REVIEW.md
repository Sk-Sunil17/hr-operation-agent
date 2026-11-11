# Review — what to fix, in what order

> **Status: sections A, B and C are implemented, plus D1, D2 and D4.**
> 343 tests pass (was 184), and all three companies pass their own golden cases
> (13/13, 12/12, 12/12). See [the implementation log](#f-what-was-actually-done)
> at the bottom.
>
> | | | Status |
> |---|---|---|
> | **F1** | Take-over blocked unrelated requests | fixed — scoped to the case |
> | **F2** | Readiness check invented findings | fixed — computed from a declared checklist |
> | **F3** | Record changes lost the new value | fixed — extracted, or stated as missing |
> | **F4** | `prepare` outcomes drafted as handovers | fixed — real branches + a class-level test |
> | **F5** | No error handling in the kernel | fixed — failures audited and handed over |
> | **P1** | Classify O(people): 131 ms at 2,500 | fixed — **0.55 ms**, 239× |
> | **P2** | Retrieval re-tokenised every query | fixed — **1.8 ms** at 1,080 sections, 19× |
> | **P3** | Schema script on every request | fixed — once per process |
> | **P4** | Only one citation ever quoted | fixed — up to two, by score margin |
> | **D1** | Free-text rejections | done — eight reason codes, each pointing somewhere |
> | **D2** | "What will we answer in January?" | done — and it needed a real fix first |
> | **D4** | Unclear requests got a shrug | done — a question, drafted for one-click approval |
>
> | **C** | Forms and document intake | done — templates per company, blank vs illegible |
> | **C** | Offboarding checklist | done — declared per company, computed where a system can answer |
> | **C** | Expiry scanning and reminders | done — four scheduled jobs, caps counted |
> | **C** | A third tenant | **done — config only, and it found a bug** |
> | **C** | Authentication | done — opt-in; role switching is off whenever it is on |
> | **C** | Pagination | done — keyset, not offset |
>
> Still outstanding: suggestions **D3**, **D5** and **D6**.

Written after reading the code and measuring it, not from memory. Every claim
below has evidence next to it. Numbers come from this machine.

**Summary:** the spine is sound — the rule engine, the verify gate, the pack
layering and the audit chain all do what they say, and 184 tests hold them
there. The problems are in the layer above: five correctness bugs, three
avoidable performance walls, and a set of workflows that have rules but no
behaviour behind them.

One finding stands out and should be fixed before this is shown to anyone who
might buy it. It is **F2**.

---

## A. Correctness — fix before any pilot

### F1 · A take-over blocks that person's unrelated requests, forever
**Severity: high.** `hr_agent/facts.py`, `_open_case`

`case_taken_over` is resolved from *the most recent open case of any kind* for
that person. So an HR partner taking over a grievance silently gags the agent
for everything else that employee ever asks.

```
grievance case opened and taken over: kind=sensitive_matter
he then asks about annual leave  ->  rule T1, mode hand_over
BUG: an unrelated routine question is blocked by the grievance take-over
```

That is the opposite of the intent. Taking over a matter should stop the agent
*on that matter*. Right now, every employee who has ever raised anything
sensitive gets a degraded service permanently, and nobody would notice for
months.

**Fix:** scope the take-over to the case the request belongs to, not the person.
Link `work_item.case_id` on the way in, and have `case_taken_over` ask *"is
**this** case taken over?"*. Where a request has no case yet, it is not
taken over. Add a test asserting a taken-over grievance leaves routine questions
untouched.

---

### F2 · The onboarding readiness check reports invented findings
**Severity: high — and it is the worst thing in the codebase.** `hr_agent/kernel.py:379`

```python
gaps = [
    "right to work documents not yet verified",
    "bank details for payroll not yet supplied",
    "emergency contact missing",
]
```

Three hardcoded strings. It reports them for every new starter, at every
company, whether or not any of it is true. In the demo it tells you Lena
Fischer is missing her emergency contact. Nobody checked.

This matters more than a normal bug because of what this product claims. Every
other guarantee here is real and tested — and this one screen quietly invents
findings about a named person. If a buyer's technical reviewer opens this file,
the honesty argument that the whole pitch rests on takes the damage, not just
the feature.

**Fix, in order of preference:**
1. Compute it: read the required items from the onboarding policy, check each
   against the HRIS adapter, report what is actually missing.
2. If that is not in scope yet, **remove the feature**. Have `O1` hand over
   with *"a readiness check is not configured for this company"*. An absent
   feature costs nothing; a lying one costs the argument.

Do not ship it as-is. If it must stay for the demo, say out loud that it is
placeholder data.

---

### F3 · A record change never captures the new value
**Severity: medium.** `hr_agent/kernel.py:373`

```
home_address: (not set)  ->  (as described in the request)
```

The agent identifies the field and the owning system correctly, then writes a
placeholder instead of the address. The approver has to read the original
message and retype it, which removes most of the saving the feature exists for.

**Fix:** extract the value during classification. A field-value pattern
(`change my X to Y`, `my new X is Y`, `X: Y`) covers most of it; when nothing
is found, say so explicitly — *"the new value was not stated; ask the employee"*
— rather than writing a placeholder that looks like data.

---

### F4 · Some `prepare` outcomes are drafted as handovers
**Severity: medium.** `hr_agent/kernel.py`, `_compose`

`_compose` has branches for enquiry, admin, onboarding and status. Document,
interview and offboarding have none, so they fall to the generic `else`, which
writes a handover:

```
I1  prepare  | I am passing this to a HR Partner rather than answering it
             <-- the rule says prepare, the draft says handover
```

The approver opens a queue item whose text says a person has already taken it.
The mode and the words disagree.

**Fix:** either write the missing branches (see **C**), or make the fallback
honest: *"This needs a person to decide. Here is the request and the facts."*
Then add a test asserting no `prepare` outcome ever contains handover language —
this class of mismatch should be caught, not spotted.

---

### F5 · The kernel has no error handling at all
**Severity: medium.** `hr_agent/kernel.py` — zero `try` blocks

If an adapter raises — a timeout, a 500 from a real HRIS, a malformed record —
the exception leaves `kernel.run()`. The work item stays `open`, no `verify` or
`act` row is ever written, the API returns a 500, and the employee hears
nothing. The audit trail stops mid-sentence, which is exactly where it is least
acceptable for it to stop.

**Fix:** wrap the act phase. On failure: cancel prepared plans, write an audit
row at stage `act` recording the failure, set the work item to `failed`, and
hand over to the verification-failure role. The rule the plan set out —
*a capability failure must not look like a refusal* — needs the failure path to
exist before it can be honoured.

---

## B. Performance — two real walls, one silly one

Measured on this machine. The agent is fast at demo scale and stops being fast
at the scale its own reference tenant describes.

### P1 · Classification is O(people), with a regex per person
**`hr_agent/classify.py`, `_entities`**

| Directory | Time to classify one message |
|---|---|
| 14 people | 0.39 ms |
| 200 | 1.73 ms |
| 1,000 | **54.6 ms** |
| 2,500 | **131.2 ms** |

For every message it loops the whole directory and runs two `re.search` calls
per person, each with a freshly escaped pattern. Meridian Group's own manifest
describes 2,500 people. The shipped reference tenant is the one that breaks.

**Fix:** build the name index once per resolved config and cache it — a single
compiled alternation of all names, or a token-set lookup over the message. Both
are O(message length). Expect ~0.5 ms at any directory size.

### P2 · Retrieval re-tokenises the entire corpus on every query
**`hr_agent/policy.py`, `search`**

| Sections | Time per query |
|---|---|
| 36 | 1.4 ms |
| 360 | 12.2 ms |
| 1,080 | **34.8 ms** |

Every search re-tokenises every section and recomputes document frequencies
from scratch. A real HR corpus is hundreds of sections.

**Fix:** build the tokenised corpus, lengths and IDF table once, keyed by
`(tenant, config_hash)`, and invalidate it when `load_corpus` runs. The config
hash already exists and already changes when the corpus does, so the
invalidation key is free. Expect roughly a 10× improvement, and it scales.

### P3 · Every API request recreates the database schema
**`hr_agent/web.py`, `connect()`**

Ten routes call `db.open_db()`, which runs the full `schema.sql` script —
twenty `CREATE TABLE IF NOT EXISTS` statements — on every single request.

```
db.open_db()  (what every route calls)  1.55 ms
db.connect()  (no schema script)        0.68 ms
```

More than half the cost of a cheap request, spent proving tables exist.

**Fix:** run `init` once at startup; routes call `connect()`. Better still, hold
one connection per worker. Small change, and it removes a per-request cost that
will only grow as the schema does.

### P4 · Only ever one citation is used
**`hr_agent/kernel.py:231`** — `probe.citations[:1]`

Retrieval returns up to three ranked sections and the agent quotes the first.
Many real questions are answered by two sections — the allowance *and* the
carry-over rule. The verify gate already supports several citations and already
rejects mixing versions of one policy.

**Fix:** quote up to two when the second scores within a margin of the first.
Low risk, immediately better answers.

---

## C. Missing — rules exist, behaviour does not

The case matrix has rules for documents (`D1`–`D4`), offboarding (`X2`),
scheduling (`I1`) and scheduled jobs (`J1`). The rules fire. Nothing is behind
them. From the simulate output:

```
rules never reached: A2, D1, D2, D3, D4, E0, E4, J1, O2, T1, X1, X2, Z1
```

Half the matrix is untested by any company's golden cases, which means those
paths have never run outside a unit test.

| Missing | Why it matters |
|---|---|
| Forms and document intake | The biggest real HR time sink, and the one the plan named as the differentiator |
| Offboarding checklist | `X2` prepares nothing today |
| Interview scheduling | The calendar adapter has free/busy and nothing calls it |
| Expiry scanning and reminders | `reminder_cap_reached` is a declared fact that nothing ever sets |
| A third tenant | Adaptability is proven across two companies. Three is the plan's own test |
| Authentication | Role switching with no auth. Fine for a demo, blocking for a pilot |
| Pagination | `work-items` returns up to 200 rows and no cursor |

---

## D. Suggestions worth more than the bug fixes

Things that would make this better as a product, not just more correct.

**1 · Structured reason codes on send-back.**
Today an approver types free text. A short list — *wrong policy, wrong person,
tone, missing information, factually wrong* — turns every rejection into a
labelled error class. The flywheel columns already exist; this is what makes
them usable without anyone reading a thousand notes.

**2 · "What will we answer in January?"**
The policy service already understands effective dates. Let someone run the
golden cases against a future date, and a company can see what their own agent
will say the day a new policy takes effect — before it does. Nothing else in
this market can do that, and it falls straight out of the versioning that is
already built.

**3 · Tell the employee why, in their own words.**
Every rule carries a human-readable reason and it is shown to HR but not to the
person affected. For a refusal, showing it is the difference between a system
that feels obstructive and one that feels fair.

**4 · Let `E4` actually ask the question.**
A low-confidence request goes to a person today. For a routine, non-restricted
request, one clarifying question would resolve most of them without a human.
The guardrail is already there — restricted matters are never asked anything.

**5 · Spend the per-tenant model budget on reranking, not drafting.**
If a model is ever introduced, the first place it earns its keep is choosing
which retrieved section to quote. That is a bounded judgement the gate can
check. Drafting is where the quoting design already removes the need.

**6 · Record which rules a company has never reached.**
`simulate` reports it per run. Tracked over time in production it becomes a real
signal: a rule nothing ever reaches is either dead configuration or a gap in
what the company's employees are being allowed to ask.

---

## E. Order of work

**Before any pilot**
1. **F2** — compute the readiness check or remove it
2. **F1** — scope take-over to the case
3. **F5** — failure path in the kernel
4. **F4** — fix the prepare/handover mismatch, with a test to prevent the class

**Before a company with more than a few hundred people**
5. **P1** — cache the name index
6. **P2** — cache the tokenised corpus
7. **P3** — initialise the schema once

**Before it is a product**
8. **F3** — extract the value for record changes
9. Document and forms intake (plan step 11)
10. Authentication
11. A third tenant, by config alone (plan step 12)

**Then the improvements in D**, of which 1 and 2 are the ones that change what
the product is worth rather than how well it works.

---

## What is genuinely solid

Worth stating, because the list above is long and the foundations are not the
problem.

The tighten-only guarantee is real and enforced at load time. The verify gate
catches what it claims to, and caught two bugs during development that testing
had not. The audit chain detects both edits and deletions, and the append-only
rule is enforced across the whole codebase by a test that has already refused
my own code once. The rule engine cannot reach a model, proven by reading its
syntax tree rather than by convention. Redaction runs before anything is stored
and the system refuses to start without it. The metrics refuse to claim savings
nobody substantiated — and the caveat list found a real citation bug in the
process.

That is the hard part, and it is done. What remains is mostly the ordinary work
of turning a correct core into a complete product.


---

## F. What was actually done

### F1 · Take-over scoped to the case

`case_taken_over` and `has_open_case` now resolve through
`facts.case_kind_for()`, which derives the mapping **from the rules** rather
than from the case list — the rules are what open cases, so the two cannot
disagree. A plain enquiry belongs to no case, which is the right answer: there
is nothing for a take-over to apply to until a person opens something.

The kernel also joins an existing open case of the same kind instead of opening
a second one, so three messages about one grievance are one case.

```
unrelated question after a taken-over grievance -> E1/auto        (was T1)
follow-up to the same grievance                 -> T1/hand_over   correctly stopped
an admin request from the same person           -> A1/prepare     unaffected
```

### F2 · Readiness computed, not invented

New `hr_agent/readiness.py` and a `readiness.yaml` per company. Each item names
a field an adapter owns or a document that has been filed — something a system
can answer. The pack validator rejects an item nothing can answer, because an
unanswerable item would be reported as outstanding for every new starter for
ever.

A company with no checklist gets a handover saying so. An empty list must never
read as a clean bill of health.

13 tests, including `test_no_finding_is_hardcoded_in_the_source`, which greps
the kernel for the three strings that used to be there.

### F3 · The new value is taken from the message

Two frame strengths. **Strict** (`change my X to Y`, `X: Y`) trusts what
follows. **Loose** (`my X is Y`) also matches *"my phone is broken"*, so it
requires the value to look like data and rejects anything whose leading phrase
describes a problem.

Where no value was stated, nothing is prepared and the reply says so. A
placeholder that reads like data is how an approver ends up committing
`(as described in the request)` into somebody's record.

This surfaced a real tension: the gate's *no identifiers* check now failed,
because a preview must contain the value or it cannot be checked. Resolved by
distinguishing an **echo** from a **leak** — an identifier the person supplied
themselves, repeated back to confirm, is not a leak; one they did not supply
came from a record and is leaving.

### F4 · Outcomes worded as what they are

Real branches for interview (free/busy, then a proposed hold), document intake
and offboarding, plus `draft.needs_a_person()` as an honest fallback. A
parametrised test asserts **no `prepare` outcome anywhere contains handover
language**, so the class of mismatch cannot return.

Two gaps fell out of this. A document arriving now outranks what the document is
*about* (`"I have attached my completed new starter form"` was being read as an
onboarding question), and a new rule **D0** handles a message that claims an
attachment when none arrived — previously the catch-all, which told the person
"nothing matched" rather than the one thing they could fix.

### F5 · Failures are handed over, not raised

The act phase is wrapped. On failure: prepared actions cancelled, an audit row
written at stage `act`, work item set to `failed`, handover to the
verification-failure role, and a reply that reads *"a system I needed did not
respond"* — deliberately distinct from *"I am not able to do this"*, so an
employee can tell **could not** from **will not**. A test asserts those two
replies differ.

### P1–P3 · Measured

| | Before | After |
|---|---|---|
| Classify, 2,500-person directory | 131.2 ms | **0.55 ms** |
| Retrieval, 1,080 sections | 34.8 ms | **1.81 ms** |
| `open_db()` after the first call | 1.55 ms | **0.83 ms** |

Twelve tests in `test_caching.py` cover invalidation rather than speed: a cache
that serves yesterday's policy is a worse defect than the slowness it replaced.

### D2 · "What will we answer in January?" — and the bug behind it

This did not work, and finding out why exposed a real modelling gap: a
`scheduled` version never became live. Its status had to be changed by hand on
the morning it took effect, which is exactly how an agent ends up quoting last
year's rules.

Now **dates decide, not statuses**. A scheduled version becomes quotable on its
effective date and supersedes the one before it, with no `effective_to` to
remember. `draft` and `retired` stay excluded whatever the calendar says,
because those are human decisions.

That changed what a conflict means, so the validator changed with it: two
versions with *different* start dates is ordinary supersession; two claiming the
*same* start date for the same population is the ambiguity the agent refuses to
answer past.

```
asked on 2026-11-02:  POL-FLEX v1.0 — "at least three days each week"
asked on 2027-02-01:  POL-FLEX v1.1 — "at least two days each week"
```

### D1 · Rejections are countable

Eight reason codes, each mapped to the part of the agent it implicates
(`wrong_policy` → retrieval, `tone` → drafting, `needs_a_conversation` → the
rules may need tightening). Sending back requires one. Metrics count them, and
raise a caveat when three or more point at the same place — a pattern should
lead somewhere rather than accumulate.

### D4 · An unclear request arrives with a question ready to send

`E4` previously required `intent: enquiry`, but a message too vague to act on is
usually too vague to classify — so the rule was unreachable and every unclear
message fell to the catch-all. Broadened to fire on low confidence alone, still
after `R1` and `W1`.

The agent drafts the question; it does not ask it. Asking is an outbound
message, and letting a low-confidence path send one on its own would widen what
the agent may do without a person. A test asserts a restricted message is still
never asked anything.


---

## G. Section C

### Forms and document intake

`hr_agent/forms.py` plus a `forms.yaml` per company. Detect by phrase, read
against declared fields and their aliases, judge complete or not.

Three distinctions do the work:

- **Blank is not illegible.** "They left it empty" and "I could not read it"
  call for different actions, and collapsing them loses the one fact that
  decides which. Twenty-two tests cover the ways a person writes nothing —
  `N/A`, `nil`, `TBC`, `-`, `?`, `[blank]` — because treating any of them as a
  value files "N/A" as somebody's bank account.
- **Restricted is a property of the type.** A medical certificate is never
  read, extracted or summarised, whatever it turns out to contain. Deciding
  that by reading it means reading it.
- **An unexpected label is surfaced, not dropped.** A form with an extra field
  on it is usually a form that has changed. Page furniture — `Page 2 of 2`,
  `For office use only`, `Signed:` — is not.

Expiry comes from the template (`expires_after_months`), so a right-to-work
check filed today is known to run out in two years without anyone diarising it.

A sensitive field is reported as *"filled in (not repeated here)"* rather than
quoted back. An approver confirming a form was completed does not need bank
details copied into a reply.

### Offboarding

`exit_checklist.yaml` per company. Items a system can answer are answered;
items it cannot are listed as needing a person, with the role that owns each.
The reply ends by saying nothing in this system can revoke access, because
nothing in it can.

### Scheduled jobs

`hr_agent/scheduler.py`: expiry scan, readiness, policy review, reminders.
Each goes through `kernel.run_job`, which means the rule engine, the verify
gate and the audit chain — a scheduled thing that bypasses the guardrails is
exactly the thing that will quietly act at three in the morning, and the audit
row will say `scheduler` where a name should be.

Reminder caps are counted in a table rather than assumed. Past the cap the
report says *"these need a person, not another reminder."*

Two bugs fell out of building this:

- A cron firing twice crashed on the event dedupe key instead of recognising a
  repeat. Now the same job, for the same subject, on the same day, is one piece
  of work.
- Every job was **refused** by `W1`, the rule about asking after other people's
  records — because a scheduled job has no requester, which is not the same as
  asking about someone else. A new fact, `has_requester`, makes the distinction
  explicit rather than implied.

### A third company, config only

`packs/tenants/harbour_clinic/` — 17 files, no Python. A 300-person clinic
running shifts around the clock, with a union, two sites, entirely different
role names (`charge_nurse`, `practice_manager`, `people_officer`,
`clinical_lead`), and the tightest configuration of the three: **no rule may
act alone**.

A test asserts the word "harbour" appears nowhere in `hr_agent/`, and another
asserts no role name is shared by all three companies — so nothing in the
kernel can quietly assume one.

**It found a bug, which is what a third company is for.** Retrieval counted a
section as an answer on a single common term, so *"What is the parking
arrangement?"* matched the leave policy on the word "arranged", scored above
the floor, and would have been quoted at someone asking about parking.

That is the one failure the verify gate cannot catch: the gate checks a
citation is real, current and quoted accurately, and has no way to know the
passage does not answer the question. Relevance has to be decided in retrieval,
so a section now needs two distinct query terms before it counts.

The fix was not Harbour-specific — it improved Acme too, where the notice
question had been matching the remote-working policy instead of the leave
policy. All three companies now pass every golden case: **37 of 37**.

### Authentication

Opt-in via `HR_AUTH`, and `DEMO_ROLE_SWITCHING` is defined as its inverse, so
the two can never both be on. With it enabled the actor comes from a signed
session and a request body cannot claim to be somebody else — approving as
another person is the one thing the queue exists to prevent, and there is a
test for exactly that.

No role is stored with an account: roles come from the directory, so there is
one place to change what someone may do rather than two that drift. A failed
sign-in never says which half was wrong, and a missing account costs the same
time as a wrong password.

The honest limit, written in the module rather than discovered: a signed token
cannot be revoked before it expires.

### Pagination

Keyset on `(created_at, id)`, not offset. An offset skips or repeats rows when
new work arrives mid-scroll, which on this list it constantly does. An
unreadable cursor is a 400, not a crash.
