# Demo script

**Read this once before you present. Keep it open on your phone while you do.**

The demo is 8 minutes. There are five moments. If you only remember one thing:
**the point of this product is that it refuses to answer when it should.**
Everything else is supporting detail.

---

## Part 1 · Five minutes before you start

Run these commands. Do not skip any of them — they reset the data so the demo
looks clean and the numbers make sense.

**Terminal 1:**
```bash
cd ~/Desktop/hr_operation_agent
python3 -m hr_agent.cli init
python3 -m hr_agent.cli demo acme_labs --reset
python3 -m hr_agent.cli demo meridian_group --reset
python3 -m hr_agent.cli demo harbour_clinic --reset
python3 -m hr_agent.cli serve
```

`init` creates the database and loads all three packs. The three `demo` lines
seed a morning of traffic for each company, so no screen you click on is empty —
including Harbour Clinic, which somebody always switches to.

Leave that running. It will print "API on http://127.0.0.1:4000".

**Terminal 2:**
```bash
cd ~/Desktop/hr_operation_agent/client
npm run dev
```
If it complains about Node, run `nvm use 22` first, then `npm run dev` again.

**Browser:** open <http://localhost:5173>

**One more setup step — do not skip it.** In the top right, change
**"Acting as"** to **Maya Iyer**. You are going to start the demo as an ordinary
employee, and become HR later.

**Check before you speak:**
- Top left says **Acme Labs**
- "Acting as" says **Maya Iyer**
- The **Approvals** tab shows eight items waiting, and **no gold number**
- The **Interviews** tab shows one booked interview
- You are on the **Ask** tab

The missing gold number is correct, not a fault. The badge counts items *you*
may decide, and Maya is an ordinary employee who may decide nothing. Switch
"Acting as" to **Hana Suzuki** and a gold **5** appears; switch to **Joel
Asante** and it is **8**, because Head of People deputises for HR Partner. If
you want a one-sentence version of the whole product, that is it — and it is
worth knowing before somebody asks why the number moved.

If the screen says "The agent is not answering", Terminal 1 has stopped. Restart it.

---

## Part 2 · The five things you need to understand

You do not need to know how the code works. You need these five ideas. Each one
is a sentence you can say out loud.

**1. It quotes, it doesn't paraphrase.**
When it answers, it pastes the exact words of the company policy and names the
version and section. It never writes its own version of what the policy means.

**2. A rule decides what it's allowed to do — not the AI.**
There's a list of rules, written in plain English in a config file. It reads
them top to bottom and stops at the first one that matches. The AI never gets a
say in how much freedom it has.

**3. It has four possible outcomes, not one.**
Answer it · Prepare it and wait for a person · Hand it to a person · Decline it.
Most HR tools only have "answer it".

**4. Nothing reaches a real system without a person clicking approve.**
It writes down what it *would* do, shows you a preview, and stops.

**5. Every company configures it, and can only make it stricter.**
Same software, different rules per company. A company can tighten the rules but
the software physically refuses to start if a company tries to loosen them.

**That's it.** If someone asks you something outside these five, it is fine to
say *"I'd have to check that one"*. Nobody expects you to know everything.

**And what it actually does**, if somebody asks for the list: employee
enquiries, policy lookup, HR administration (changes to records), document and
form intake, onboarding readiness, offboarding checklists, and interview
scheduling. Every one of those is in the demo below except offboarding and the
nightly checks, which are on the **Work items** and **Configuration** screens if
anybody asks.

---

## Part 3 · The demo, moment by moment

The words in quote marks are things to say. They're written to be spoken, not
read out — say them in your own words.

---

### MOMENT 1 — It answers, and shows its working *(90 seconds)*

**Say first:**

> "This is an HR assistant. I'll show you something simple first, then I'll show
> you the thing that actually matters."

**Do:** On the **Ask** tab, click the first test case in the left panel —
*"How many days of annual leave do I get in a year?"* — then click **Run it**.

**Point at the dark bar across the top** (the row of seven numbered circles).

> "That's everything it did, in order. It received the question, worked out what
> it was about, looked for a policy, a rule decided what it was allowed to do, it
> wrote a reply, it checked the reply, and it sent it."

**Point at the green box with the reply.**

> "Notice what the answer is. It's not the assistant's opinion. It's the actual
> words of the leave policy, with the version number and the section. If somebody
> argues about this answer in six months, we can show exactly where it came from."

