// One request, as a person reads it: what happened, the reply, where the answer
// came from, and what happens next. How the agent got there is one click away.

import { AuditTrail, Checks, Rail } from '../shared.jsx';
import { kindOf, ruleTitle, statusOf, when } from '../format.js';

const OUTCOME = {
  answered: ['outcome-ok', 'Answered', 'Sent straight away.'],
  queued: ['outcome-wait', 'Waiting for approval', 'Nothing has been sent or changed yet. An HR approver reviews it first.'],
  handed_over: ['outcome-wait', 'Passed to HR', 'A person from HR picks this up. The agent only acknowledged it.'],
  refused: ['outcome-err', 'Declined', 'The agent declined and told the person why.'],
  split: ['', 'Split into separate requests', 'One message asked for several things, so each is handled on its own.'],
  suppressed: ['', 'Already received', 'This was a repeat of a request already in progress, so nothing was done twice.'],
};

const REPLY_LABEL = {
  answered: 'Reply sent',
  queued: 'Draft reply, not sent yet',
};

export default function RequestSummary({ data, people = [], fresh = false, withTrail = false }) {
  const { item, decision, citations, plans, messages, trail, children } = data;
  const nameOf = (id) => people.find((p) => p.id === id)?.name || id;
  const [tone, headline, explainer] = OUTCOME[item.status] || ['', statusOf(item.status).label, ''];
  const asked = messages.find((m) => m.direction === 'inbound')?.body || 'Request';
  // Sending the reply is itself a planned action; the reply is already shown above.
  const changes = plans.filter((p) => p.status !== 'cancelled' && !/message$/.test(p.operation));
  const sources = decision?.retrieval?.citations || [];
  const titleOf = (c) => sources.find((s) => s.doc_id === c.doc_id && s.section_id === c.section_id);
  const checks = decision?.checks || [];
  const failed = checks.filter((c) => c.status === 'fail');
  const passed = checks.filter((c) => c.status === 'pass').length;

  return (
    <article className="detail">
      <header className="detail-head">
        <div className="detail-title">
          <p className="eyebrow">
            {kindOf(item.rule_id) || 'Request'} · {item.requester_name} · {when(item.created_at)}
          </p>
          <h1>{asked}</h1>
        </div>
      </header>

      <section className={`outcome ${tone}`}>
        <h2>{headline}</h2>
        <p className="outcome-note">
          {explainer}
          {item.status === 'answered' && citations.length > 0 && ' It quotes company policy word for word.'}
        </p>
        {decision?.draft && (
          <>
            <p className="letter-label">{REPLY_LABEL[item.status] || `Message to ${item.requester_name}`}</p>
            <pre className="letter">{decision.draft}</pre>
          </>
        )}
        {decision?.human_verdict && (
          <p className="outcome-note">
            {decision.human_actor ? nameOf(decision.human_actor) : 'A person'} {decision.human_verdict.replace('_', ' ')} this
            {decision.human_note ? `: “${decision.human_note}”` : '.'}
          </p>
        )}
      </section>

      {changes.length > 0 && (
        <section className="panel">
          <h2>{changes.every((p) => p.status === 'committed') ? 'Changes made' : 'Changes waiting for approval'}</h2>
          <ul className="changes">
            {changes.map((p) => (
              <li key={p.id}>
                <span className={`tag ${p.status === 'committed' ? 'tag-ok' : 'tag-wait'}`}>
                  {p.status === 'committed' ? 'Done' : 'On approval'}
                </span>
                <pre>{p.preview_text}</pre>
              </li>
            ))}
          </ul>
        </section>
      )}

      {children?.length > 0 && (
        <section className="panel">
          <h2>Handled separately</h2>
          <ul className="changes">
            {children.map((c) => (
              <li key={c.id}>
                <span className={`status status-${statusOf(c.status).tone}`}>{statusOf(c.status).label}</span>
                <span>{ruleTitle(c.rule_id)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {citations.length > 0 && (
        <section className="panel">
          <h2>Source</h2>
          {citations.map((c) => {
            const source = titleOf(c);
            return (
              <blockquote key={c.id} className="quote">
                {c.quoted_text}
                <span className="quote-source">
                  {source?.doc_title || c.doc_id}
                  {source?.heading ? ` — ${source.heading}` : ''} · version {c.version}
                </span>
              </blockquote>
            );
          })}
        </section>
      )}

      <details className="how">
        <summary>
          How the agent decided
          <span className={`tag ${failed.length ? 'tag-err' : 'tag-ok'}`}>
            {failed.length ? `${failed.length} check${failed.length === 1 ? '' : 's'} failed` : `${passed} safety checks passed`}
          </span>
        </summary>
        <div className="how-body">
          <p>
            <strong>{ruleTitle(item.rule_id)}.</strong> {decision?.rule_reason}
          </p>
          <Rail trail={trail} fresh={fresh} />
          <Checks checks={checks} />
          {withTrail && <AuditTrail trail={trail} />}
        </div>
      </details>
    </article>
  );
}
