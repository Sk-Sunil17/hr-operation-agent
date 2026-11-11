// The company's configuration, where each setting came from, and its own tests.

import { useState } from 'react';
import { api } from '../api.js';
import { useBusy, useLoad } from '../shared.jsx';
import { modeOf } from '../format.js';

export default function PackView({ tenant, company, onError }) {
  const [config] = useLoad(() => api.config(tenant), [tenant], onError);
  const [sim, setSim] = useState(null);
  const [asOf, setAsOf] = useState('');
  const [busy, run] = useBusy(onError);

  const simulate = () => run('sim', async () => setSim(await api.simulate(tenant, asOf || undefined)));

  const errors = (config?.problems || []).filter((p) => p.severity === 'error');
  const warnings = (config?.problems || []).filter((p) => p.severity === 'warning');

  return (
    <div className="layout">
      <aside className="side">
        <section className="card">
          <h2>Validation</h2>
          {config ? (
            errors.length === 0 ? (
              <p>
                <span className="status status-lg status-ok">
                  Clean{warnings.length ? ` · ${warnings.length} warning(s)` : ''}
                </span>
              </p>
            ) : (
              <p>
                <span className="status status-lg status-err">{errors.length} error(s)</span>
              </p>
            )
          ) : (
            <p className="hint">Loading…</p>
          )}
          {(config?.problems || []).map((p) => (
            <p key={`${p.where}${p.message}`} className="hint">
              <strong>{p.where}</strong>: {p.message}
            </p>
          ))}
          <p className="hint">
            Onboarding a company means filling in a pack and getting this clean. Not forking the
            code.
          </p>
        </section>

        <section className="card">
          <h2>Run this company's own tests</h2>
          <p className="hint" style={{ marginTop: 0 }}>
            Its golden cases, through the real kernel, in a throwaway database.
          </p>
          <label className="field-label" htmlFor="asof">
            As if it were this date
          </label>
          <input id="asof" type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
          <p className="hint hint-tight">
            Policy versions are effective-dated, so you can see what this company's agent{' '}
            <strong>will</strong> answer the day a new policy takes effect — before it does.
          </p>
          <button
            type="button"
            className="btn btn-primary btn-block"
            style={{ marginTop: 12 }}
            onClick={simulate}
            disabled={Boolean(busy)}
          >
            {busy === 'sim' ? 'Running…' : asOf ? `Simulate as at ${asOf}` : 'Simulate'}
          </button>
          {sim && (
            <p className="hint">
              <strong>
                {sim.passed} of {sim.total}
              </strong>{' '}
              cases reached the rule the company expected.
            </p>
          )}
        </section>

        <section className="card">
          <h2>Configuration</h2>
          <div className="defs">
            <dl>
              <div>
                <dt>Fingerprint</dt>
                <dd>
                  <code>{config?.config_hash}</code>
                </dd>
              </div>
              <div>
                <dt>Time zone</dt>
                <dd>{config?.locale?.timezone}</dd>
              </div>
              <div>
                <dt>Languages</dt>
                <dd>{(config?.locale?.languages || []).join(', ')}</dd>
              </div>
              <div>
                <dt>Working hours</dt>
                <dd>
                  {config?.locale?.working_hours?.start}–{config?.locale?.working_hours?.end}
                </dd>
              </div>
              <div>
                <dt>Holiday calendar</dt>
                <dd>{config?.locale?.holiday_calendar || 'none supplied'}</dd>
              </div>
              <div>
                <dt>Tone</dt>
                <dd>{config?.manifest?.tone}</dd>
              </div>
              <div>
                <dt>Channels</dt>
                <dd>{(config?.manifest?.channels || []).join(', ')}</dd>
              </div>
              <div>
                <dt>Model provider</dt>
                <dd>{config?.manifest?.models?.provider}</dd>
              </div>
              <div>
                <dt>Trains on your data</dt>
                <dd>no</dd>
              </div>
              <div>
                <dt>Jurisdiction packs</dt>
                <dd>{(config?.manifest?.jurisdiction_packs || []).join(', ') || 'none'}</dd>
              </div>
            </dl>
          </div>
          <p className="hint">
            The fingerprint is stamped onto every decision, so months later you can prove which
            configuration produced a given answer.
          </p>
        </section>
      </aside>

      <main className="main">
        {sim && (
          <section className="panel">
            <div className="panel-head">
              <h2>
                {sim.passed} of {sim.total} golden cases
                {sim.on && <span className="panel-count">as at {sim.on}</span>}
              </h2>
              <span className={`tag ${sim.passed === sim.total ? 'tag-ok' : 'tag-err'}`}>
                {sim.passed === sim.total ? 'green' : 'not green'}
              </span>
            </div>
            <div className="table-scroll">
              <table className="grid">
                <thead>
                  <tr>
                    <th>Case</th>
                    <th>Expected</th>
                    <th>Got</th>
                    <th>Verified</th>
                    <th>Quoted</th>
                    <th>Message</th>
                  </tr>
                </thead>
                <tbody>
                  {sim.results.map((r) => (
                    <tr key={r.id} className={r.pass ? undefined : 'is-open'}>
                      <td>
                        <code>{r.id}</code>
                      </td>
                      <td>{r.expected}</td>
                      <td>
                        <span className={`tag ${r.pass ? 'tag-ok' : 'tag-err'}`}>{r.got}</span>
                      </td>
                      <td>{r.verified ? 'yes' : 'no'}</td>
                      <td>
                        {(r.cited || []).map((c) => (
                          <span key={c} className="tag tag-mono">
                            {c}
                          </span>
                        )) || ''}
                      </td>
                      <td>{r.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="hint">
              Rules reached: {sim.rules_reached.join(', ')}.
              {sim.rules_never_reached.length > 0 && (
                <>
                  {' '}
                  Never reached by these cases: {sim.rules_never_reached.join(', ')} — honest
                  coverage, not a score.
                </>
              )}
            </p>
          </section>
        )}

        <section className="panel">
          <h2>Roles</h2>
          <div className="table-scroll">
            <table className="grid">
              <thead>
                <tr>
                  <th>Role</th>
                  <th>Label</th>
                  <th>Counts as HR</th>
                  <th>Deputies</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(config?.roles || {}).map(([id, label]) => (
                  <tr key={id}>
                    <td>
                      <code>{id}</code>
                    </td>
                    <td>{label}</td>
                    <td>{(config?.hr_roles || []).includes(id) ? 'yes' : ''}</td>
                    <td>{(config?.delegation?.[id] || []).join(', ') || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="hint">
            Nothing in the kernel knows which role names mean "HR". {company?.name} declares that,
            which is why acting on another person's record is a configured permission rather than a
            hardcoded role name.
          </p>
        </section>

        {(config?.segments || []).length > 0 && (
          <section className="panel">
            <h2>Populations</h2>
            <div className="table-scroll">
              <table className="grid">
                <thead>
                  <tr>
                    <th>Population</th>
                    <th>Matches</th>
                    <th>Tightens</th>
                  </tr>
                </thead>
                <tbody>
                  {config.segments.map((s) => (
                    <tr key={s.id}>
                      <td>
                        <strong>{s.label}</strong>
                        <br />
                        <code>{s.id}</code>
                      </td>
                      <td>
                        {Object.entries(s.match)
                          .map(([k, v]) => `${k} = ${Array.isArray(v) ? v.join(' or ') : v}`)
                          .join(', ')}
                      </td>
                      <td>
                        {s.rules.length
                          ? s.rules.map((r) => (
                              <span key={r.id} className="tag tag-wait">
                                {r.id} → {modeOf(r.mode).short}
                              </span>
                            ))
                          : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        <section className="panel">
          <h2>Where each setting came from</h2>
          <p className="hint" style={{ marginTop: 0, marginBottom: 12 }}>
            Resolution order is base pack, then this company, then the population. A later layer may
            only tighten.
          </p>
          <div className="table-scroll">
            <table className="grid grid-tight">
              <tbody>
                {Object.entries(config?.provenance || {})
                  .sort()
                  .map(([key, layer]) => (
                    <tr key={key}>
                      <td>
                        <code>{key}</code>
                      </td>
                      <td className={`set-by ${layer !== 'base' ? 'set-by-tenant' : ''}`}>{layer}</td>
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
