"""Writing the reply.

Most traffic needs no generative model, because the agent quotes policy rather
than paraphrasing it. A routine answer is a template wrapped around the exact
words of the policy; a restricted acknowledgement is fixed text, which is what
you would want even with the best model available.

Every template is written to pass the verify gate by construction: it quotes
citations verbatim, it never tells a person what they personally get, and it
never gives advice. If a template ever stops satisfying the gate, the gate wins.
"""

from __future__ import annotations

from typing import Any

from . import interviews as interviews_mod
from .policy import Citation
from .packs.models import ResolvedConfig

# Tone is a pack setting. The substance never changes with it.
OPENERS = {
    "warm": "Thanks for asking.",
    "plain": "Here is what the policy says.",
    "formal": "Thank you for your enquiry.",
}
ACK_OPENERS = {
    "warm": "Thank you for telling us, and for trusting us with it.",
    "plain": "Thank you for raising this.",
    "formal": "Thank you for bringing this to our attention.",
}


def _role_label(cfg: ResolvedConfig, role: str) -> str:
    return cfg.approvals.roles.get(role, role)


def _quote_block(citations: list[Citation]) -> str:
    """The cited words, verbatim. The verify gate checks these appear unchanged."""
    return "\n\n".join(f'"{c.quoted_text}"\n— {c.label}' for c in citations)


def answer(cfg: ResolvedConfig, citations: list[Citation], *, personal: bool = False) -> str:
    """A policy answer. States what the policy says, never what the person gets."""
    tone = cfg.manifest.tone
    parts = [OPENERS.get(tone, OPENERS["plain"]), "", _quote_block(citations)]
    if personal:
        parts += [
            "",
            "What this means in your particular case depends on circumstances the "
            f"people team can see and I cannot, so a {_role_label(cfg, cfg.manifest.escalation.default_role)} "
            "is checking this before it is sent to you.",
        ]
    else:
        parts += [
            "",
            "That is the current version of the policy. If your own situation is not "
            f"covered by it, a {_role_label(cfg, cfg.manifest.escalation.default_role)} can pick it up from here.",
        ]
    return "\n".join(parts)


def acknowledge_restricted(cfg: ResolvedConfig, *, requester_name: str | None = None) -> str:
    """Fixed text. A restricted matter is never answered, summarised or assessed."""
    tone = cfg.manifest.tone
    role = _role_label(cfg, cfg.manifest.escalation.restricted_role)
    minutes = cfg.manifest.sla.restricted_acknowledge_minutes
    return "\n".join([
        ACK_OPENERS.get(tone, ACK_OPENERS["plain"]),
        "",
        f"This is being passed to a {role} now, and they will contact you directly "
        f"within {minutes} minutes. I have not recorded any view about it and I will "
        "not be answering it.",
        "",
        "Nothing you have written has been shared more widely than the person handling it.",
    ])


def acknowledge_queued(cfg: ResolvedConfig) -> str:
    """Sent immediately when something is queued, so nobody waits in silence."""
    role = _role_label(cfg, cfg.manifest.escalation.default_role)
    hours = cfg.manifest.sla.answer_hours
    return (
        f"Thanks — I have prepared this and sent it to a {role} to check. "
        f"You should hear back within {hours} hours. Nothing has changed yet."
    )


def handover(cfg: ResolvedConfig, *, reason: str, role: str) -> str:
    """A person is taking it, and the requester is told why in plain words."""
    return "\n".join([
        f"I am passing this to a {_role_label(cfg, role)} rather than answering it myself.",
        "",
        f"Why: {reason}",
        "",
        f"They will come back to you within {cfg.manifest.sla.answer_hours} hours.",
    ])


def refusal(cfg: ResolvedConfig, *, reason: str, alternative: str) -> str:
    """A refusal says what and why, and always offers the next step."""
    return "\n".join([
        "I am not able to do this one.",
        "",
        f"Why: {reason}",
        "",
        alternative,
    ])


def prepared_change(cfg: ResolvedConfig, previews: list[str], *, unstated: list[str] | None = None) -> str:
    """What a person approving a change is shown, and what the requester later gets.

    Where the new value was never stated, say so. Writing a placeholder that
    reads like data is how an approver ends up committing "(as described in the
    request)" into somebody's record.
    """
    role = _role_label(cfg, cfg.manifest.escalation.default_role)
    lines: list[str] = []
    if previews:
        lines += ["I have prepared this change but have not made it.", "", "\n\n".join(previews)]
    if unstated:
        readable = ", ".join(f.replace("_", " ") for f in unstated)
        lines += [
            "" if lines else None,
            f"I could not prepare the change to your {readable}: the new value was not "
            "stated in the message, and I will not guess at what belongs in a record.",
        ]
        lines = [line for line in lines if line is not None]
    lines += [
        "",
        f"A {role} needs to approve it before anything is written to any system."
        if previews
        else f"A {role} will come back to you.",
    ]
    return "\n".join(lines)


