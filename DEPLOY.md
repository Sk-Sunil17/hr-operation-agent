# Deploying it

One server. The same process serves the **dashboard** (every path) and the
**JSON API** (`/api/*`) from one origin, so there is no API address to bake into
the dashboard and no CORS origin to keep in step with it.

---

## Why it is not serverless

The demo is stateful on purpose. The sequence that makes the product's argument
is:

1. the agent **prepares** a change and commits nothing,
2. a person **approves** it,
3. the change **exists**, and the audit chain proves it happened in that order.

A platform that discards the filesystem between requests breaks step 3. On
Vercel, Lambda and friends, SQLite writes go to an ephemeral `/tmp` that the
next request may not see, so approvals vanish, booked interviews vanish, and the
hash-chained audit trail — the thing that is meant to be tamper-evident — starts
over. You would be demonstrating the opposite of the claim.

So: **one small always-on container.** Render, Railway and Fly all do this. The
Dockerfile in this repo builds the dashboard and ships it inside that container.

---

## Before anything: the thing you must decide

The dashboard has a **role switcher** — you act as an employee, then as the HR
partner who approves their request. That is how the approval guards get
demonstrated, and it is the most persuasive 30 seconds of the demo.

It also means **anyone who can reach the URL can act as anyone in the
directory**, read drafts, and approve record changes. So `serve()` refuses to
bind to anything but localhost while the role switcher is on:

```
refusing to bind 0.0.0.0: anyone could act as anyone while role switching is on.
```

A container has nothing but `0.0.0.0` to bind, so you have to choose one:

### Option 1 — a public demo of the fictional companies (what the demo is for)

Acknowledge it explicitly:

```
HR_PUBLIC_DEMO=anyone-can-act-as-anyone
```

A truthy value is deliberately **not** enough — not `1`, not `true`. It has to
be that exact sentence, so that no platform default and no copied template can
switch this off by accident. With it set, the server starts, logs a warning, and
the dashboard shows a banner telling visitors it is a public demo of invented
data.

Only do this for the three fictional companies in `packs/`. There is no real
person in them.

### Option 2 — anything with real people in it

Turn authentication on instead, which switches the role switcher off and
satisfies the guard legitimately:

```
HR_AUTH=on
HR_AUTH_SECRET=<a long random string>
```

With it on, the dashboard opens on a sign-in screen, there is no role switcher,
and every company-scoped `/api` route needs a session for that company. Five
wrong passwords lock an account for 15 minutes. Create sign-ins with:

```bash
hrctl account acme_labs emp_021 --email hana@acme.test
```

Sessions last a working day and end when the browser tab closes. Do not put real
employee data behind Option 1 because it was quicker.

---

## Render, one click

1. [dashboard.render.com](https://dashboard.render.com) → **New** → **Blueprint**
2. Connect this repo. Render reads `render.yaml` and shows one service,
   `hr-agent`.
3. **Apply**. Nothing to fill in.

First boot runs `hrctl init`, seeds all three companies, and writes
`/data/.seeded` so a restart with a disk does **not** wipe your demo state.
Open the service URL: you should see the public-demo banner and Acme Labs with
work waiting. Confirm the API with:

```bash
curl https://<your-service>.onrender.com/api/health
```

**The free plan has no disk.** Render cannot attach a persistent disk to a free
instance, so on free the database lives inside the container: the demo is fully
stateful while the instance is up, and re-seeds when it restarts. To keep state
across restarts, switch the service to a paid plan and add the disk block shown
in the comment in `render.yaml`.

Free instances also sleep when idle and take about 30 seconds to wake. Open the
link a minute before you show anyone.

To re-seed later: delete `/data/.seeded` from the Render shell and restart, or
run `hrctl demo acme_labs --reset` there.

### Railway or Fly instead

Same container, same environment variables.

- **Railway** — New Project → Deploy from GitHub → it finds the Dockerfile. Add
  a volume at `/data` and set `HR_VAR_DIR=/data`.
- **Fly** — `fly launch` (it will detect the Dockerfile), then
  `fly volumes create hr_data --size 1` and mount it at `/data` in `fly.toml`.

---

## Running it locally, which is still the best demo

No cold start, nothing hosted:

```bash
python3 -m hr_agent.cli init
python3 -m hr_agent.cli demo acme_labs --reset
python3 -m hr_agent.cli demo meridian_group --reset
python3 -m hr_agent.cli demo harbour_clinic --reset
(cd client && npm install && npm run build)     # once, or after changing the dashboard
python3 -m hr_agent.cli serve
```

Then <http://127.0.0.1:4000>. See `HR_AGENT_GUIDE.txt` for what to say.

Changing the dashboard? `cd client && npm run dev` serves it with hot reload on
<http://localhost:5173> and forwards `/api` to the server on 4000.

And if the browser ever fails in front of people:

```bash
python3 -m hr_agent.cli walkthrough acme_labs
```

Twelve steps, the whole demo as text, no browser involved.

---

## Every environment variable

| Variable | Default | What it does |
|---|---|---|
| `HR_VAR_DIR` | `./var` | Where the SQLite database lives. Point it at a mounted disk. |
| `HR_DB_PATH` | `$HR_VAR_DIR/hr.db` | The database file itself, if you want it elsewhere. |
| `HR_PACKS_DIR` | `./packs` | Where company configuration is read from. |
| `HR_DASHBOARD_DIR` | `./client/dist` | The built dashboard the server hands out. |
| `HR_PUBLIC_DEMO` | *(unset)* | Must be exactly `anyone-can-act-as-anyone` to allow a public bind with the role switcher on. |
| `HR_AUTH` | `off` | `on` turns on sign-in and turns the role switcher off. |
| `HR_AUTH_SECRET` | *(ephemeral)* | Signing key for sessions (`HR_SESSION_SECRET` also works). Unset means sessions die on restart. |
| `HR_REDACTION` | `on` | Only ever set this off in a test that proves the refusal happens. |
| `HR_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Only for a dashboard served from another origin, such as `npm run dev`. |
| `PORT` | `4000` | Injected by most platforms. |

---

## What is still not production-ready

Say this out loud rather than letting someone discover it:

- Every connected system is a **mock**. HR system, calendar, email, document
  store. The adapter interface is real and a Google Calendar/Gmail connector
  exists, but the demo talks to nothing outside itself.
- Sign-in lockouts are kept in memory, so they reset on restart and are not
  shared between several server processes.
- SQLite. Fine for a demo and for a pilot of one company; the schema was
  written to be Postgres-compatible for when that stops being true.
- Audit **truncation** is not detectable — removing rows from the end of a chain
  leaves a valid chain. That needs an external anchor, which is not built.
