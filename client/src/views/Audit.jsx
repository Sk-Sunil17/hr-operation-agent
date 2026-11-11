// The tamper-evident trail, and whether it still verifies.

import { api } from '../api.js';
import { useLoad } from '../shared.jsx';
import { clock } from '../format.js';

const WHO = { agent: 'Agent', rule: 'Rule', person: 'Person', scheduler: 'Schedule' };

export default function AuditView({ tenant, onError }) {
  const [data] = useLoad(() => api.audit(tenant), [tenant], onError);
  const chain = data?.chain;
  const rows = data?.items || [];

  const byStage = rows.reduce((acc, r) => ({ ...acc, [r.stage]: (acc[r.stage] || 0) + 1 }), {});

  return (
    <div className="layout">
      <aside className="side">
        <section className={`card ${chain && !chain.ok ? 'outcome outcome-err' : ''}`}>
          <h2>Chain</h2>
          {chain ? (
            chain.ok ? (
              <>
                <p>
                  <span className="status status-lg status-ok">
                    Intact across {chain.checked} row{chain.checked === 1 ? '' : 's'}
                  </span>
                </p>
                <p className="hint">
                  Every row's hash covers the row before it. An edited row fails its own hash; a
                  removed row breaks the link between its neighbours.
                </p>
              </>
            ) : (
              <>
                <p>
                  <span className="status status-lg status-err">Broken at row {chain.broken_at}</span>
                </p>
                <p className="hint">{chain.reason}</p>
              </>
            )
          ) : (
            <p className="hint">Loading…</p>
          )}
        </section>

        <section className="card">
          <h2>An honest limit</h2>
          <p className="hint" style={{ marginTop: 0 }}>
            {data?.limitation}
          </p>
        </section>

        <section className="card">
          <h2>Rows by stage</h2>
          <div className="defs">
            <dl>
              {Object.entries(byStage).map(([stage, n]) => (
                <div key={stage}>
                  <dt>{stage}</dt>
                  <dd>{n}</dd>
                </div>
              ))}
            </dl>
          </div>
        </section>
      </aside>

      <main className="main">
        <section className="panel audit">
          <div className="panel-head">
            <h2>Most recent first</h2>
            <span className="tag">{rows.length} rows</span>
          </div>
          <ol>
            {rows.map((r) => (
              <li key={r.seq} className={`who-${r.actor_kind}`}>
                <time dateTime={r.at}>{clock(r.at)}</time>
                <span className="who">{WHO[r.actor_kind] || r.actor_kind}</span>
                <span className="stage">{r.stage}</span>
                <span className="what">
                  {/^failed/.test(r.action) && <span className="failed">Failed. </span>}
                  {r.action}
                  {r.actor_kind === 'rule' && (
                    <span className="tag tag-mono" style={{ marginLeft: 8 }}>
                      {r.actor}
                    </span>
                  )}
                  {r.work_item_id && (
                    <span className="tag tag-mono" style={{ marginLeft: 4 }}>
                      {r.work_item_id}
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ol>
        </section>
      </main>
    </div>
  );
}
