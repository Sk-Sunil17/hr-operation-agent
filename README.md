# HR Operations Agent

A policy-grounded, approval-gated HR operations agent. The kernel is code and is
identical for every customer; **every company is a declarative pack**.

Two properties define it:

1. It cannot send a claim it cannot cite to a policy version in force.
2. A company's pack may only ever **tighten** what the base rules allow. The
   loader refuses to load one that loosens, so no configuration and no injected
   instruction can unlock a send.

No model is called. Replies are templates wrapped around policy text quoted
verbatim, and the verify gate checks the quote is really there.

- **[DEMO_SCRIPT.md](DEMO_SCRIPT.md)** — presenting this to someone? Word-for-word
  script, what to click, the questions you'll be asked, and what to do if it breaks.
- **[THE_STORY.md](THE_STORY.md)** — one morning at the company, told as a story.
  The shortest route to understanding what this thing actually does.
- **[HOW_IT_WORKS.md](HOW_IT_WORKS.md)** — the same flow as a reference: stage by
  stage, traced through real requests, with the code behind each one.
- **[PLAN.md](PLAN.md)** — the reasoning: the gaps being solved, the market
  analysis, the model strategy, and the build order.

---

## Run the demo

**One command** loads the companies, seeds each with a morning of traffic, builds
the dashboard if it is missing or out of date, and serves everything on one port:

```bash
python3 -m hr_agent.cli up            # open http://127.0.0.1:4000
python3 -m hr_agent.cli up --reset    # the same, with every company re-seeded fresh
```

Or step by step:

```bash
# 1. Build the database and load both companies' packs
python3 -m hr_agent.cli init

# 2. The whole agent, end to end, narrated. One command, nothing to click.
python3 -m hr_agent.cli walkthrough acme_labs

# 3. Fill the screens with each company's own test cases
python3 -m hr_agent.cli demo acme_labs
python3 -m hr_agent.cli demo meridian_group --reset
python3 -m hr_agent.cli demo harbour_clinic --reset

# 4. Build the dashboard once (needs Node 20+)
(cd client && npm install && npm run build)

# 5. Start the server: dashboard and API on one port
python3 -m hr_agent.cli serve      # open http://127.0.0.1:4000
```

`walkthrough` runs nine steps against the real database: it decides without
acting, answers a routine question, prepares a record change and shows the
record untouched, tries to approve it as the wrong people, approves it properly,
takes a restricted matter, absorbs four prompt injections, runs the same
question at the other company, breaks an audit chain on a throwaway copy, and
ends on the metrics with their caveats. Everything it does is then clickable in
the dashboard.

One server, one port: `/api/*` is the JSON API and every other path is the
dashboard. Only if you are changing the dashboard itself, run `cd client && npm
run dev` for hot reload on <http://localhost:5173>; it forwards `/api` to the
server on 4000.

### Deploying it

`DEPLOY.md` has the full walkthrough. The short version: one always-on
container serves both the dashboard and the API (Render, Railway, Fly — there is
a `Dockerfile` and a `render.yaml`). It is deliberately not serverless: the demo is stateful, and a
platform that discards the filesystem between requests breaks the approval
sequence the product exists to show.

One thing to read before hosting it: the dashboard's role switcher lets anyone
act as anyone, so the server refuses a public bind until you acknowledge that
explicitly with `HR_PUBLIC_DEMO=anyone-can-act-as-anyone`. A truthy value is not
enough, on purpose.

### What to try, in order

