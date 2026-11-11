# Deploying it

Two pieces: an **API** that must keep its state, and a **dashboard** that is
just static files.

That split is the whole reason this page exists. The dashboard goes anywhere.
The API does not, and the reason is worth understanding before you pick a host.

---

## Why the API is not serverless

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

So: **one small always-on container with a disk.** Render, Railway and Fly all
do this on a free or near-free tier. The Dockerfile in this repo is that
container.

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

Create sign-ins with `hrctl account`. **Note:** the dashboard has no login
screen yet — the API has `/api/auth/login`, but the React client never calls it.
So this path needs that screen built first. Do not put real employee data behind
Option 1 because it was quicker.

---

## Part 1 — the API on Render

The repo has a `render.yaml`, so this is mostly clicking.

1. Push this repo to GitHub.
2. Render → **New** → **Blueprint** → pick the repo.
3. It reads `render.yaml`: one Docker web service, a 1 GB disk mounted at
   `/data`, health check on `/api/health`.
4. Check the environment variables it proposes:
   - `HR_VAR_DIR=/data` — the database lives on the disk, not in the image.
   - `HR_PUBLIC_DEMO=anyone-can-act-as-anyone` — read the section above before
     leaving this in.
   - `HR_CORS_ORIGINS` — fill in once you have the Vercel URL (Part 3).
5. Deploy. First boot runs `hrctl init`, seeds all three companies, and writes
   `/data/.seeded` so a restart does **not** wipe your demo state.

Confirm it:

```bash
curl https://<your-service>.onrender.com/api/health
```

You want `"ok": true` and `"public": true`.

**Free tier caveat worth knowing before a live demo:** Render's free instances
sleep after inactivity and take ~30 seconds to wake. Hit the URL a minute before
you present, or pay for the always-on tier. Nothing is more awkward than a
loading spinner in front of a room.

To re-seed later: delete `/data/.seeded` from the Render shell and restart, or
run `hrctl demo acme_labs --reset` there.

### Railway or Fly instead

Same container, same two environment variables.

- **Railway** — New Project → Deploy from GitHub → it finds the Dockerfile. Add
  a volume at `/data` and set `HR_VAR_DIR=/data`.
- **Fly** — `fly launch` (it will detect the Dockerfile), then
  `fly volumes create hr_data --size 1` and mount it at `/data` in `fly.toml`.

---

## Part 2 — the dashboard on Vercel

`vercel.json` is already set up: it builds `client/` and serves
`client/dist` as a static site.

1. Vercel → **Add New** → **Project** → import the repo.
2. Leave the build settings alone; `vercel.json` has them.
3. Add one environment variable:

   | Name | Value |
   |---|---|
   | `VITE_API_BASE_URL` | `https://<your-service>.onrender.com` |

   This is read at **build** time, not run time. If you change it, redeploy —
   otherwise the old URL stays baked into the bundle.
4. Deploy.

If you skip step 3 the dashboard loads and then says *"Cannot reach the
agent"*, because it is calling `/api` on Vercel, where nothing is listening.
That message is the symptom of a missing `VITE_API_BASE_URL`, every time.

---

## Part 3 — let them talk to each other

The browser calls the API cross-origin, so the API has to allow the dashboard's
origin. Back in Render, set:

```
HR_CORS_ORIGINS=https://<your-project>.vercel.app,http://localhost:5173
```

Keep `localhost:5173` in the list so you can still run the dashboard locally
against the hosted API, which is a genuinely useful way to work.

Then redeploy the Render service and reload the dashboard. If the screens are
empty and the browser console shows a CORS error, this is the variable you got
wrong — check for a trailing slash, and that it is `https`.

Vercel gives every branch and every preview its own hostname, and those are
*not* in this list. Either add the ones you care about or accept that only
production works.

---

## Running it locally, which is still the best demo

Nothing above is needed for this, and it has no cold start:

```bash
python3 -m hr_agent.cli init
python3 -m hr_agent.cli demo acme_labs --reset
python3 -m hr_agent.cli demo meridian_group --reset
python3 -m hr_agent.cli demo harbour_clinic --reset
python3 -m hr_agent.cli serve
```

```bash
cd client && npm run dev
```

Then <http://localhost:5173>. See `HR_AGENT_GUIDE.txt` for what to say.

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
| `HR_PUBLIC_DEMO` | *(unset)* | Must be exactly `anyone-can-act-as-anyone` to allow a public bind with the role switcher on. |
| `HR_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated browser origins allowed to call the API. |
| `HR_AUTH` | `off` | `on` turns on sign-in and turns the role switcher off. |
| `HR_AUTH_SECRET` | *(ephemeral)* | Signing key for sessions. Unset means sessions die on restart. |
| `HR_REDACTION` | `on` | Only ever set this off in a test that proves the refusal happens. |
| `PORT` | `4000` | Injected by most platforms. |
| `VITE_API_BASE_URL` | *(empty)* | **Build-time**, for the dashboard. Where the API is. |

---

## What is still not production-ready

Say this out loud rather than letting someone discover it:

- Every connected system is a **mock**. HR system, calendar, email, document
  store. The adapter interface is real and a Google Calendar/Gmail connector
  exists, but the demo talks to nothing outside itself.
- **No login screen** in the dashboard, so `HR_AUTH=on` is not yet a usable
  path for a hosted deployment.
- SQLite. Fine for a demo and for a pilot of one company; the schema was
  written to be Postgres-compatible for when that stops being true.
- Audit **truncation** is not detectable — removing rows from the end of a chain
  leaves a valid chain. That needs an external anchor, which is not built.
