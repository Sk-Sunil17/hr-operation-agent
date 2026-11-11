// Every request the agent has handled, and the whole trail for each one.

import { useState } from 'react';
import { api } from '../api.js';
import { AuditTrail, Checks, Rail, RecordList, useLoad } from '../shared.jsx';
import { modeOf, statusOf, when } from '../format.js';

const FILTERS = [
  ['all', 'All', null],
  ['queued', 'Waiting', 'queued'],
  ['answered', 'Answered', 'answered'],
  ['handed_over', 'With a person', 'handed_over'],
  ['refused', 'Declined', 'refused'],
];

export default function ItemsView({ tenant, onError }) {
  const [filter, setFilter] = useState('all');
  const [selected, setSelected] = useState(null);
  const [list] = useLoad(() => api.workItems(tenant, {}), [tenant], onError);
  const [detail] = useLoad(
    () => (selected ? api.workItem(tenant, selected) : Promise.resolve(null)),
    [tenant, selected],
    onError,
  );

  const items = (list?.items || []).filter((i) => {
    const want = FILTERS.find(([id]) => id === filter)?.[2];
    return !want || i.status === want;
  });

  const counts = Object.fromEntries(
    FILTERS.map(([id, , status]) => [
      id,
      (list?.items || []).filter((i) => !status || i.status === status).length,
    ]),
  );

  const rows = items.map((i) => ({
    id: i.id,
    title: i.message || i.id,
    right: i.rule_id || '',
    status: statusOf(i.status),
    meta: `${i.requester_name} · ${when(i.created_at)}`,
  }));

  return (
    <div className="layout">
      <aside className="side">
        <section className="card">
          <h2>Work items</h2>
          <div className="tabs" role="tablist">
            {FILTERS.map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="tab"
                className="tab"
                aria-selected={filter === id}
                onClick={() => setFilter(id)}
              >
                {label} <span className="count">{counts[id] ?? 0}</span>
              </button>
            ))}
          </div>
          <RecordList rows={rows} selectedId={selected} onSelect={setSelected} empty="Nothing here yet." />
        </section>
      </aside>

      <main className="main">
        {detail ? (
          <Detail data={detail} />
        ) : (
          <div className="blank">
            <h1>Pick a work item</h1>
            <p>
              Each one shows the whole trail: how it was read, which rule fired and on what facts,
              what was retrieved, what was drafted, every verify check, and what was actually done.
            </p>
          </div>
        )}
      </main>
    </div>
  );
}

function Detail({ data }) {
  const { item, decision, citations, plans, messages, trail, approval, case: hrCase, children } = data;
  const status = statusOf(item.status);
  return (
    <article className="detail">
      <header className="detail-head">
        <div className="detail-title">
          <h1>{messages.find((m) => m.direction === 'inbound')?.body || item.id}</h1>
          <p className="detail-sub">
            <span className={`status status-lg status-${status.tone}`}>{status.label}</span>
            {decision && (
              <span className={`mode mode-${decision.mode}`} style={{ marginLeft: 10 }}>
                {modeOf(decision.mode).short}
              </span>
            )}
          </p>
          <p className="detail-meta">
            {item.id} · {item.requester_name} · {when(item.created_at)} · configuration{' '}
            {decision?.pack_hash} · drafted by {decision?.provider}
            {decision?.model_id ? ` (${decision.model_id})` : ' (no model)'}
          </p>
        </div>
      </header>

      <Rail trail={trail} />

      {decision && (
        <section className={`outcome ${decision.verify_passed ? 'outcome-ok' : 'outcome-err'}`}>
          <h2>
            {decision.rule_id} → {modeOf(decision.mode).label}
          </h2>
          <p className="outcome-note">{decision.rule_reason}</p>
          {decision.draft && <pre className="letter">{decision.draft}</pre>}
          {decision.human_verdict && (
            <p className="outcome-note">
              A person {decision.human_verdict.replace('_', ' ')} this
              {decision.human_actor ? ` (${decision.human_actor})` : ''}
              {decision.human_note ? `: "${decision.human_note}"` : ''}
            </p>
          )}
        </section>
      )}

      {children?.length > 0 && (
        <section className="panel">
          <h2>Split into</h2>
          <table className="grid">
            <tbody>
              {children.map((c) => (
                <tr key={c.id}>
                  <td>{c.id}</td>
                  <td>{c.rule_id}</td>
                  <td>
                    <span className={`mode mode-${c.mode}`}>{modeOf(c.mode).short}</span>
                  </td>
                  <td>{statusOf(c.status).label}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {citations.length > 0 && (
        <section className="panel">
          <h2>Quoted</h2>
          {citations.map((c) => (
            <blockquote key={c.id} className="quote">
              {c.quoted_text}
              <span className="quote-source">
                {c.doc_id} version {c.version}, section {c.section_id}
              </span>
            </blockquote>
          ))}
        </section>
      )}

      {decision && (
        <section className="panel">
          <div className="panel-head">
            <h2>Verify gate</h2>
            <span className={`tag ${decision.verify_passed ? 'tag-ok' : 'tag-err'}`}>
              {decision.verify_passed ? 'passed' : 'failed'}
            </span>
          </div>
          <Checks checks={decision.checks} />
        </section>
      )}

      {plans.length > 0 && (
        <section className="panel">
          <h2>Actions</h2>
          <div className="table-scroll">
            <table className="grid">
              <thead>
                <tr>
                  <th>Operation</th>
                  <th>System</th>
                  <th>State</th>
                  <th>Key</th>
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
                    <td>
                      <code>{p.idempotency_key.slice(0, 14)}…</code>
                    </td>
                    <td style={{ whiteSpace: 'pre-wrap' }}>{p.preview_text}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {(approval || hrCase) && (
        <section className="panel">
          <h2>People and cases</h2>
          <dl className="fields">
            {approval && (
              <>
                <div>
                  <dt>Assigned to</dt>
                  <dd>{approval.assigned_role}</dd>
                </div>
                <div>
                  <dt>Approval state</dt>
                  <dd>{approval.outcome || approval.status}</dd>
                </div>
                <div>
                  <dt>Decided by</dt>
                  <dd>{approval.decided_by || '—'}</dd>
                </div>
              </>
            )}
            {hrCase && (
              <>
                <div>
                  <dt>Case</dt>
                  <dd>{hrCase.kind}</dd>
                </div>
                <div>
                  <dt>Owner role</dt>
                  <dd>{hrCase.owner_role || hrCase.owner_id || '—'}</dd>
                </div>
                <div>
                  <dt>Agent stopped</dt>
                  <dd>{hrCase.taken_over ? 'yes, a person took it over' : 'no'}</dd>
                </div>
              </>
            )}
          </dl>
        </section>
      )}

      <section className="panel">
        <h2>Messages</h2>
        <table className="grid">
          <tbody>
            {messages.map((m) => (
              <tr key={m.id}>
                <td style={{ width: '6rem' }}>{m.direction}</td>
                <td style={{ width: '6rem' }}>{m.channel}</td>
                <td style={{ whiteSpace: 'pre-wrap' }}>{m.body}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <AuditTrail trail={trail} />
    </article>
  );
}