| Screen | Try this | What it shows |
|---|---|---|
| **Ask** | "How many days of annual leave do I get in a year?" | Answered on its own, quoting Leave Policy v1.2 §2 verbatim, every safety check passed |
| **Ask** | "My manager has been making comments about my accent" | Handed to a named person in seconds. No answer drafted, no clarifying question, a case opened |
| **Ask** | Switch **Company** to Meridian Group, ask the leave question again | Same rule `E1`, different outcome: queued for a person. Same kernel, zero code difference |
| **Ask** | "What is Priya's home address?" | Declined, with the reason and what to do instead |
| **Ask** | Open **How the agent decided** under any result | The rule that fired, the seven stages, and every safety check |
| **Ask** | "Please arrange a technical interview with Astrid Lindqvist (astrid.l@example.test) for the Head of Finance role, with Priya and Sam." | Three calendars read across four working days, a time proposed with two alternatives, and the calendar hold, the candidate's email and the panel's brief all prepared. Plus every day it ruled out, and why |
| **Ask** | Click the **Documents** tile (it attaches the form for you) | The form read against this company's own template, every field read back to be checked — and the bank details reported as *filled in, not repeated here* |
| **Ask** | Delete one line from that form and run it again | Names exactly what is missing, files nothing, chases nobody |
| **Requests** | As HR, **Start a leaver** for Tomas Brandt with a last day and a reason, then **Prepare checklist** | The agent never starts an exit on its own. Once HR opens the case it drafts the company's departure checklist for approval, saying which items a system can confirm and which need a person. **Close** ends the case; both steps are audited |
| **Policies** | "Can I carry over unused leave?" | The passage that answers it, quoted word for word, and **Read policy** to open any policy in force in full. Retired versions are never shown |
| **Interviews** | — | What is actually booked, and a read-only "when is this panel free" that runs the same solver the agent does |
| **Approvals** | Approve a prepared change | Nothing touched a real system until this click. Watch the record change appear |
| **Approvals** | Approve the interview | One decision books the calendar entry AND emails the candidate with an `.ics` attached. Splitting it would make "booked, nobody told" reachable |
| **Rules** | As an HR person, **Change** "Routine policy questions" to *Draft for approval* | Before saving, it shows which sample requests would be handled differently. Save with a reason and the next leave question waits for approval. Options looser than the company rulebook are greyed out, and relaxing a rule you tightened needs a second HR person |
| **Insights** | **Run the sample requests** | Usage, outcomes, the audit chain check, and the company's own sample requests through the real kernel |
| **Acting as** | Switch to an employee such as Maya | Only *Ask*, *My requests* and *Policies*: employees see their own requests and nothing else |

### Who can change the rules

HR changes how each kind of request is handled from the **Rules** tab; no file
editing and no restart. The company's rulebook (`packs/tenants/<company>/rules.yaml`)
stays the floor: the dashboard can make a rule stricter at once, can never make it
looser than the rulebook, and relaxing a rule takes a different HR person from the
one who tightened it. Every change needs a reason and goes on the audit trail.

---

## The command line

```bash
hrctl init                                   # create the database, load every pack
hrctl tenants                                # the companies on disk
hrctl validate [company]                     # is this pack coherent
hrctl show config|rules|policies|cases|redaction <company>
hrctl explain <company> "<message>" --as <person>    # decide, change nothing
hrctl run     <company> "<message>" --as <person>    # do it for real
hrctl queue   <company> --as <person>        # what is waiting
hrctl decide  <company> <approval> approved|sent_back|taken_over --as <person>
hrctl integrations [company] [--check]       # which products are connected, and every one that could be
hrctl leaver  <company> <person> --as <hr person> --last-day 2026-12-11 --note "Resigned"  # open an exit case
hrctl policy search <company> "<query>"      # what would be quoted, and why it matched
hrctl simulate [company]                     # the company's own golden cases
hrctl audit verify                           # recompute every hash
hrctl redact  <company> "<text>"             # what the redaction pack does
hrctl walkthrough [company]                  # the whole agent end to end, narrated
hrctl jobs    <company> [job]                # the scheduled checks (they report, change nothing)
hrctl interviews <company>                   # what is booked, and anything about it needing a person
hrctl availability <company> <ids> --stage <stage>   # when a panel is free; books nothing
hrctl account <company> <person>             # create a sign-in (only used when HR_AUTH=on)
hrctl demo    <company> --reset              # seed a morning of traffic
hrctl serve                                  # the JSON API on 127.0.0.1:4000
```

Run it with `python3 -m hr_agent.cli <command>`, or `pip install -e .` for the
`hrctl` name.

---

## How it works

```
  receive -> understand -> find policy -> decide -> draft -> verify -> act -> audit
```

- **receive** — the payload is stored before anything reads it
- **understand** — intent and sensitivity, from rules and lexicons. A model may
  only ever RAISE sensitivity
- **find policy** — read-only probe. Only versions in force today, scoped to the
  person. It runs before the decision because "is there a policy" is a fact the
  rules test
- **decide** — a deterministic rule engine picks one of four modes from facts
  gathered from systems. No model is consulted, which is why text inside a
  message cannot change what the agent may do
- **draft** — templates, quoting policy verbatim
- **verify** — twelve mechanical checks. A failure escalates to a person; it
  never degrades into sending
- **act** — send, queue, hand over, or decline. Everything that reaches the world
  goes through prepare → commit under an idempotency key
