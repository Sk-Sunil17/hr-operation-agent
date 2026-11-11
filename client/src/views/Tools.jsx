// The systems this company has connected, and what each one is allowed to touch.

import { useState } from 'react';
import { api } from '../api.js';
import { useBusy, useLoad } from '../shared.jsx';

export default function ToolsView({ tenant, company, onError }) {
  const [tools] = useLoad(() => api.tools(tenant), [tenant], onError);
  const [redaction] = useLoad(() => api.redaction(tenant), [tenant], onError);
  const [text, setText] = useState(
    'Hi, I am Maya (maya@acme.test, 07700 900123). I was diagnosed with asthma, my DOB is 14/03/1991 and my payroll ref is PR-456123.',
  );
  const [tried, setTried] = useState(null);
  const [busy, run] = useBusy(onError);

  const tryIt = () => run('try', async () => setTried(await api.tryRedaction(tenant, text)));

  return (
    <div className="layout">
      <aside className="side">
        <section className="card">
          <h2>Field ownership</h2>
          <p className="hint" style={{ marginTop: 0 }}>
            Each system declares, in the pack, the fields it owns. The agent refuses to write a
            field no system owns — that is a refusal with a reason, not a silent failure.
          </p>
          <h3>Never writable, by anything</h3>
          <p>
            {(tools?.never_writable || []).map((f) => (
              <span key={f} className="tag tag-err tag-mono">
                {f}
              </span>
            ))}
          </p>
          <p className="hint">
            Checked when the pack loads and again at the moment of the call, including behind an
            approval. A guarantee worth having is worth checking twice.
          </p>
        </section>

        <section className="card">
          <h2>Try the redaction pack</h2>
          <textarea rows={5} value={text} onChange={(e) => setText(e.target.value)} />
          <button
            type="button"
            className="btn btn-primary btn-block"
            style={{ marginTop: 12 }}
            onClick={tryIt}
            disabled={Boolean(busy)}
          >
            {busy === 'try' ? 'Working…' : 'Redact it'}
          </button>
          {tried && (
            <>
              <p className="hint" style={{ marginTop: 12 }}>
                <strong>Out:</strong>
              </p>
              <pre className="letter" style={{ margin: '6px 0' }}>
                {tried.output}
              </pre>
              {tried.findings.map((f) => (
                <span key={f.pattern} className="tag tag-wait">
                  {f.class} ×{f.count}
                </span>
              ))}
            </>
          )}
        </section>
      </aside>

      <main className="main">
        <section className="panel">
          <div className="panel-head">
            <h2>Connected systems</h2>
            <span className="tag">{(tools?.items || []).length} adapters</span>
          </div>
          <div className="table-scroll">
            <table className="grid">
              <thead>
                <tr>
                  <th>System</th>
                  <th>Kind</th>
                  <th>Implementation</th>
                  <th>Owns these fields</th>
                  <th>Reads</th>
                  <th>Writes</th>
                </tr>
              </thead>
              <tbody>
                {(tools?.items || []).map((t) => (
                  <tr key={t.name}>
                    <td>
                      <strong>{t.name}</strong>
                    </td>
                    <td>{t.kind}</td>
                    <td>
                      <code>{t.implementation}</code>
                    </td>
                    <td>
                      {t.owns.length
                        ? t.owns.map((f) => (
                            <span key={f} className="tag tag-mono">
                              {f}
                            </span>
                          ))
                        : '—'}
                    </td>
                    <td>{t.reads.join(', ') || '—'}</td>
                    <td>{t.writes.join(', ') || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="hint">
            These are mock systems. A real connector implements the same three verbs — read with no
            side effects, prepare which writes only a plan, commit which acts exactly once under a
            key — so swapping one in is a change to this company's pack, never to the kernel.
          </p>
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>Redaction in force: {redaction?.pack}</h2>
            <span className="tag">{(redaction?.patterns || []).length} patterns</span>
          </div>
          <p className="hint" style={{ marginTop: 0 }}>
            Order matters: a specific shape runs before a general one, or a general pattern eats
            part of a specific one and leaves the rest in the clear. Not one identifier format is
            written in the kernel — {company?.name}'s local formats live only in its own pack.
          </p>
          <div className="table-scroll">
            <table className="grid grid-tight">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Pattern</th>
                  <th>Class</th>
                  <th>Becomes</th>
                </tr>
              </thead>
              <tbody>
                {(redaction?.patterns || []).map((p, i) => (
                  <tr key={p.name}>
                    <td className="num">{i + 1}</td>
                    <td>
                      <code>{p.name}</code>
                    </td>
                    <td>{p.class}</td>
                    <td>{p.replacement}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h3>Field names dropped from any stored structure</h3>
          <p>
            {(redaction?.sensitive_keys || []).map((k) => (
              <span key={k} className="tag tag-mono">
                {k}
              </span>
            ))}
          </p>
          <p className="hint">
            Matched as whole words, so <code>bank_holiday</code> and <code>tokenization_policy</code>{' '}
            survive while <code>api_token</code> and <code>bank_details_v2</code> are dropped.
            Contact details are masked rather than dropped, so the record stays readable.
          </p>
        </section>
      </main>
    </div>
  );
}