def readiness(cfg: ResolvedConfig, *, name: str, days: int, report, citations: list[Citation]) -> str:
    """An onboarding readiness report.

    Every line comes from asking a system. Items that are satisfied are listed
    too, so the reader can see what was actually checked rather than trusting
    that an empty list means everything is fine.
    """
    lines = [
        f"{name} starts in {days} day{'' if days == 1 else 's'}. I checked "
        f"{len(report.findings)} item{'' if len(report.findings) == 1 else 's'} from this company's "
        "new starter checklist against the systems that hold them.",
        "",
    ]
    if not report.outstanding:
        lines.append("Everything on the checklist is in place.")
    else:
        lines.append("Outstanding:")
        for finding in report.outstanding:
            mark = "  - " + finding.item.label
            if finding.item.blocking:
                mark += "  (a start date cannot be confirmed without this)"
            lines.append(f"{mark}\n      {finding.detail}")
    satisfied = [f for f in report.findings if f.present]
    if satisfied:
        lines += ["", "Already in place: " + ", ".join(f.item.label for f in satisfied) + "."]
    if report.unanswerable:
        lines += ["", "Could not be checked:"] + [f"  - {u}" for u in report.unanswerable]
    if citations:
        lines += ["", "The requirements this is checked against:", "", _quote_block(citations)]
    lines += ["", "I have not chased anyone and I have changed nothing. A person decides what happens next."]
    return "\n".join(lines)


ALTERNATIVES: dict[str, str] = {
    "P1": "A policy owner can publish or retire a version in the policy library, and the change is recorded there.",
    "W1": "If you need something about another person's record, ask the people team and say why you need it.",
    "A2": "Tell the people team which system holds that field and they can route the change properly.",
    "O2": "Ask again closer to the start date and the readiness check will have something useful to say.",
    "X1": "The people team opens an exit case first. Once it exists, the checklist can be prepared against it.",
}


def alternative_for(rule_id: str, cfg: ResolvedConfig) -> str:
    role = _role_label(cfg, cfg.manifest.escalation.default_role)
    return ALTERNATIVES.get(rule_id, f"A {role} can help with this directly.")


def needs_a_person(cfg: ResolvedConfig, *, what: str, reason: str) -> str:
    """For a `prepare` outcome with no specific template.

    Deliberately NOT worded as a handover. A handover says a person already has
    it; this says a person must decide before anything happens. An approver
    opening a queue item whose text claims somebody else took it is a small lie
    that costs trust in everything else on the screen.
    """
    role = _role_label(cfg, cfg.manifest.escalation.default_role)
    return "\n".join([
        f"I have not acted on {what}. It needs a {role} to decide.",
        "",
        f"Why: {reason}",
        "",
        "Nothing has been changed in any system.",
    ])


def system_failure(cfg: ResolvedConfig, *, role: str) -> str:
    """A connected system did not respond. Say so; do not imply a refusal."""
    return "\n".join([
        "I could not finish this one.",
        "",
        "A system I needed did not respond, so I have stopped rather than half-doing it. "
        f"A {_role_label(cfg, role)} has been told and will pick it up.",
        "",
        "Nothing has been changed, and nothing was sent on your behalf.",
    ])


def _booked_words(cfg: ResolvedConfig, interview: dict) -> str:
    """A booked interview's time, in the company's own date format."""
    from datetime import date as _date

    day = _date.fromisoformat(interview["on_date"])
    return (
        f"{interviews_mod.written_date(cfg, day)} at {interview['start_time']}–"
        f"{interview['end_time']} ({interview.get('timezone') or cfg.locale.timezone})"
    )


def _slot_words(cfg: ResolvedConfig, slot) -> str:
    """One slot, written out with the timezone it is in.

    The zone is never dropped. A candidate in another country reading "14:00"
    with no zone attached is a candidate who misses their interview, and the
    company's locale is the only thing that knows which zone was meant.
    """
    return (
        f"{interviews_mod.written_date(cfg, slot.on)} at {slot.start}–{slot.end} "
        f"({cfg.locale.timezone})"
    )