- **audit** — one hash chain per company, with the configuration fingerprint on
  every row

### The four outcomes

| | When | What the employee sees |
|---|---|---|
| **auto** | Routine, policy in force, citation verified | An immediate answer quoting the policy |
| **prepare** | Personal, or any real-world write | "HR will confirm shortly"; a person approves, edits or sends back |
| **hand over** | Restricted, or verification failed | A warm acknowledgement and a named human. No answer drafted |
| **refuse** | Someone else's record, or no governing policy | A plain explanation, and what to do instead |

---

## Layout

```
hr_agent/           the kernel: identical for every customer
  schema.sql        25 tables; `decision` carries the human-verdict columns
  packs/            pack models, layered loader, validator
  classify.py       intent and sensitivity, rules first
  policy.py         versioned corpus, in-force resolution, BM25 retrieval
  facts.py          what is true, from systems rather than from the text
  interviews.py     scheduling: reading a request, solving for a time, persistence
  ics.py            calendar invitations, so a candidate's email carries a real one
  connect.py        what every outside connector shares: credentials, transport, email
  gworkspace.py     Google Calendar and Gmail
  microsoft365.py   Outlook calendars (Teams links) and Outlook mail, via Microsoft Graph
  standard.py       any vendor by standard: SMTP mail, chat webhooks, REST HR and document systems
  engine.py         the rule engine. Imports nothing that could reach a model
  draft.py          templates that pass the verify gate by construction
  verify.py         the gate
  tools.py          adapter interface, prepare/commit, idempotency
  approvals.py      the queue, roles, delegation, re-checking citations
  kernel.py         the loop
  audit.py          hash-chained trail
  redact.py         pattern-pack engine
  metrics.py        measurement, with caveats that cannot be removed
  web.py            the JSON API
  cli.py            hrctl

packs/
  base/             taxonomy, 30 rules, case types, generic redaction pack
  tenants/
    acme_labs/        60 people, flat, loose
    meridian_group/   2,500 people, 3 entities, 5 populations, tight
    harbour_clinic/   300 people, shifts, a union, two sites, tightest of the three

client/             the dashboard (React + Vite)
tests/              416 tests
```

---

## Requirements

- Python 3.12 with `pydantic`, `pyyaml`, `pytest` (and `fastapi` + `uvicorn` for
  the API)
- Node 20+ for the dashboard only. On this machine, Node 22 is under nvm:
  `nvm use 22` — the bare `node` on PATH is an ancient v5

```bash
python3 -m pytest        # 416 tests
hrctl simulate           # every company's own golden cases
```

---

## Interviews

Scheduling is the one thing the agent does whose output lands in other people's
calendars and in a stranger's inbox, so it is worth saying exactly where the
lines are.

**What it does.** Reads free/busy for the panel, finds times that satisfy this
company's own notice period, buffer, daily cap, working hours and working days,
and prepares three things together: the calendar entry, an email to the
candidate with a real `.ics` invitation, and a brief for the panel. It also
handles moving one and calling one off, each of which withdraws something a
candidate was already promised and so has its own rule to tighten.

**What it never does.** It does not rank, screen or assess a candidate. It does
not book anything, email anyone, or take an hour out of a calendar without a
person approving it first. It computes no statutory notice: `min_notice_hours` is
a courtesy a company sets, not a legal calculation, which is the same reason
nothing else in the kernel does arithmetic on anybody's entitlements.

**Where the numbers come from.** `packs/tenants/<company>/interviews.yaml`.
Durations, stages, panel minimums, required roles, notice, buffer and the daily
cap are all configuration — the three shipped companies differ on every one of
them, and none of it is in the kernel.

**The security boundary.** Panel members are resolved against the directory, so
no address in a message can ever become an interviewer. The one address taken
from text is the candidate's — they are by definition not in the directory — and
it is written out in full in the preview an approver reads before anything is
sent. Where several addresses appear and none is attributable to a named
candidate, none is used and the reply says so.

**Real calendars and mailboxes.** See *Connecting each company's own products* below.

---

## Connecting each company's own products

Companies use different products, so the agent is never tied to one. Each
company names its own in `packs/tenants/<company>/adapters.yaml`; the kernel only
ever asks for "a calendar", "an HR system" or "something that can email a
candidate". Swapping Google for Microsoft, or either for something else, changes
that file and nothing else: the rules, the safety checks and the approval queue
are untouched.

