// The company's policies: search them in plain words, or open one and read it.

import { useEffect, useState } from 'react';
import { api } from '../api.js';
import { useBusy, useLoad } from '../shared.jsx';

const longDate = (iso) =>
  iso ? new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' }) : '';

const scopeText = (scope) => {
  const parts = Object.entries(scope || {}).map(([k, v]) => `${k.replace(/_/g, ' ')} ${Array.isArray(v) ? v.join(' or ') : v}`);
  return parts.length ? parts.join(', ') : 'Everyone';
};

function Body({ text }) {
  const blocks = text.split(/\n\s*\n/).map((b) => b.trim()).filter(Boolean);
  return blocks.map((block, i) => {
    const lines = block.split('\n');
    if (lines.every((l) => /^\s*([-*]|\d+\.)\s+/.test(l))) {
      return (
        <ul key={i} className="policy-list">
          {lines.map((l, j) => (
            <li key={j}>{l.replace(/^\s*([-*]|\d+\.)\s+/, '')}</li>
          ))}
        </ul>
      );
    }
    return <p key={i}>{lines.join(' ')}</p>;
  });
}

export default function PoliciesView({ tenant, actor, me, onError }) {
  const [data] = useLoad(() => api.policies(tenant), [tenant], onError);
  const [query, setQuery] = useState('');
  const [result, setResult] = useState(null);
  const [open, setOpen] = useState(null);
  const [busy, run] = useBusy(onError);

  const search = (e) => {
    e?.preventDefault();
    if (!query.trim()) return;
    run('search', async () => setResult(await api.policySearch(tenant, query.trim(), actor)));
  };

  useEffect(() => {
    if (open) document.getElementById('policy-reader')?.scrollIntoView({ behavior: 'smooth' });
  }, [open]);

  const read = (id, version) => run(`read-${id}`, async () => setOpen(await api.policyText(tenant, id, version)));

  const library = (data?.items || [])
    .map((doc) => ({ ...doc, live: doc.versions.filter((v) => v.quotable_today) }))
    .filter((doc) => doc.live.length);

  return (
    <div className="layout layout-wide">
      <div className="page-head">
        <div>
          <h1>Policies</h1>
          <p>
            The policies in force today. Ask a question in your own words to find the passage that
            answers it, or open a policy to read it in full.
          </p>
        </div>
      </div>

      <form className="policy-search" onSubmit={search}>
        <input
          type="search"
          aria-label="Search the policies"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="e.g. Can I carry over unused leave?"
        />
        <button type="submit" className="btn btn-primary" disabled={Boolean(busy) || !query.trim()}>
          {busy === 'search' ? 'Searching…' : 'Search'}
        </button>
      </form>

      {result && (
        <section className="panel">
          <h2>{result.citations?.length && !result.conflict ? 'What the policies say' : 'No clear answer'}</h2>
          {result.conflict ? (
            <p className="empty-list">
              Two versions of this policy are in force at once, so the agent will not pick one.
              Please ask HR from the Ask screen.
            </p>
          ) : result.citations?.length ? (
            <div className="sections">
              {result.citations.map((c) => (
                <blockquote key={`${c.doc_id}-${c.section_id}`} className="quote">
                  {c.quoted_text}
                  <span className="quote-source">
                    {c.doc_title}, section {c.section_id}: {c.heading} ·{' '}
                    <button type="button" className="inline-link" onClick={() => read(c.doc_id, c.version)}>
                      Read the full policy
                    </button>
                  </span>
                </blockquote>
              ))}
            </div>
          ) : (
            <p className="empty-list">
              None of the policies in force covers this. Ask HR from the Ask screen and a person
              will pick it up.
            </p>
          )}
        </section>
      )}

      <section className="policy-grid">
        {!data && <p className="hint">Loading…</p>}
        {library.map((doc) =>
          doc.live.map((v) => {
            const older = doc.versions.filter((x) => !x.quotable_today).length;
            return (
              <article key={`${doc.id}-${v.version}`} className={`policy-card${open?.id === doc.id && open?.version === v.version ? ' policy-card-open' : ''}`}>
                <h3>{doc.title}</h3>
                <p className="policy-meta">
                  Version {v.version} · in force since {longDate(v.effective_from)}
                </p>
                <p className="policy-meta">Applies to: {scopeText(v.scope)}</p>
                {me?.is_hr && (
                  <p className="policy-meta">
                    {doc.owner_label ? `Owned by ${doc.owner_label}` : ''}
                    {doc.owner_label && doc.review_months ? ' · ' : ''}
                    {doc.review_months ? `reviewed every ${doc.review_months} months` : ''}
                    {older ? ` · ${older} earlier version${older === 1 ? '' : 's'} no longer quoted` : ''}
                  </p>
                )}
                <button
                  type="button"
                  className="btn btn-small"
                  onClick={() => read(doc.id, v.version)}
                  disabled={busy === `read-${doc.id}`}
                >
                  Read policy
                </button>
              </article>
            );
          }),
        )}
      </section>

      {open && (
        <section className="panel policy-reader" id="policy-reader">
          <div className="panel-head">
            <h2>
              {open.title} <span className="policy-meta">version {open.version}</span>
            </h2>
            <button type="button" className="btn btn-small" onClick={() => setOpen(null)}>
              Close
            </button>
          </div>
          {open.sections
            .filter((s) => s.body.trim())
            .map((s) => (
              <div key={s.section_id} className="policy-section">
                <h3>
                  {s.section_id !== '0' && <span className="policy-num">{s.section_id}.</span>} {s.heading}
                </h3>
                <Body text={s.body} />
              </div>
            ))}
        </section>
      )}
    </div>
  );
}
