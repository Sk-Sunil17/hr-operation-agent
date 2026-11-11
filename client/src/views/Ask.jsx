// The main screen: ask something, watch the whole decision happen.

import { useState } from 'react';
import { api } from '../api.js';
import { Checks, Rail, useBusy, useLoad } from '../shared.jsx';
import { modeOf, readable, statusOf } from '../format.js';

const OUTCOME_CLASS = {
  answered: 'outcome-ok',
  queued: 'outcome-wait',
  handed_over: 'outcome-wait',
  refused: 'outcome-err',
  split: '',
  suppressed: '',
};

const HEADLINE = {
  answered: 'Answered, quoting the policy',
  queued: 'Prepared. A person must approve it',
  handed_over: 'Handed to a person. Nothing was answered',
  refused: 'Declined, with a reason',
  split: 'One message, several requests',
  suppressed: 'Already received. Nothing done again',
};

export default function AskView({ tenant, actor, people, onError, onChanged }) {
  const [message, setMessage] = useState('');
  // A document arriving with the message. Document intake is the largest piece
  // of real HR work the agent does, and without somewhere to put a form it was
  // the one feature the dashboard could not reach at all.
  const [attachment, setAttachment] = useState('');
  const [attaching, setAttaching] = useState(false);
  const [result, setResult] = useState(null);
  const [preview, setPreview] = useState(null);
  const [busy, run] = useBusy(onError);
  const [cases] = useLoad(() => api.goldenCases(tenant), [tenant], onError);

  const attached = attaching && attachment.trim() ? { text: attachment } : undefined;

  const ask = () => {
    if (!message.trim()) return;
    run('ask', async () => {
      setPreview(null);
      setResult(await api.chat(tenant, { message, requester: actor, attachment: attached }));
      onChanged();
    });
  };

  const explain = () => {
    if (!message.trim()) return;
    run('explain', async () => {
      setResult(null);
      setPreview(await api.explain(tenant, { message, requester: actor, attachment: attached }));
    });
  };

  const useExample = (example) => {
    setMessage(example.message);
    setAttachment(example.attachment?.text || '');
    setAttaching(Boolean(example.attachment?.text));
    setResult(null);
    setPreview(null);
  };

  const actorName = people.find((p) => p.id === actor)?.name || actor;

  return (
    <div className="layout">
      <aside className="side">
        <section className="card">
          <h2>Ask as {actorName}</h2>
          <label className="field-label" htmlFor="message">
            What would an employee or an HR person say?
          </label>
          <textarea
            id="message"
            rows={5}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder="How many days of annual leave do I get in a year?"
          />
          <label className="hint" style={{ display: 'block', marginTop: 10 }}>
            <input
              type="checkbox"
              checked={attaching}
              onChange={(e) => setAttaching(e.target.checked)}
            />{' '}
            <strong>Attach a document</strong> — a form, as it arrived
          </label>
          {attaching && (
            <>
              <textarea
                id="attachment"
                rows={8}
                value={attachment}
                onChange={(e) => setAttachment(e.target.value)}
                placeholder={'NEW STARTER FORM\nFull name: Lena Fischer\nStart date: 2026-11-24\nPersonal email: lena.fischer@example.test\nPhone: 07700 900512\nEmergency contact: Jonas Fischer, 07700 900513\nBank details: 12-34-56 / 45678901'}
              />
              <p className="hint hint-tight">
                Read against this company's own form templates. Delete a line to see it name
                exactly what is missing; paste something it has never seen and it refuses to guess
                what it is. A document that arrives decides the request is an intake, whatever the
                covering note says.
              </p>
            </>
          )}
          <div className="decision-actions">
            <button type="button" className="btn btn-primary" onClick={ask} disabled={Boolean(busy)}>
              {busy === 'ask' ? 'Running…' : 'Run it'}
            </button>
            <button type="button" className="btn btn-outline" onClick={explain} disabled={Boolean(busy)}>
              {busy === 'explain' ? 'Working…' : 'Explain only'}
            </button>
          </div>
          <p className="hint">
            <strong>Run it</strong> does it for real: it decides, drafts, verifies and either sends,
            queues or declines. <strong>Explain only</strong> shows the decision and changes nothing.
          </p>
        </section>

        <section className="card">
          <h2>This company's own test cases</h2>
          <p className="hint" style={{ marginTop: 0 }}>
            These come from the company's pack. Getting them all green is what onboarding means.
          </p>
          <ul className="rec-list">
            {(cases?.items || []).map((c) => (
              <li key={c.id}>
                <button type="button" className="rec-item" onClick={() => useExample(c)}>
                  <span className="rec-top">
                    <span className="rec-title">{c.message}</span>
                  </span>
                  <span className="rec-bottom">
                    <span className="rec-meta">
                      {c.id} · expects {c.expect_rule} · {modeOf(c.expect_mode).short}
                      {c.attachment ? ' · with a document' : ''}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      </aside>

      <main className="main">
        {result ? (
          <Result data={result} />
        ) : preview ? (
          <Preview data={preview} />
        ) : (
          <div className="blank">
            <h1>Ask it something</h1>
            <p>
              Pick one of the company's own test cases on the left, or write your own. Try a routine
              policy question, then try telling it about a grievance, and watch the difference.
            </p>
            <p>
              The same question asked at the other company in the switcher above reaches a different
              outcome, with no code change — only configuration.
            </p>
          </div>
        )}
      </main>
    </div>
  );
}

function Result({ data }) {
  const { item, decision, citations, plans, messages, trail, children } = data;
  const status = statusOf(item.status);
  const sent = messages.filter((m) => m.direction === 'outbound');

  return (
    <article className="detail">
      <header className="detail-head">
        <div className="detail-title">
          <h1>{messages.find((m) => m.direction === 'inbound')?.body || item.id}</h1>
          <p className="detail-sub">
            <span className={`status status-lg status-${status.tone}`}>{status.label}</span>
          </p>
          <p className="detail-meta">
            {item.requester_name} · read as {item.intent}, {item.sensitivity} · rule{' '}
            <strong>{item.rule_id}</strong> · configuration {decision?.pack_hash}
          </p>
        </div>
      </header>

      <Rail trail={trail} fresh />

      <section className={`outcome ${OUTCOME_CLASS[item.status] || ''}`}>
        <h2>{HEADLINE[item.status] || item.status}</h2>
        <p className="outcome-note">
          Rule <strong>{item.rule_id}</strong>: {decision?.rule_reason}
        </p>
        {children?.length > 0 && (
          <p className="outcome-note">
            Split into {children.length} separate work items, each with its own trail and outcome.
          </p>
        )}
        {decision?.draft && <pre className="letter">{decision.draft}</pre>}
        {sent.length > 0 && (
          <p className="outcome-note">
            {sent.length} message{sent.length === 1 ? '' : 's'} actually sent.
            {item.status === 'queued' && ' The reply itself is waiting: only the acknowledgement went out.'}
          </p>
        )}
      </section>

      {citations.length > 0 && (
        <section className="panel">
          <h2>What it quoted</h2>
          {citations.map((c) => (
            <blockquote key={c.id} className="quote">
              {c.quoted_text}
              <span className="quote-source">
                {c.doc_id} version {c.version}, section {c.section_id}
              </span>
            </blockquote>
          ))}
          <p className="hint">
            The verify gate checks these exact words appear in the reply. A citation the reply does
            not quote is a failure, not a formatting detail.
          </p>
        </section>
      )}

      <section className="panel">
        <div className="panel-head">
          <h2>The verify gate</h2>
          <span className={`tag ${decision?.verify_passed ? 'tag-ok' : 'tag-err'}`}>
            {decision?.verify_passed ? 'passed' : 'failed — escalated'}
          </span>
        </div>
        <Checks checks={decision?.checks || []} />
      </section>

      {plans.length > 0 && (
        <section className="panel">
          <h2>What it would do, or did</h2>
          <div className="table-scroll">
            <table className="grid">
              <thead>
                <tr>
                  <th>Action</th>
                  <th>System</th>
                  <th>State</th>
                  <th>Preview</th>
                </tr>
              </thead>
              <tbody>
                {plans.map((p) => (
                  <tr key={p.id}>
                    <td>{p.operation}</td>
                    <td>{p.adapter}</td>
                    <td>
                      <span
                        className={`tag ${
                          p.status === 'committed' ? 'tag-ok' : p.status === 'cancelled' ? 'tag-err' : 'tag-wait'
                        }`}
                      >
                        {p.status}
                      </span>
                    </td>
                    <td style={{ whiteSpace: 'pre-wrap' }}>{p.preview_text}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="hint">
            Prepared means written down and not done. Every action carries an idempotency key, so a
            retry collides with itself instead of acting twice.
          </p>
        </section>
      )}

      <section className="panel">
        <h2>The facts the rule fired on</h2>
        <p className="hint" style={{ marginTop: 0, marginBottom: 12 }}>
          These come from the directory and the policy corpus, not from the text of the message.
          That is why instructions written inside a message cannot change what the agent may do.
        </p>
        <div className="table-scroll">
          <table className="grid grid-tight">
            <tbody>
              {Object.entries(decision?.facts || {})
                .filter(([, v]) => v !== null)
                .map(([k, v]) => (
                  <tr key={k}>
                    <td style={{ width: '14rem' }}>{k}</td>
                    <td>
                      <code>{String(v)}</code>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </section>
    </article>
  );
}

function Preview({ data }) {
  const { classification, facts, verdict, probe, segments, context } = data;
  return (
    <article className="detail">
      <header className="detail-head">
        <div className="detail-title">
          <h1>Nothing has run</h1>
          <p className="detail-sub">This is the decision the agent would reach, and why.</p>
        </div>
      </header>

      <section className="outcome outcome-wait">
        <h2>
          Rule {verdict.rule_id} · <span className={`mode mode-${verdict.mode}`}>{modeOf(verdict.mode).short}</span>
        </h2>
        <p className="outcome-note">{verdict.reason}</p>
        <p className="outcome-note">
          Matched on: <code>{readable(verdict.matched_on)}</code>
        </p>
      </section>

      <div className="panels">
        <section className="panel">
          <h2>How it was read</h2>
          <dl className="fields">
            <div>
              <dt>Intent</dt>
              <dd>{classification.intent}</dd>
            </div>
            <div>
              <dt>Sensitivity</dt>
              <dd>{classification.sensitivity}</dd>
            </div>
            <div>
              <dt>Confidence</dt>
              <dd>{classification.confidence}</dd>
            </div>
            <div>
              <dt>About</dt>
              <dd>{context.subject_name || '—'}</dd>
            </div>
            <div>
              <dt>Populations</dt>
              <dd>{segments.length ? segments.join(', ') : 'none'}</dd>
            </div>
          </dl>
          {classification.notes?.length > 0 && (
            <ul className="reasons">
              {classification.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          )}
        </section>

        <section className="panel">
          <h2>Rules considered, in order</h2>
          <p>
            {verdict.considered.map((id) => (
              <span key={id} className={`tag ${id === verdict.rule_id ? 'tag-wait' : ''} tag-mono`}>
                {id}
              </span>
            ))}
          </p>
          <p className="hint">
            First match wins, so order is part of the meaning. The last rule matches everything,
            which is why the agent can never fall off the end of the list.
          </p>
        </section>

        {probe.query_terms?.length > 0 && (
          <section className="panel">
            <h2>Policy probe</h2>
            <p className="hint" style={{ marginTop: 0 }}>
              Read-only, and it runs before the decision because "is there a policy for this" is a
              fact the rules test. Terms: <code>{probe.query_terms.join(', ')}</code>
            </p>
            {probe.conflict ? (
              <ul className="reasons">
                {probe.conflicts.map((c) => (
                  <li key={c}>{c}</li>
                ))}
              </ul>
            ) : probe.citations?.length ? (
              <div className="sections">
                {probe.citations.map((c) => (
                  <div key={`${c.doc_id}${c.section_id}`} className="section-item">
                    <span className="section-score">{c.score.toFixed(2)}</span>
                    <h3>
                      {c.doc_title} v{c.version} · section {c.section_id}
                    </h3>
                    <p className="hint hint-tight">{c.quoted_text}</p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="empty-list">Nothing in force covers this.</p>
            )}
          </section>
        )}
      </div>
    </article>
  );
}