| Job | Connectors (`implementation:`) |
|---|---|
| Interview calendars | `google_calendar` (Google Meet links) · `microsoft_calendar` (Outlook, Teams links) |
| Emailing candidates | `gmail` · `outlook_mail` · `smtp_mail` (any provider: Zoho, Fastmail, Amazon SES, SendGrid, Mailgun, an Exchange relay…) |
| Staff notifications | `webhook` with `format: slack`, `teams`, `google_chat`, `mattermost` or `discord` |
| Staff records (onboarding, record changes) | `rest_hris`: any HR system with a REST API (BambooHR, HiBob, Personio, Workday, Rippling, in-house), with a field mapping |
| Filing onboarding documents | `rest_docstore`: any document system, directly or through Power Automate / Zapier / Make to SharePoint, Google Drive or Box |
| Demo | `mock_*` for every job, used by the three shipped companies |

Example, for a company on Microsoft 365 with BambooHR and Slack:

```yaml
adapters:
  - name: calendars
    kind: calendar
    implementation: microsoft_calendar
    operations: [free_busy, prepare_invite, commit_invite, prepare_cancel, commit_cancel]
    settings: { organizer: recruiting@contoso.com, teams_meeting: true, credentials: CONTOSO_MS }

  - name: email
    kind: notify
    implementation: outlook_mail
    operations: [prepare_message, commit_message]
    settings: { from_address: recruiting@contoso.com, credentials: CONTOSO_MS }

  - name: people_system
    kind: hris
    implementation: rest_hris
    owns_fields: [phone, home_address, personal_email]
    operations: [read_person, read_manager, read_record, prepare_record_change, commit_record_change]
    settings:
      vendor: BambooHR
      base_url: https://api.bamboohr.com/api/gateway.php/contoso/v1
      auth: basic
      credentials: CONTOSO_BAMBOO
      read_path: /employees/{person}?fields=mobilePhone,address1,homeEmail
      write_path: /employees/{person}
      write_method: POST
      fields: { phone: mobilePhone, home_address: address1, personal_email: homeEmail }

  - name: slack
    kind: notify
    implementation: webhook
    operations: [prepare_message, commit_message]
    settings: { format: slack, channel_label: "#people-help", credentials: CONTOSO_SLACK }
```

**Secrets live on the server, never in the pack.** Packs are hashed into every
decision and kept in version control, so a pack containing a password, token or
key is refused when it loads. Instead each adapter names an environment prefix
with `credentials:`, and that is how two companies on one server use two
different Google or Microsoft accounts. The example above reads:

```bash
export CONTOSO_MS_TENANT_ID=... CONTOSO_MS_CLIENT_ID=... CONTOSO_MS_CLIENT_SECRET=...   # Entra ID app: Calendars.ReadWrite, Mail.Send
export CONTOSO_BAMBOO_USERNAME=<api key> CONTOSO_BAMBOO_PASSWORD=x
export CONTOSO_SLACK_URL=https://hooks.slack.com/services/...
```

Without `credentials:` the defaults are `HR_GOOGLE_*` (client id, secret and
refresh token), `HR_MS_*`, `HR_SMTP_*`, `HR_WEBHOOK_URL` and `HR_REST_*`.

**Checking a setup.** `hrctl integrations` lists every connector and the settings
and secrets each needs. `hrctl integrations <company>` shows what that company
is connected to and whether each is ready; add `--check` to prove the
credentials work without sending, booking or changing anything. `hrctl validate`
rejects a connector used for the wrong job, missing settings, an `http://`
address, or unencrypted mail to anything but a relay on the same machine. HR
sees the same status under **Insights → Connected systems**.

With credentials missing or a vendor down, a connector fails loudly rather than
pretending: nothing is booked, nobody is emailed, nothing is changed, and a
person is told which system did not answer.

**Adding another vendor** is one module: subclass `BaseAdapter`, implement
`read` / `preview` / `act` for the operations of its kind, declare `VENDOR`,
`PRODUCT`, `SECRETS` and `REQUIRED_SETTINGS`, export it in `IMPLEMENTATIONS`,
and list the module in `tools.EXTERNAL_MODULES`. `gworkspace.py`,
`microsoft365.py` and `standard.py` are the worked examples, and
`tests/test_integrations.py` shows how to test one against a fake transport.

---

## What this deliberately does not do

No candidate ranking or screening. No performance or termination
recommendations. No payroll calculation. No legal, medical or financial advice.
No tool anywhere that can write pay or grant access — not even behind an
approval, and there is a test for each one.