def interview_proposal(
    cfg: ResolvedConfig,
    *,
    request,
    proposal,
    panel_names: list[str],
    actions: list[str],
) -> str:
    """Times that work, and everything that would happen if a person approves.

    This is a proposal and says so repeatedly, because the thing being proposed
    goes into other people's calendars and into a stranger's inbox. Every
    constraint that ruled a day out is listed: an approver who can see *why*
    Tuesday was skipped can overrule it, and one who cannot has to redo the work
    the agent just did.
    """
    role = _role_label(cfg, cfg.manifest.escalation.default_role)
    best = proposal.best
    lines = [
        f"I checked {len(proposal.panel)} calendar{'' if len(proposal.panel) == 1 else 's'} across "
        f"{len(proposal.searched)} working day{'' if len(proposal.searched) == 1 else 's'}, "
        f"within {proposal.within}.",
        "",
        f"{request.stage_label}"
        + (f" for {request.role_title}" if request.role_title else "")
        + f", {request.minutes} minutes."
        + (f" Candidate: {request.candidate_name}." if request.candidate_name else ""),
        f"Interviewers: {', '.join(panel_names)}." if panel_names else "No interviewer was named.",
        "",
        f"I have prepared a hold for {_slot_words(cfg, best)}.",
        "It is not in anyone's calendar until a person approves it.",
    ]

    others = proposal.slots[1:]
    if others:
        lines += [
            "",
            "Also free, if that one does not suit:",
            *[f"  - {_slot_words(cfg, slot)}" for slot in others],
        ]

    if request.at and proposal.requested_time_available is False:
        lines += [
            "",
            f"The message asked for {request.at}. That is not free, so the time above is "
            "a different one — worth saying plainly rather than letting it look like what was asked for.",
        ]

    if proposal.rejected:
        lines += ["", "Days I ruled out, and why:"]
        lines += [f"  - {r.label}: {r.why}" for r in proposal.rejected]

    if request.gaps:
        lines += ["", "What I could not settle:"]
        lines += [f"  - {gap}" for gap in request.gaps]

    if request.notes:
        lines += ["", *[f"Note: {note}" for note in request.notes]]

    if actions:
        # A summary, not the full text. Each prepared action carries its own
        # preview, which the approval queue shows in full next to the button
        # that commits it -- repeating all of it here buries the proposal under
        # the two emails that depend on it.
        lines += ["", "What approving this would do:", *[f"  - {a}" for a in actions]]

    lines += [
        "",
        f"I have contacted nobody and nothing is booked. A {role} approves it, or picks "
        "a different time.",
    ]
    return "\n".join(lines)


def interview_no_time(cfg: ResolvedConfig, *, request, proposal, panel_names: list[str]) -> str:
    """No time works. Say exactly what was in the way, and prepare nothing.

    Deliberately not a refusal and not a handover: the request was reasonable
    and nobody has taken it over. There is simply no hour that satisfies this
    company's own constraints, and the useful thing to hand a person is the list
    of constraints rather than an apology.
    """
    role = _role_label(cfg, cfg.manifest.escalation.default_role)
    lines = [
        f"I could not find a time for this, so I have prepared nothing and booked nothing.",
        "",
        f"{request.stage_label}"
        + (f" for {request.role_title}" if request.role_title else "")
        + f", {request.minutes} minutes, within {proposal.within}.",
        f"Interviewers: {', '.join(panel_names)}." if panel_names else "No interviewer was named.",
        "",
    ]
    if proposal.searched:
        from datetime import date as _date

        first = interviews_mod.written_date(cfg, _date.fromisoformat(proposal.searched[0]))
        last = interviews_mod.written_date(cfg, _date.fromisoformat(proposal.searched[-1]))
        lines.append(
            f"I looked at {len(proposal.searched)} working day"
            f"{'' if len(proposal.searched) == 1 else 's'}, from {first} to {last}."
        )
    if proposal.rejected:
        lines += ["", "What was in the way:"]
        lines += [f"  - {r.label}: {r.why}" for r in proposal.rejected]
    else:
        lines += ["", "Nothing in the search window was available."]

    # Worth saying even when no time was found: a panel that is short of a
    # required role is a second thing to fix, and finding out about it only
    # after a time has been agreed wastes the agreement.
    if request.gaps:
        lines += ["", "Also unresolved:"] + [f"  - {gap}" for gap in request.gaps]

    settings = cfg.interviews
    lines += [
        "",
        "The constraints are this company's own, and any of them can be relaxed by a person:",
        f"  - at least {settings.min_notice_hours} hours' notice to the candidate",
        f"  - {settings.buffer_minutes} minutes clear either side of an interview",
        f"  - no more than {settings.max_per_day_per_interviewer} interviews a day per interviewer",
        f"  - inside {proposal.within}",
        "",
        f"A {role} can choose a time outside those, or ask somebody else to sit on the panel.",
    ]
    return "\n".join(lines)