**Scroll down to "The verify gate".**

> "And before it sent anything, twelve checks ran. One of them checks that the
> policy it claims to quote is genuinely in the reply, word for word. Another
> checks it hasn't told the person what *they personally* get — because it can't
> verify that, so it isn't allowed to say it."

---

### MOMENT 2 — It refuses, and that's the product *(2 minutes — this is the important one)*

**Say first:**

> "Now the one that actually matters. This is what the rest of the market gets
> wrong."

**Do:** Clear the box. Type (or paste) this:

```
My manager keeps making comments about my accent in front of the team and I dread meetings now.
```

Click **Run it**.

**Pause for a second before you say anything. Let them read it.**

> "It did not answer. It didn't look up the grievance policy. It didn't summarise
> what he said, it didn't form a view about whether it sounds serious, and it
> didn't ask him a follow-up question — because making a distressed person
> explain themselves to a machine is its own small cruelty."

**Point at the reply.**

> "What it did, in under a second, was acknowledge him warmly, tell him a named
> human has it, and say explicitly: *I have not recorded any view about this and
> I will not be answering it.* That text is fixed. It's not generated. We
> wouldn't want it generated even with the best AI available."

**Point at the third circle in the bar — "Find policy" — which is dashed, not filled.**

> "See that step is greyed out? It never even looked for a policy. The rule fires
> before retrieval happens."

**The line to land:**

> "Every HR AI tool is built to answer. None of them are built to refuse. In HR,
> a confident wrong answer isn't a bad user experience — it's a legal event."

---

### MOMENT 3 — Nothing happens without a human *(2 minutes)*

**Say first:**

> "Third thing. What happens when it needs to actually change something."

**Do:** Clear the box. Type:

```
I have moved. Please update my home address to 12 Oakfield Road, Bristol BS6 7JQ.
```

Click **Run it**.

**Say the new address out loud as you type it.** It matters: the agent will not
invent a value. If you only say "I have moved", it identifies the field and the
system correctly and then tells you it cannot prepare a change because you never
said what to change it to. That is deliberate — guessing what belongs in
somebody's record is worse than asking — but it means this moment needs the
address in the message.

**Point at the yellow box.**

> "It knows which system holds the address. It has permission to call it. It
> didn't. It wrote down exactly what it *would* do and stopped. The record right
> now is unchanged."

**Do:** In the top right, change **"Acting as"** from Maya Iyer to
**Hana Suzuki — HR Partner**. Then click the **Approvals** tab.

**Say while you switch:**

> "I've just stopped being the employee and become the HR partner. Maya can't
> approve her own request — the system won't let her. So somebody else has to."

> "This is the HR person's queue. They see the draft, they see the preview of the
> change, and they see exactly what clicking approve will do. Nothing is hidden."

**Do:** In the left-hand list, click the item that says
**"A change to a real record is prepared…"** (rule A1). Then scroll down and
click **Approve and send**.

**Point at the green banner that appears.**

> "Now it's committed — once, under a unique key, so a double-click or a retry
> can't do it twice."

**If you want one extra beat, say:**

> "There are three guards here. You can't approve your own request — I had to
> switch person to do this. You can't approve something assigned to a role you
> don't hold. And the policy the draft quoted gets re-checked at the moment of
> approval, so if the policy changed overnight, approving is blocked rather than
> sending stale wording."

**Optional, if you want to show a guard failing rather than describe it:**
switch "Acting as" back to **Maya Iyer**, open the same queue item and try to
approve. You'll get *"nobody decides their own request"* in red. Switch back to
Hana to continue. Only do this if you're comfortable — it costs 20 seconds.

---

### MOMENT 3b — It does the work, not just the talking *(2 minutes)*

This is the answer to "isn't this just a chatbot". Do it right after Moment 3,
while you are still the HR partner.

**Say first:**

> "Everything so far has been about answering. This is the part that is actually
> somebody's Tuesday afternoon."

**Do:** Go back to the **Ask** tab. You are still Hana Suzuki. Type:

```
Please arrange a technical interview with Astrid Lindqvist (astrid.l@example.test) for the Head of Finance role, with Priya and Sam.
```

Click **Run it**.

**Point at the reply, and walk down it slowly. There are four things to notice.**

> "One. It read three calendars across four working days. It did not ask anyone
> when they were free — it looked."

