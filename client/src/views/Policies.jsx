// The policy library, and what retrieval would actually quote.

import { useState } from 'react';
import { api } from '../api.js';
import { useBusy, useLoad } from '../shared.jsx';

const STATUS_TAG = {
  in_force: 'tag-ok',
  retired: 'tag-err',
  scheduled: 'tag-wait',
  draft: '',
};

export default function PoliciesView({ tenant, actor, people, onError }) {
  const [data] = useLoad(() => api.policies(tenant), [tenant], onError);
  const [query, setQuery] = useState('');
  const [scoped, setScoped] = useState(true);
  const [result, setResult] = useState(null);
  const [busy, run] = useBusy(onError);

  const search = () => {
    if (!query.trim()) return;
    run('search', async () => setResult(await api.policySearch(tenant, query, scoped ? actor : null)));
  };

  const actorName = people.find((p) => p.id === actor)?.name || actor;

  return (
    <div className="layout">
      <aside className="side">
        <section className="card">
          <h2>What would be quoted</h2>
          <label className="field-label" htmlFor="q">
            Search the corpus
          </label>
          <input
            id="q"
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && search()}
            placeholder="carry over unused leave"
          />
          <label className="hint" style={{ display: 'block', marginTop: 10 }}>
            <input
              type="checkbox"
              checked={scoped}
              onChange={(e) => setScoped(e.target.checked)}
              style={{ width: 'auto', minHeight: 0, marginRight: 6 }}
            />
            scope to {actorName}
          </label>
          <button
            type="button"
            className="btn btn-primary btn-block"
            style={{ marginTop: 12 }}
            onClick={search}
            disabled={Boolean(busy)}
          >
            {busy === 'search' ? 'Searching…' : 'Search'}
          </button>
          <p className="hint">
            Retrieval is lexical, over sections, and only over versions in force today. That is a
            feature: you can be told exactly why a passage matched.
          </p>
        </section>

        <section className="card">
          <h2>Version rules</h2>
          <p className="hint" style={{ marginTop: 0 }}>
            Only a version whose status is <strong>in force</strong> and whose dates cover today can
            ever be quoted. Retired and scheduled versions are invisible to retrieval.
          </p>
          <p className="hint">
            Two unscoped versions in force for one policy is a data fault. The agent refuses to
            answer rather than choosing one.
          </p>
        </section>
      </aside>

      <main className="main">
        {result && (
          <section className="panel">
            <div className="panel-head">
              <h2>Search result</h2>
              <span className="tag">
                {result.sections_searched} section{result.sections_searched === 1 ? '' : 's'} searched
              </span>
            </div>
            <p className="hint" style={{ marginTop: 0 }}>
              Terms after dropping question frames and stopwords:{' '}
              <code>{(result.query_terms || []).join(', ')}</code>
            </p>
            {result.conflict ? (
              <ul className="reasons">
                {result.conflicts.map((c) => (
                  <li key={c}>{c}</li>
                ))}
              </ul>
            ) : result.citations?.length ? (
              <div className="sections">
                {result.citations.map((c) => (
                  <div key={`${c.doc_id}-${c.section_id}`} className="section-item">
                    <span className="section-score">{c.score.toFixed(2)}</span>
                    <h3>
                      {c.doc_title} v{c.version} · section {c.section_id} — {c.heading}
                    </h3>
                    <p style={{ fontSize: 14 }}>{c.quoted_text}</p>
                    <p className="hint hint-tight">
                      matched: {(c.matched_terms || []).map((t) => (
                        <span key={t} className="tag tag-mono">
                          {t}
                        </span>
                      ))}
                    </p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="empty-list">Nothing in force covers this. The agent would hand it to a person.</p>
            )}
          </section>
        )}

        <section className="panel">
          <h2>The corpus</h2>
          <div className="table-scroll">
            <table className="grid">
              <thead>
                <tr>
                  <th>Policy</th>
                  <th>Version</th>
                  <th>Status</th>
                  <th>From</th>
                  <th>To</th>
                  <th>Applies to</th>
                  <th>Quotable today</th>
                  <th>Owner</th>
                </tr>
              </thead>
              <tbody>
                {(data?.items || []).flatMap((doc) =>
                  doc.versions.map((v, i) => (
                    <tr key={`${doc.id}-${v.version}`} className={v.quotable_today ? 'is-open' : undefined}>
                      <td>{i === 0 ? `${doc.title}` : ''}</td>
                      <td>
                        <code>{v.version}</code>
                      </td>
                      <td>
                        <span className={`tag ${STATUS_TAG[v.status] || ''}`}>{v.status.replace('_', ' ')}</span>
                      </td>
                      <td>{v.effective_from || '—'}</td>
                      <td>{v.effective_to || '—'}</td>
                      <td>
                        {Object.keys(v.scope || {}).length
                          ? Object.entries(v.scope)
                              .map(([k, val]) => `${k} = ${val}`)
                              .join(', ')
                          : 'everyone'}
                      </td>
                      <td>{v.quotable_today ? 'yes' : 'no'}</td>
                      <td>{i === 0 ? doc.owner_role || '—' : ''}</td>
                    </tr>
                  )),
                )}
              </tbody>
            </table>
          </div>
          <p className="hint">
            Rows highlighted in gold are the versions that may be quoted right now. Where two are
            highlighted for one policy, each is scoped to a different population — that is
            legitimate, and different from the conflict the agent refuses to answer past.
          </p>
        </section>
      </main>
    </div>
  );
}