def interview_candidate_mail(
    cfg: ResolvedConfig, *, request, slot, panel_names: list[str], location: str | None
) -> tuple[str, str]:
    """The message to the candidate. Returns (subject, body).

    Written to be the email a person would have written: what, when, where, who
    they will meet, and how to say it does not work. It promises the candidate
    nothing about the outcome, because the agent knows nothing about the outcome
    and a warm sentence that implies otherwise is a cruelty dressed as courtesy.
    """
    company = cfg.manifest.name
    what = request.stage_label.lower()
    subject = (
        f"{request.stage_label} with {company}"
        + (f" — {request.role_title}" if request.role_title else "")
    )
    greeting = f"Dear {request.candidate_name}," if request.candidate_name else "Hello,"
    body = [
        greeting,
        "",
        f"We would like to invite you to {'a' if what[0] not in 'aeiou' else 'an'} {what}"
        + (f" for the {request.role_title} role" if request.role_title else "")
        + f" at {company}.",
        "",
        f"  When:  {interviews_mod.written_date(cfg, slot.on)}, {slot.start}–{slot.end} "
        f"({cfg.locale.timezone})",
        f"  Where: {location or 'to be confirmed'}",
        f"  Who:   {', '.join(panel_names)}" if panel_names else "  Who:   to be confirmed",
        f"  Lasts: about {request.minutes} minutes",
        "",
        "A calendar invitation is attached so you can add it directly.",
        "",
        "If that time does not work for you, please reply to this message and we will "
        "find another — there is no disadvantage at all in asking.",
        "",
        f"With best wishes,",
        f"{company}",
    ]
    return subject, "\n".join(body)


def interview_panel_brief(
    cfg: ResolvedConfig, *, request, slot, panel_names: list[str], location: str | None
) -> tuple[str, str]:
    """The message to the interviewers. Returns (subject, body).

    A calendar invitation tells a panel when to turn up. It does not tell them
    who they are meeting, for what, or what is missing — which is the half that
    makes the hour worth having.
    """
    subject = (
        f"{request.stage_label}: {request.candidate_name or 'candidate to be confirmed'}"
        + (f" — {request.role_title}" if request.role_title else "")
    )
    body = [
        f"{request.stage_label}"
        + (f" for {request.role_title}" if request.role_title else "")
        + ".",
        "",
        f"  When:      {interviews_mod.written_date(cfg, slot.on)}, {slot.start}–{slot.end} "
        f"({cfg.locale.timezone})",
        f"  Where:     {location or 'to be confirmed'}",
        f"  Candidate: {request.candidate_name or 'not identified yet'}",
        f"  Panel:     {', '.join(panel_names)}" if panel_names else "  Panel:     nobody named",
    ]
    if request.gaps:
        body += ["", "Still open:", *[f"  - {gap}" for gap in request.gaps]]
    body += [
        "",
        "This was arranged from everyone's free/busy time, allowing "
        f"{cfg.interviews.buffer_minutes} minutes either side. If it clashes with something "
        "the calendar does not know about, say so and it will be moved.",
    ]
    return subject, "\n".join(body)


def interview_cancellation_mail(
    cfg: ResolvedConfig, *, interview: dict, reason: str | None
) -> tuple[str, str]:
    """Telling a candidate an interview is off. Returns (subject, body)."""
    company = cfg.manifest.name
    label = interview.get("stage_label") or "Interview"
    from datetime import date as _date

    when = interviews_mod.written_date(cfg, _date.fromisoformat(interview["on_date"]))
    subject = f"{label} on {when} — cancelled"
    body = [
        f"Dear {interview.get('candidate_name') or 'candidate'},",
        "",
        f"I am sorry to say that the {label.lower()} arranged for {when} at "
        f"{interview['start_time']} ({interview.get('timezone') or cfg.locale.timezone}) "
        "will no longer go ahead.",
        "",
        f"Reason given: {reason}" if reason else "No reason was recorded.",
        "",
        "This is not a decision about your application. Someone will be in touch about "
        "arranging another time.",
        "",
        "A cancellation has been sent to your calendar so the entry can be removed.",
        "",
        "With apologies,",
        company,
    ]
    return subject, "\n".join(body)