> "Two. It proposed a time, and two alternatives. And it tells me the day it
> ruled out and why: that one was inside the twenty-four hours' notice this
> company has decided a candidate gets. That number is in Acme's config file.
> Harbour Clinic's is seventy-two."

> "Three — and this is the bit I'd point at if you only look at one thing. It
> lists what approving would do. Put it in the calendar. Email the candidate,
> with a real calendar invitation attached. Brief the panel. Three separate
> things, one decision."

> "Four. Nothing has happened. The candidate has not been emailed. Nobody's
> calendar has changed."

**Do:** Switch **"Acting as"** to **Joel Asante — Head of People**. Go to
**Approvals** and approve the interview.

**Say while you switch:**

> "Same guard as before, and notice it applies to HR too. Hana asked for this,
> so Hana cannot approve it. Joel can, because the pack says Head of People
> deputises for HR Partner."

**Do:** Click the **Interviews** tab.

**Say:**

> "Now it exists. That one click booked the calendar entry and sent the
> candidate their invitation — together. We deliberately did not split that
> approval, because 'booked, but nobody told the candidate' is the worst
> possible outcome and we did not want it to be reachable."

**Point at the left-hand panel.**

> "And every number the scheduler obeyed is on screen: notice, buffer, how many
> interviews one person can be asked to do in a day. None of that is in our
> code. It is in this company's configuration file, and the three companies
> shipped here disagree on every one of them."

**If somebody asks "is that a real calendar?"** — say this, it is the honest
answer and it lands better than hedging:

> "In this demo it is a mock, so nothing leaves the room. There is a real Google
> Calendar and Gmail connector in the box, behind the same interface — swapping
> to it is a change to one config file and three environment variables. The
> kernel, the rules and the approval queue do not change at all."

---

### MOMENT 3c — The forms, which is where HR's hours actually go *(2 minutes)*

If you are short of time, this is the one to cut — but it is the moment an HR
audience recognises their own job in.

**Say first:**

> "Last one on the doing side. Everything so far was somebody typing a sentence.
> Most of HR's week is paperwork arriving."

**Do:** On the **Ask** tab, in this company's test cases on the left, click
**"Here is my completed new starter form."** — the one tagged *with a document*.
It fills in both the message and the form itself. Then click **Run it**.

**Point at the read-back in the reply.**

> "It read the form against this company's own template — the fields, and the
> names people actually write for them. It reads every value back so a person
> can check the reading, not just trust it."

**Point at the bank details line.** This is the beat.

> "Look at that one. *Bank details: filled in, not repeated here.* It confirms
> the field was completed without copying somebody's account number into a
> message. An approver doesn't need to see it to know the form was filled in —
> they can open the document."

**Do:** Now delete one line from the form — the **Emergency contact** line — and
click **Run it** again.

> "Same form, one field missing. It names exactly what is missing, files nothing,
> and chases nobody. A person decides whether to ask for it and from whom."

**If you have another thirty seconds**, click
**"Here is my medical certificate from the doctor."**

> "And this one it refuses to read at all. A medical certificate is restricted by
> *type*, not by what turns out to be in it — because deciding that by reading it
> means reading it. Nothing extracted, nothing filed, nothing summarised."

---

### MOMENT 4 — The same software, a different company *(2 minutes)*

This is your commercial argument. Slow down here.

**Say first:**

> "Last thing, and this is the one that matters if you're selling this to more
> than one company."

**Do:** In the top right, change **Company** from Acme Labs to **Meridian Group**.

> "Different company. Two and a half thousand people, three legal entities, a
> works council, much more cautious."

*Say the next bit before anyone counts: the directory shipped here is a
14-person sample. "The directory in this demo is a sample — in a real
deployment the people come from the customer's HR system through an adapter,
not from a file in our repo." The three legal entities and the works-council
population are real in the config, and the Rules tab shows them.*

**Do:** Go to the **Ask** tab, and run the *same* leave question:

```
How many days of annual leave do I get in a year?
```

**Point at the result.**

> "Same question. Same rule fired — E1. But at Acme it answered on its own, and
> here it's waiting for a person. Not one line of code is different between these
> two companies. Meridian's rules file overrides four rules, and the first one
> says it plainly: no answer reaches an employee without a person approving it
> first, however routine."

*(If somebody asks to see the file, it is
`packs/tenants/meridian_group/rules.yaml` — twenty lines, comments included.
Showing it helps rather than hurts.)*

**Do:** Click the **Rules** tab.

