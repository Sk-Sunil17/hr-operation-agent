// Measurement, with the caveats attached. The caveat list is never empty.

import { api } from '../api.js';
import { Kpis, useLoad } from '../shared.jsx';
import { minutes, pct } from '../format.js';

export default function MetricsView({ tenant, company, onError }) {
  const [m] = useLoad(() => api.metrics(tenant), [tenant], onError);
  if (!m) {
    return (
      <div className="layout layout-wide">
        <div className="blank">
          <p>Loading…</p>
        </div>
      </div>
    );
  }

  const coverageBad = m.citation_required > 0 && m.citation_coverage !== null && m.citation_coverage < 1;

  return (
    <div className="layout layout-wide">
      <main className="main">
        <Kpis
          items={[
            { label: 'Requests handled', value: m.total },
            {
              label: 'Sent by the agent alone',
              value: m.by_status.answered || 0,
              tone: 'good',
              note: 'the only thing that can count as a saving',
            },
            {
              label: 'Waiting for a person',
              value: m.queued_awaiting,
              tone: 'flag',
              note: 'counted as review cost, not saving',
            },
            {
              label: 'Citations where required',
              value: pct(m.citation_coverage),
              tone: coverageBad ? 'bad' : 'good',
              note: `${m.citation_given} of ${m.citation_required}`,
            },
          ]}
        />

        <section className="caveats">
          <h2>Read these before quoting any number above</h2>
          <ul>
            {m.caveats.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </section>

        <div className="panels" style={{ marginTop: 16 }}>
          <section className="panel">
            <h2>Time</h2>
            <div className="defs">
              <dl>
                <div>
                  <dt>Estimated time saved</dt>
                  <dd>{m.minutes_saved === null ? 'not claimed' : minutes(m.minutes_saved)}</dd>
                </div>
                <div>
                  <dt>Review time this created</dt>
                  <dd>{minutes(m.minutes_reviewing)}</dd>
                </div>
                <div>
                  <dt>Net</dt>
                  <dd>
                    {m.minutes_saved === null
                      ? '—'
                      : minutes(m.minutes_saved - m.minutes_reviewing)}
                  </dd>
                </div>
              </dl>
            </div>
            <p className="hint">
              {m.minutes_saved === null
                ? `${company?.name} has not supplied a baseline for how long one of these took a person before, so no saving is reported. That is deliberate: the alternative is inventing one.`
                : 'Saved time uses this company’s own estimate. Review time is always counted against it, because a queued case costs a person minutes it would not otherwise have cost.'}
            </p>
          </section>

          <section className="panel">
            <h2>Where requests ended up</h2>
            <table className="grid">
              <tbody>
                {Object.entries(m.by_status).map(([status, n]) => (
                  <tr key={status}>
                    <td>{status.replace('_', ' ')}</td>
                    <td className="num">{n}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {Object.keys(m.human_verdicts).length > 0 && (
              <>
                <h3>What people decided</h3>
                <table className="grid">
                  <tbody>
                    {Object.entries(m.human_verdicts).map(([verdict, n]) => (
                      <tr key={verdict}>
                        <td>{verdict.replace('_', ' ')}</td>
                        <td className="num">{n}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="hint">
                  Every one of these is a labelled example: approved is a positive, edited is a
                  preference pair, sent back carries the cause. This is the only training data this
                  product will ever have, which is why it is recorded from the first run.
                </p>
              </>
            )}
          </section>

          {Object.keys(m.rejection_reasons || {}).length > 0 && (
            <section className="panel">
              <h2>Why drafts were rejected</h2>
              <table className="grid">
                <tbody>
                  {Object.entries(m.rejection_reasons).map(([reason, n]) => (
                    <tr key={reason}>
                      <td>{reason}</td>
                      <td className="num">{n}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <h3>Which part of the agent that points at</h3>
              <table className="grid">
                <tbody>
                  {Object.entries(m.rejections_point_at || {}).map(([part, n]) => (
                    <tr key={part}>
                      <td>{part}</td>
                      <td className="num">{n}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="hint">
                Free text is unreadable in bulk, so a rejection is recorded as a code. A cluster in
                one place is a lead — retrieval quoting the wrong section is a different problem from
                drafting putting it badly.
              </p>
            </section>
          )}

          <section className="panel">
            <h2>Which rules fired</h2>
            <table className="grid">
              <tbody>
                {Object.entries(m.by_rule).map(([rule, n]) => (
                  <tr key={rule}>
                    <td>
                      <code>{rule}</code>
                    </td>
                    <td className="num">{n}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section className="panel">
            <h2>Quality signals</h2>
            <div className="defs">
              <dl>
                <div>
                  <dt>Verification failures</dt>
                  <dd>{m.verify_failures}</dd>
                </div>
                <div>
                  <dt>Duplicates suppressed</dt>
                  <dd>{m.duplicates_suppressed}</dd>
                </div>
                <div>
                  <dt>Answers that needed a citation</dt>
                  <dd>{m.citation_required}</dd>
                </div>
                <div>
                  <dt>…and carried one</dt>
                  <dd>{m.citation_given}</dd>
                </div>
              </dl>
            </div>
            <p className="hint">
              Deflection rate is deliberately absent. It counts answers given, not answers that were
              right, and in HR a confident wrong answer is a legal event rather than a bad metric.
            </p>
          </section>
        </div>
      </main>
    </div>
  );
}