def interview_cancel_proposal(
    cfg: ResolvedConfig, *, interview: dict, reason: str | None, actions: list[str]
) -> str:
    """What cancelling would do. Nothing is withdrawn until a person approves."""
    role = _role_label(cfg, cfg.manifest.escalation.default_role)
    panel = [a for a in interview.get("attendees", []) if a["part"] in {"panel", "organiser"}]
    lines = [
        "I have prepared a cancellation but have not cancelled anything.",
        "",
        f"{interview.get('stage_label') or 'Interview'}"
        + (f" for {interview['role_title']}" if interview.get("role_title") else "")
        + f", {_booked_words(cfg, interview)}.",
        # The address itself is not repeated here. It identifies nobody that the
        # name, date and id do not already identify, and an address read out of a
        # record into a reply is exactly what the verify gate exists to stop. The
        # prepared email carries it on its own To: line, which the approval queue
        # shows in full next to the button that sends it.
        f"Candidate: {interview.get('candidate_name') or 'not recorded'}"
        + (" (we hold an address for them)" if interview.get("candidate_email")
           else " — no address on file, so they cannot be told from here"),
        f"Interviewers: {', '.join(a['name'] or a['person_id'] for a in panel) or 'none recorded'}.",
        "",
        f"Reason I would give: {reason}" if reason else
        "No reason was given in the message, and I will not invent one — "
        "the cancellation would say that no reason was recorded.",
    ]
    if actions:
        lines += ["", "What approving this would do:", *[f"  - {a}" for a in actions]]
    lines += [
        "",
        "Until then the interview is still in everyone's calendar and the candidate still "
        f"expects it. A {role} decides.",
    ]
    return "\n".join(lines)


def interview_ambiguous(cfg: ResolvedConfig, *, action: str, matches: list[dict]) -> str:
    """Several interviews could be the one meant, so none is touched.

    Breaking the tie by recency would be the obvious shortcut and the wrong one:
    it moves the wrong interview and tells the wrong candidate, and neither
    mistake announces itself.
    """
    role = _role_label(cfg, cfg.manifest.escalation.default_role)
    verb = {"cancel": "cancelled", "reschedule": "moved"}.get(action, "changed")
    lines = [
        f"More than one interview could be the one to be {verb}, so I have not touched any of them.",
        "",
        "Which did you mean?",
    ]
    for match in matches[:8]:
        lines.append(
            f"  - {match['id']}: {match.get('stage_label') or 'Interview'}"
            + (f" for {match['role_title']}" if match.get("role_title") else "")
            + f", {_booked_words(cfg, match)}"
            + (f", candidate {match['candidate_name']}" if match.get("candidate_name") else "")
        )
    if len(matches) > 8:
        lines.append(f"  … and {len(matches) - 8} more.")
    lines += [
        "",
        f"A {role} can say which one, and nothing has changed in the meantime.",
    ]
    return "\n".join(lines)


def interview_none_found(cfg: ResolvedConfig, *, action: str) -> str:
    """Nothing is booked that this could refer to."""
    role = _role_label(cfg, cfg.manifest.escalation.default_role)
    verb = {"cancel": "cancel", "reschedule": "move"}.get(action, "change")
    return "\n".join([
        f"There is no interview booked that I can see, so there is nothing to {verb}.",
        "",
        "Either it was arranged outside this system, or it has already been cancelled. "
        "I have changed nothing and told nobody.",
        "",
        f"A {role} can check the calendar directly and confirm.",
    ])


def incomplete_form(cfg: ResolvedConfig, *, reading, who: str | None = None) -> str:
    """Say exactly what is missing. Chase nobody."""
    role = _role_label(cfg, cfg.manifest.escalation.default_role)
    name = who or "the sender"
    lines = [
        f"{reading.template.label} arrived from {name}, and it is not complete. "
        "I have not filed it.",
        "",
    ]
    if reading.missing:
        lines += ["Not filled in:"] + [f"  - {f.label}" for f in reading.missing]
    if reading.illegible:
        lines += ["", "Could not be read (this is different from being left blank):"]
        lines += [f"  - {f.label}" for f in reading.illegible]
    if reading.old_template:
        lines += [
            "",
            f"It is version {reading.stated_version}, and the current one is "
            f"{reading.template.version}. The fields may have changed since.",
        ]
    if reading.unexpected:
        lines += ["", "Labels on the form that this company does not expect: " + ", ".join(reading.unexpected) + "."]
    filled = [f.label for f in reading.fields if f.satisfied]
    if filled:
        lines += ["", "Already filled in: " + ", ".join(filled) + "."]
    lines += ["", f"I have chased nobody. A {role} decides what to ask for and from whom."]
    return "\n".join(lines)


