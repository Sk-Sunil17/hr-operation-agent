// The approval queue. Where "prepared" becomes "done", and only here.

import { useState } from 'react';
import { api } from '../api.js';
import { RecordList, useBusy, useLoad } from '../shared.jsx';
import { modeOf, when } from '../format.js';

export default function QueueView({ tenant, actor, me, onError, onNotice, onChanged }) {
  const [selected, setSelected] = useState(null);
  const [note, setNote] = useState('');
  const [reasonCode, setReasonCode] = useState('');
  const [busy, run] = useBusy(onError);
  const [queue, loading, reload] = useLoad(() => api.queue(tenant, actor), [tenant, actor], onError);
  const [reasons] = useLoad(() => api.reasonCodes(), [], onError);

  const items = queue?.items || [];
  const open = items.find((i) => i.id === selected) || items.find((i) => i.mine) || null;

  const decide = (outcome) => {
    if (!open) return;
    if (outcome !== 'approved' && !note.trim()) {
      onError('Sending something back or taking it over needs a reason the requester can read.');
      return;
    }
    if (outcome === 'sent_back' && !reasonCode) {
      onError('Choose what was wrong with it. Free text cannot be counted; a code can.');
      return;
    }
    run(outcome, async () => {
      const result = await api.decide(tenant, open.id, {
        outcome,
        note: note.trim() || null,
        reason_code: outcome === 'sent_back' ? reasonCode : null,
        actor,
      });
      setNote('');
      setReasonCode('');
      setSelected(null);
      reload();
      onChanged();
      onNotice(
        outcome === 'approved'
          ? `Approved. ${result.committed.length} action(s) committed, each once, under its key.`
          : outcome === 'sent_back'
            ? `Sent back. ${result.cancelled.length} prepared action(s) cancelled; nothing was written.`
            : `Taken over. The agent stops contributing to this case.`,
      );
    });
  };

  const rows = items.map((i) => ({
    id: i.id,
    title: i.rule_reason?.slice(0, 70) || i.rule_id,
    right: i.rule_id,
    status: i.mine
      ? { label: `Yours — ${i.role_label}`, tone: 'wait' }
      : { label: `${i.role_label}`, tone: 'neutral' },
    meta: `${i.intent}/${i.sensitivity} · ${when(i.asked_at)}`,
  }));

  return (
    <div className="layout">
      <aside className="side">
        <section className="card">
          <div className="panel-head">
            <h2>Waiting</h2>
            <span className={`count ${queue?.mine ? 'count-wait' : ''}`}>{items.length}</span>
          </div>
          <p className="hint" style={{ marginTop: 0 }}>
            {loading
              ? 'Loading…'
              : `${queue?.mine || 0} of ${items.length} assigned to ${me?.name || actor}. Other roles' work is listed but their drafts are not shown.`}
          </p>
          <RecordList rows={rows} selectedId={open?.id} onSelect={setSelected} empty="Nothing is waiting." />
        </section>
        <section className="card">
          <h2>Why a person is here</h2>
          <p className="hint" style={{ marginTop: 0 }}>
            Nothing in this queue has touched a real system. Approving commits every prepared action
            exactly once. Sending it back cancels them. Taking it over stops the agent on that case
            for good.
          </p>
          <p className="hint">
            The citation is re-checked at the moment of approval: a draft written against a policy
            that has since changed is blocked, not sent.
          </p>
        </section>
      </aside>

      <main className="main">
        {open ? (
          open.mine ? (
            <article className="detail">
              <header className="detail-head">
                <div className="detail-title">
                  <h1>{open.rule_id} · needs you</h1>
                  <p className="detail-sub">{open.rule_reason}</p>
                  <p className="detail-meta">
                    asked by {open.requester_name} · {open.intent}/{open.sensitivity} ·{' '}
                    <span className={`mode mode-${open.mode || 'prepare'}`}>{modeOf('prepare').short}</span>
                  </p>
                </div>
              </header>

              <section className="outcome outcome-wait">
                <h2>The reply that is waiting</h2>
                {open.verify_passed === 0 && (
                  <p className="outcome-note">
                    This one failed verification, which is why a person is seeing it.
                  </p>
                )}
                <pre className="letter">{open.draft_text}</pre>

                {open.citations.length > 0 && (
                  <>
                    <h3>Cited</h3>
                    {open.citations.map((c) => (
                      <blockquote key={c.id} className="quote">
                        {c.quoted_text}
                        <span className="quote-source">
                          {c.doc_id} version {c.version}, section {c.section_id}
                        </span>
                      </blockquote>
                    ))}
                  </>
                )}

                {open.plans.length > 0 && (
                  <>
                    <h3>What approving would do</h3>
                    <ul className="reasons">
                      {open.plans.map((p) => (
                        <li key={p.id} style={{ whiteSpace: 'pre-wrap' }}>
                          {p.preview_text}
                        </li>
                      ))}
                    </ul>
                  </>
                )}

                <div className="decision-form">
                  <label className="field-label" htmlFor="reason">
                    What was wrong with it? (required to send back)
                  </label>
                  <select id="reason" value={reasonCode} onChange={(e) => setReasonCode(e.target.value)}>
                    <option value="">—</option>
                    {(reasons?.items || []).map((r) => (
                      <option key={r.code} value={r.code}>
                        {r.label}
                      </option>
                    ))}
                  </select>
                  <p className="hint hint-tight">
                    {reasonCode
                      ? `Counted against: ${(reasons?.items || []).find((r) => r.code === reasonCode)?.points_at}`
                      : 'A short list rather than free text, because nobody will read a thousand notes. A code tells you which part of the agent is wrong.'}
                  </p>

                  <label className="field-label" htmlFor="note" style={{ marginTop: 12 }}>
                    Note (required to send back or take over; recorded either way)
                  </label>
                  <textarea
                    id="note"
                    rows={2}
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    placeholder="What you checked, or why this is going back"
                  />
                  <div className="decision-actions">
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled={Boolean(busy)}
                      onClick={() => decide('approved')}
                    >
                      {busy === 'approved' ? 'Committing…' : 'Approve and send'}
                    </button>
                    <button
                      type="button"
                      className="btn btn-outline"
                      disabled={Boolean(busy)}
                      onClick={() => decide('sent_back')}
                    >
                      Send back
                    </button>
                    <button
                      type="button"
                      className="btn btn-danger"
                      disabled={Boolean(busy)}
                      onClick={() => decide('taken_over')}
                    >
                      I will handle it
                    </button>
                  </div>
                  <p className="hint">
                    You cannot approve your own request, and only the assigned role or its deputy can
                    decide. Every outcome is recorded against the decision, which is also how the
                    agent would learn from you later.
                  </p>
                </div>
              </section>
            </article>
          ) : (
            <div className="blank">
              <h1>Assigned to {open.role_label}</h1>
              <p>
                You are acting as {me?.name || actor}, who does not hold that role. The queue shows
                that this exists, because a queue that hides its own size is impossible to manage,
                but the draft and the prepared actions are not shown to you.
              </p>
              <p>Switch who you are acting as in the top right to review it.</p>
            </div>
          )
        ) : (
          <div className="blank">
            <h1>Nothing is waiting</h1>
            <p>
              Ask something that needs a person — a change to a record, or a question whose answer
              depends on someone's circumstances — and it will appear here.
            </p>
          </div>
        )}
      </main>
    </div>
  );
}