**Point at the gold highlighted row (E1) and the "set by: tenant" column.**

> "Every rule is listed, in plain English, with who set it. Gold means this
> company changed it. And here's the guarantee that makes it safe —"

**Point at the left panel text.**

> "— a company can only make it *stricter*. If a customer edits their config to
> let the assistant answer something it shouldn't, the software refuses to start.
> Not a warning. It won't boot. So no customer setting, and no instruction
> smuggled into a document, can unlock something that needs a human."

**The line to land:**

> "Onboarding a new company is filling in a config file and getting its tests to
> pass. It isn't a six-month consulting project, which is what it is with every
> tool in this market today."

---

### MOMENT 5 — Close *(30 seconds)*

**Do:** Click the **Audit** tab.

> "Everything it does is written to a tamper-evident trail. Each row is
> cryptographically linked to the one before it, so if anyone edits history, it
> shows. Eighteen months from now, if a tribunal asks what we told an employee on
> a particular Tuesday, we can produce the message, the rule that fired, the exact
> policy version quoted, who approved it, and prove none of it was altered."

**Close with:**

> "So — three things. It quotes policy instead of making things up. It refuses
> when it should, which nothing else in this market does. And every company
> configures it themselves, with a guarantee they can only make it stricter, never
> looser."

**Then stop talking.** Let them ask.

---

## Part 4 · Questions you will get

Short answers. Say them plainly and don't oversell.

**"Which AI model is this using?"**
> "Right now, none. The replies are templates with the policy quoted into them,
> because the agent quotes rather than paraphrases. About 70–80% of HR traffic
> doesn't need a model at all. There's a slot for one, and a company can turn it
> on with one config line, but the guardrails don't depend on it."

*(This is a strength. Say it confidently. It means no API cost, no data leaving,
nothing to hallucinate.)*

**"What if the AI gets it wrong?"**
> "It can't send an answer it can't quote to a policy that's in force. If the
> check fails, it escalates to a person — it never falls back to sending. A worse
> model means more things go to humans, not more wrong answers."

**"Can it handle our company's policies?"**
> "Yes — that's the whole design. Your policies, your rules, your approval chain,
> your systems. It's a configuration file, not a code change."

**"Is our data safe? Is it training on it?"**
> "No training on your data, and that's a hard setting, not a promise. Personal
> details — emails, phone numbers, bank details, dates of birth — are stripped
> before anything is stored or sent anywhere. The system refuses to start with
> that switched off."

**"What about GDPR / the EU AI Act?"**
> "We deliberately stay out of the high-risk category — no CV screening, no
> candidate scoring, no performance or termination decisions. And every decision
> has a full audit record with the reason, which is what those regulations
> actually ask for."

**"How much time does it save?"**
> Go to the **Measurement** tab and point at the yellow caveat box.
> "I'll be honest about this one. It only counts time saved on things it handled
> entirely alone. Anything a human touched is counted as a *cost*, not a saving.
> And for the second company it reports no saving at all, because they haven't
> given us a baseline. We'd rather under-claim."

