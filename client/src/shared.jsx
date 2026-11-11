// Pieces every screen uses: the stage rail, the checks list, the record list on
// the left, the audit trail, and two small hooks.

import { useCallback, useEffect, useRef, useState } from 'react';
import { clock, modeOf, statusOf } from './format.js';

// The agent's seven stages, in order. These are the same names the audit trail
// uses, so the picture and the record cannot drift apart.
// Retrieve comes BEFORE decide, which is the real order and not the obvious one:
// "is there a policy in force for this" is a fact the rules test, so the probe
// has to have run before the rule engine can decide anything. The probe is
// read-only by construction, which is what makes that safe.
export const STAGES = [
  ['receive', 'Receive', 'Store the message before anything reads it'],
  ['understand', 'Understand', 'What is it, and how sensitive'],
  ['retrieve', 'Find policy', 'Read-only. Only versions in force today'],
  ['decide', 'Decide', 'A rule picks the mode, from facts'],
  ['draft', 'Draft', 'Quote the policy, do not paraphrase'],
  ['verify', 'Verify', 'Pass or fail. Failure escalates'],
  ['act', 'Outcome', 'Send, queue, hand over, or decline'],
];

const NOTE = { error: 'Failed', skipped: 'Not needed' };

export function Rail({ trail, fresh }) {
  // `handover` rows belong to the final station: a handover IS the outcome.
  const seen = new Map();
  (trail || []).forEach((row) => {
    const stage = row.stage === 'handover' ? 'act' : row.stage;
    const existing = seen.get(stage);
    if (!existing || row.seq > existing.seq) seen.set(stage, { ...row, handover: row.stage === 'handover' });
  });

  return (
    <ol className={`rail ${fresh ? 'rail-fresh' : ''}`} aria-label="What the agent did, step by step">
      {STAGES.map(([id, name, blurb], i) => {
        const row = seen.get(id);
        let state = 'pending';
        let note = null;
        if (row) {
          state = 'done';
          if (id === 'verify' && /^failed/.test(row.action)) {
            state = 'error';
            note = 'Failed';
          } else if (id === 'act' && row.handover) {
            state = 'flag';
            note = 'A person has it';
          }
          note = note || NOTE[row.status] || null;
        }
        return (
          <li key={id} className={`station station-${state}`} style={{ '--i': i }}>
            <span className="marker" aria-hidden="true">
              {i + 1}
            </span>
            <div className="station-body">
              <p className="station-name">
                {name}
                {note && <span className="station-note">{note}</span>}
              </p>
              <p className="station-text">{row ? row.action : blurb}</p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export function CheckIcon({ status }) {
  const label = { pass: 'Passed', fail: 'Failed', skip: 'Not applicable' }[status] || status;
  return (
    <span className="check-icon" role="img" aria-label={label}>
      <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
        {status === 'pass' && (
          <path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        )}
        {status === 'fail' && (
          <>
            <path d="M8 3.5v5.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            <circle cx="8" cy="12.2" r="1.2" fill="currentColor" />
          </>
        )}
        {status === 'skip' && <path d="M4 8h8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />}
      </svg>
    </span>
  );
}

export function Checks({ checks, columns = 2 }) {
  if (!checks?.length) return <p className="empty-list">No checks were recorded.</p>;
  return (
    <ul className={`checks ${columns === 2 ? 'checks-2' : ''}`}>
      {checks.map((c) => (
        <li key={c.id} className={`check check-${c.status}`}>
          <CheckIcon status={c.status} />
          <div>
            <p className="check-label">{c.label}</p>
            <p className="check-detail">{c.detail}</p>
          </div>
        </li>
      ))}
    </ul>
  );
}

// rows: [{ id, title, right, status: {label, tone}, meta }]
export function RecordList({ rows, selectedId, onSelect, empty }) {
  if (!rows.length) return <p className="empty-list">{empty}</p>;
  return (
    <ul className="rec-list">
      {rows.map((row) => (
        <li key={row.id}>
          <button
            type="button"
            className="rec-item"
            aria-current={row.id === selectedId ? 'true' : undefined}
            onClick={() => onSelect(row.id)}
          >
            <span className="rec-top">
              <span className="rec-title">{row.title}</span>
              {row.right && <span className="rec-right">{row.right}</span>}
            </span>
            <span className="rec-bottom">
              <span className={`status status-${row.status.tone}`}>{row.status.label}</span>
              <span className="rec-meta">{row.meta}</span>
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

const WHO = { agent: 'Agent', rule: 'Rule', person: 'Person', scheduler: 'Schedule' };

export function AuditTrail({ trail, showDetail = false }) {
  if (!trail?.length) return null;
  return (
    <section className="panel audit">
      <h2>Audit trail</h2>
      <p className="hint" style={{ marginTop: 0, marginBottom: 12 }}>
        Each row's hash covers the row before it. Changing or removing one breaks the chain visibly.
      </p>
      <ol>
        {trail.map((row) => (
          <li key={row.seq} className={`who-${row.actor_kind}`}>
            <time dateTime={row.at}>{clock(row.at)}</time>
            <span className="who">{WHO[row.actor_kind] || row.actor_kind}</span>
            <span className="stage">{row.stage}</span>
            <span className="what">
              {/^failed/.test(row.action) && <span className="failed">Failed. </span>}
              {row.action}
              {row.actor_kind === 'rule' && <span className="tag tag-mono" style={{ marginLeft: 8 }}>{row.actor}</span>}
              {showDetail && Object.keys(row.detail || {}).length > 0 && (
                <span className="audit-detail">{JSON.stringify(row.detail)}</span>
              )}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function ModeBadge({ mode }) {
  return <span className={`mode mode-${mode || 'none'}`}>{modeOf(mode).short}</span>;
}

export function StatusDot({ status }) {
  const s = statusOf(status);
  return <span className={`status status-${s.tone}`}>{s.label}</span>;
}

export function Kpis({ items }) {
  return (
    <dl className="kpis">
      {items.map((item) => (
        <div key={item.label} className={item.tone ? `kpi-${item.tone}` : undefined}>
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
          {item.note && <p className="kpi-note">{item.note}</p>}
        </div>
      ))}
    </dl>
  );
}

// Run one action at a time, with a busy flag and one place for errors.
export function useBusy(onError) {
  const [busy, setBusy] = useState(null);
  const run = useCallback(
    async (kind, action) => {
      setBusy(kind);
      onError(null);
      try {
        await action();
      } catch (err) {
        onError(err.message);
      } finally {
        setBusy(null);
      }
    },
    [onError],
  );
  return [busy, run];
}

// Load something once per dependency change, with loading and error states.
export function useLoad(loader, deps, onError) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const latest = useRef(0);
  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    const request = ++latest.current;
    setLoading(true);
    loader()
      .then((result) => {
        if (request === latest.current) setData(result);
      })
      .catch((err) => onError(err.message))
      .finally(() => {
        if (request === latest.current) setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return [data, loading, reload];
}
