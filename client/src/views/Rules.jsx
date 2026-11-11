// The rule set in force, and what this company changed about it.

import { api } from '../api.js';
import { useLoad } from '../shared.jsx';
import { modeOf, readable } from '../format.js';

export default function RulesView({ tenant, company, onError }) {
  const [data] = useLoad(() => api.rules(tenant), [tenant], onError);
  const rules = data?.items || [];
  const tightened = rules.filter((r) => r.tightened);
  const overlays = data?.segment_overlays || [];

  return (
    <div className="layout">
      <aside className="side">
        <section className="card">
          <h2>How to read this</h2>
          <p className="hint" style={{ marginTop: 0 }}>
            Rules are evaluated top to bottom and the first match wins, so the order is part of the
            meaning. The last rule matches everything, which is why the agent can never fall off the
            end of the list.
          </p>
          <p className="hint">
            A company may move any rule to a <strong>more</strong> restrictive mode and may add a
            citation requirement. It can never do the reverse: the loader refuses to load a pack
            that tries, so no configuration and no injected instruction can unlock a send.
          </p>
        </section>
        <section className="card">
          <h2>{company?.name} changed</h2>
          {tightened.length === 0 ? (
            <p className="empty-list">Nothing. This company runs at the shared ceiling.</p>
          ) : (
            <ul className="reasons">
              {tightened.map((r) => (
                <li key={r.id}>
                  <strong>{r.id}</strong>: {r.base_mode} <span className="arrow">→</span> {r.mode}
                </li>
              ))}
            </ul>
          )}
          {overlays.length > 0 && (
            <>
              <h3>Populations that tighten further</h3>
              <ul className="reasons">
                {overlays.map((o) => (
                  <li key={o.segment}>
                    <strong>{o.label}</strong>:{' '}
                    {o.rules.map((r) => `${r.id} → ${r.mode}`).join(', ')}
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
        <section className="card">
          <h2>The four modes</h2>
          <div className="defs">
            <dl>
              {['auto', 'prepare', 'hand_over', 'refuse'].map((m) => (
                <div key={m}>
                  <dt>
                    <span className={`mode mode-${m}`}>{modeOf(m).short}</span>
                  </dt>
                  <dd style={{ fontWeight: 400, textAlign: 'left' }}>{modeOf(m).label}</dd>
                </div>
              ))}
            </dl>
          </div>
        </section>
      </aside>

      <main className="main">
        <section className="panel">
          <div className="panel-head">
            <h2>{rules.length} rules in force</h2>
            <span className="tag">{tightened.length} tightened by this company</span>
          </div>
          <div className="table-scroll">
            <table className="grid">
              <thead>
                <tr>
                  <th>Rule</th>
                  <th>Fires when</th>
                  <th>Mode</th>
                  <th>Cite</th>
                  <th>Opens</th>
                  <th>Set by</th>
                </tr>
              </thead>
              <tbody>
                {rules.map((r) => (
                  <tr key={r.id} className={r.tightened ? 'is-open' : undefined}>
                    <td>
                      <code>{r.id}</code>
                    </td>
                    <td>{readable(r.when)}</td>
                    <td>
                      {r.tightened && (
                        <>
                          <span className="mode mode-none">{modeOf(r.base_mode).short}</span>
                          <span className="arrow">→</span>
                        </>
                      )}
                      <span className={`mode mode-${r.mode}`}>{modeOf(r.mode).short}</span>
                    </td>
                    <td>{r.needs_citation ? 'yes' : ''}</td>
                    <td>{r.opens_case || ''}</td>
                    <td className={`set-by ${r.set_by === 'tenant' ? 'set-by-tenant' : ''}`}>{r.set_by}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel">
          <h2>What each rule says, in words</h2>
          <p className="hint" style={{ marginTop: 0, marginBottom: 12 }}>
            Every rule carries a reason written for the person it affected. This is what an employee
            or an auditor is shown, not a rule id.
          </p>
          <div className="table-scroll">
            <table className="grid grid-tight">
              <tbody>
                {rules.map((r) => (
                  <tr key={r.id}>
                    <td style={{ width: '4rem' }}>
                      <code>{r.id}</code>
                    </td>
                    <td>{r.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </main>
    </div>
  );
}