*(This honesty lands very well with serious buyers. Don't apologise for it.)*

**"Does it connect to our calendar? Google? Outlook?"**

> "Google Calendar and Gmail are in the box and work through the same adapter
> interface everything else uses — swapping to them is one config file and three
> environment variables, with no change to the kernel, the rules or the approval
> queue. Outlook is the same shape of job: one adapter that answers 'when is
> this person busy' and 'create this event'. What you see in the demo is a mock,
> so nothing leaves this room."

**"Could it just book the interview itself and save us the click?"**

> "It could, and we deliberately didn't. Booking puts an hour in four people's
> calendars and sends a stranger an invitation — if that's wrong, somebody takes
> a morning off work for nothing. The company can loosen plenty of things in
> config, but the base rules set a ceiling it can't raise, and scheduling is one
> of the ones pinned to 'a person decides'."

**"Is this production-ready?"**
> "The core is built and tested — 416 automated tests. The systems it connects to
> are simulated right now; connecting a real HR system is an adapter, which is a
> known piece of work. What you're seeing is the logic and the guardrails, which
> is the hard part."

**"Who built this?"**
> Just answer honestly. Say what you did.

**Anything you don't know:**
> "Good question — let me check and come back to you."

Say that. It's a better answer than guessing, and nobody minds.

---

## Part 5 · If something goes wrong

**The screen says "The agent is not answering."**
Terminal 1 stopped. Click it, press the up arrow, press Enter. Reload the browser.

**The page is blank or won't load.**
Terminal 2 stopped. Same fix — up arrow, Enter, reload.

**It says "nobody decides their own request" when you try to approve.**
You are acting as the person who made the request. This is the system working
correctly, not a bug — say so out loud and move on. The fix depends on who asked:

- Maya asked (Moment 3) → switch to **Hana Suzuki — HR Partner**
- Hana asked (Moment 3b, the interview) → switch to **Joel Asante — Head of
  People**, who deputises for HR Partner

**It says "this is assigned to hr_partner, which this account does not hold".**
Switch to **Hana Suzuki**, or **Joel Asante** if Hana was the one who asked.

**You clicked Run it and nothing happened.**
The text box was empty, or you clicked before typing. Click in the box, check
your text is there, click Run it again.

**The numbers look wrong or the queue is empty.**
In a third terminal:
```bash
cd ~/Desktop/hr_operation_agent
python3 -m hr_agent.cli demo acme_labs --reset
```
Then reload the browser.

**Everything is broken and people are watching.**
Say: *"Let me show you this from the command line instead."* Then run:
```bash
python3 -m hr_agent.cli walkthrough acme_labs
```
That prints the entire demo as text — eleven steps, including the interview
being scheduled and booked — and needs no browser. It is honestly just as
convincing, and because it runs the real kernel against the real database, the
dashboard shows the same work afterwards.

---

## Part 6 · Do not say these things

Being caught overclaiming loses the room. These are the traps.

| Don't say | Say instead |
|---|---|
| "It's 100% accurate" | "It can't send an answer it can't cite. If it can't, a person gets it." |
| "It replaces HR people" | "It handles the routine so people spend time on what needs judgement." |
| "It understands employees" | "It classifies requests and quotes policy. It doesn't judge anything." |
| "It's fully automated" | "Anything that touches a real system waits for a person." |
| "It's AI-powered" *(as the headline)* | "It's rules and policy-quoting. AI is optional and off right now." |
| "It's ready to deploy tomorrow" | "The logic is built and tested. Connecting real systems is the next step." |
| "It books interviews for you" | "It proposes a time and prepares the invitation. A person approves before anything is booked or emailed." |
| "It screens candidates" | "It never ranks, screens or assesses anybody. It finds a time and writes the invitation." |

---

## The one-page version

Tape this to your monitor.

```
BEFORE:  init  ·  demo acme_labs/meridian_group/harbour_clinic --reset  ·  serve
         npm run dev  ·  open localhost:5173
         SET "Acting as" = Maya Iyer

1  ASK   "How many days of annual leave do I get in a year?"  → Run it
         → it quotes the policy, 12 checks pass

2  ASK   "My manager keeps making comments about my accent..."  → Run it
         → IT REFUSES TO ANSWER. This is the product. Pause here.

3  ASK   "I have moved. Please update my home address to
          12 Oakfield Road, Bristol BS6 7JQ."              → Run it
         → prepared, not done  (SAY THE ADDRESS — it won't invent one)
         → SWITCH "Acting as" to Hana Suzuki  (Maya can't approve her own)
         → Approvals tab  →  click the A1 item  →  Approve and send

3b ASK   "Please arrange a technical interview with Astrid Lindqvist
          (astrid.l@example.test) for the Head of Finance role, with Priya and Sam."
         → 3 calendars read, a time proposed, days ruled out WITH REASONS
         → "what approving would do": calendar + candidate email + panel brief
         → SWITCH "Acting as" to Joel Asante  (Hana can't approve her own either)
         → Approvals → approve → INTERVIEWS tab → it exists now

3c ASK   click test case "Here is my completed new starter form."
          (tagged WITH A DOCUMENT — fills the form in for you)   → Run it
         → reads every field back; BANK DETAILS = "filled in, not repeated here"
         → delete the Emergency contact line, Run it again → names what's missing

4  TOP RIGHT: switch company to Meridian  →  ask question 1 again
         → same rule, different outcome, zero code change
         →  Rules tab  →  gold row, "set by: tenant"

5  AUDIT tab  →  tamper-evident trail  →  close

CLOSE:  quotes instead of inventing · refuses when it should · does the
        back-office work · every company configures it, and can only tighten it

IF IT BREAKS:  python3 -m hr_agent.cli walkthrough acme_labs
```