def complete_form(
    cfg: ResolvedConfig, *, reading, preview: str, document_type: str, sensitive: set[str] | None = None
) -> str:
    """A document ready to file. Filing is still a person's decision.

    The read-back shows what was understood so a person can check it, but a
    field the redaction pack treats as sensitive is reported as present rather
    than quoted. An approver does not need somebody's bank details copied into
    a reply to confirm the form was filled in -- they can open the document.
    """
    role = _role_label(cfg, cfg.manifest.escalation.default_role)
    sensitive = sensitive or set()
    label = reading.template.label if reading and reading.template else document_type
    lines = [f"{label} arrived and is complete. I have prepared it for filing but not filed it.", "", preview]
    if reading and reading.fields:
        read_back = []
        for entry in reading.fields:
            if not entry.satisfied:
                continue
            if entry.id in sensitive:
                read_back.append(f"  {entry.label}: filled in (not repeated here)")
            else:
                read_back.append(f"  {entry.label}: {entry.value}")
        if read_back:
            lines += ["", "What I read from it, for you to check:", *read_back]
    lines += ["", f"A {role} confirms the reading before anything is stored."]
    return "\n".join(lines)


def exit_checklist(cfg: ResolvedConfig, *, name: str, done: list, outstanding: list, manual: list) -> str:
    """The company's own departure checklist, against an exit case a person opened.

    Items a system can answer are answered. Items it cannot are listed as
    needing a person, rather than guessed at or quietly omitted.
    """
    role = _role_label(cfg, cfg.manifest.escalation.default_role)
    lines = [
        f"There is an open exit case for {name}. Here is this company's departure "
        f"checklist, {len(done) + len(outstanding) + len(manual)} items.",
        "",
    ]
    if outstanding:
        lines += ["Outstanding, and a system can see it:"]
        lines += [f"  - {i.label}  ({_role_label(cfg, i.owner_role)})" for i in outstanding]
    if done:
        lines += ["", "Already done: " + ", ".join(i.label for i in done) + "."]
    if manual:
        lines += ["", "No system can answer these, so a person confirms each one:"]
        lines += [
            f"  - {i.label}  ({_role_label(cfg, i.owner_role)})"
            + ("" if i.before_last_day else "  [after the last day]")
            for i in manual
        ]
    lines += [
        "",
        "I have contacted nobody, closed no account and changed no record. Nothing in "
        f"this system can revoke access. A {role} owns the case and decides what happens and when.",
    ]
    return "\n".join(lines)


def clarifying_question(cfg: ResolvedConfig, *, message: str, topics: list[str]) -> str:
    """A proposed question, for a person to approve in one click.

    The agent does NOT ask this itself, even though it could. Asking is an
    outbound message, and letting a low-confidence path send one on its own
    would widen what the agent may do without a person -- which is exactly the
    kind of quiet loosening the rules exist to prevent. Drafting the question
    gets most of the value and costs none of the guarantee.
    """
    role = _role_label(cfg, cfg.manifest.escalation.default_role)
    about = (
        f"It mentions {', '.join(topics)}, but not enough to act on."
        if topics
        else "There is not enough in it to tell what is being asked."
    )
    question = (
        f"Thanks for getting in touch. So that I point you at the right thing — "
        f"could you say a little more about what you need regarding "
        f"{topics[0] if topics else 'this'}?"
    )
    return "\n".join([
        "I could not tell what this is asking, so I have not acted on it.",
        "",
        f"What arrived: \"{message.strip()[:160]}\"",
        about,
        "",
        "A question I could send, if you approve it:",
        "",
        f"    {question}",
        "",
        f"A {role} decides whether to send that, ask something else, or just answer it.",
    ])


def job_report(cfg: ResolvedConfig, *, job: str, summary: str, detail: str) -> str:
    """What a scheduled job found. A job reports; it never closes anything."""
    role = _role_label(cfg, cfg.manifest.escalation.default_role)
    return "\n".join([
        f"Scheduled check: {job.replace('_', ' ')}.",
        "",
        summary,
        "",
        detail,
        "",
        f"Nothing has been sent, chased or changed. A {role} decides what to do about any of it.",
    ])
