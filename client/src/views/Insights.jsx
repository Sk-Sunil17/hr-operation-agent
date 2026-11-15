// What the agent is doing for this company, and whether it can be trusted.

import { useState } from 'react';
import { api } from '../api.js';
import { Kpis, useBusy, useLoad } from '../shared.jsx';
import { minutes, pct, ruleTitle, statusOf } from '../format.js';

function Bars({ rows }) {
  const top = Math.max(1, ...rows.map(([, n]) => n));
  return (
    <ul className="bars">
      {rows.map(([label, n, tone]) => (
        <li key={label}>
          <span className="bars-label">{label}</span>
          <span className="bars-track">
            <span className={`bars-fill bars-${tone || 'neutral'}`} style={{ width: `${(n / top) * 100}%` }} />
          </span>
          <span className="bars-n">{n}</span>
        </li>
      ))}
    </ul>
  );
}

export default function InsightsView({ tenant, company, onError }) {
  const [m] = useLoad(() => api.metrics(tenant), [tenant], onError);
  const [audit] = useLoad(() => api.audit(tenant), [tenant], onError);
  const [check, setCheck] = useState(null);
  const [busy, run] = useBusy(onError);

  if (!m) {
    return (
      <div className="layout layout-wide">
        <div className="blank">
          <p>Loading…</p>
        </div>
      </div>
    );
  }

  const chain = audit?.chain;
  const outcomes = Object.entries(m.by_status)
    .map(([status, n]) => [statusOf(status).label, n, statusOf(status).tone])
    .sort((a, b) => b[1] - a[1]);
  const topics = Object.entries(m.by_rule)
    .map(([rule, n]) => [ruleTitle(rule), n])
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6);

  return (
    <div className="layout layout-wide">
      <main className="main">
        <section className="page-head">
          <div>
            <h1>Insights</h1>
            <p>How {company?.name || 'the agent'} is being used, and the evidence it is behaving.</p>
          </div>
        </section>

        <Kpis
          items={[
            { label: 'Requests handled', value: m.total },
            { label: 'Answered automatically', value: m.by_status.answered || 0, tone: 'good' },
            { label: 'Waiting for approval', value: m.queued_awaiting, tone: m.queued_awaiting ? 'flag' : undefined },
            {
              label: 'Policy answers with a source',
              value: pct(m.citation_coverage),
              tone: m.citation_coverage !== null && m.citation_coverage < 1 ? 'bad' : 'good',
            },
          ]}
        />

        <div className="panels panels-2">
          <section className="panel">
            <h2>Outcomes</h2>
            <Bars rows={outcomes} />
          </section>
          <section className="panel">
            <h2>Most common requests</h2>
            <Bars rows={topics} />
          </section>

          <section className="panel">
            <h2>Trust</h2>
            <ul className="trust">
              <li className={chain?.ok === false ? 'trust-bad' : 'trust-ok'}>
                <strong>Audit trail</strong>
                {chain
                  ? chain.ok
                    ? ` intact across ${chain.checked} records. Editing or deleting any record would show here.`
                    : ` broken at record ${chain.broken_at}: ${chain.reason}`
                  : ' checking…'}
              </li>
              <li className={m.verify_failures ? 'trust-warn' : 'trust-ok'}>
                <strong>Safety checks</strong>{' '}
                {m.verify_failures
                  ? `caught ${m.verify_failures} draft${m.verify_failures === 1 ? '' : 's'} and sent them to a person instead.`
                  : 'have not had to stop a single draft.'}
              </li>
              <li className="trust-ok">
                <strong>Duplicates</strong> {m.duplicates_suppressed} repeated request
                {m.duplicates_suppressed === 1 ? '' : 's'} recognised and not acted on twice.
              </li>
            </ul>
            <div className="sample-check">
              <button
                type="button"
                className="btn btn-outline btn-small"
                disabled={Boolean(busy)}
                onClick={() => run('check', async () => setCheck(await api.simulate(tenant)))}
              >
                {busy === 'check' ? 'Checking…' : 'Run the sample requests'}
              </button>
              {check && (
                <span className={`status status-${check.passed === check.total ? 'ok' : 'err'}`}>
                  {check.passed} of {check.total} handled exactly as expected
                </span>
              )}
            </div>
          </section>

          <section className="panel">
            <h2>Time</h2>
            <div className="defs">
              <dl>
                <div>
                  <dt>Estimated time saved</dt>
                  <dd>{m.minutes_saved === null ? 'not claimed' : minutes(m.minutes_saved)}</dd>
                </div>
                <div>
                  <dt>Time spent reviewing</dt>
                  <dd>{minutes(m.minutes_reviewing)}</dd>
                </div>
                <div>
                  <dt>Net</dt>
                  <dd>{m.minutes_saved === null ? '—' : minutes(m.minutes_saved - m.minutes_reviewing)}</dd>
                </div>
              </dl>
            </div>
            {m.minutes_saved === null && (
              <p className="hint">
                No saving is shown until {company?.name || 'the company'} sets how long these took by hand.
              </p>
            )}
          </section>
        </div>

        <details className="how">
          <summary>How these numbers are counted</summary>
          <ul className="how-body reasons">
            {m.caveats.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </details>
      </main>
    </div>
  );
}
