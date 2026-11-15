// How the agent handles each kind of request, and where HR changes that.
//
// The company rulebook is the floor: HR can make any rule stricter here at
// once, can never go below the rulebook, and relaxing a rule somebody made
// stricter takes a second HR person. Every change is on the audit trail.

import { useEffect, useState } from 'react';
import { api } from '../api.js';
import { useBusy, useLoad } from '../shared.jsx';
import { AREAS, MODES, areaOf, modeOf, ruleTitle, when } from '../format.js';

export default function RulesView({ tenant, actor, onError, onNotice, onChanged }) {
  const [data, , reload] = useLoad(() => api.rules(tenant, actor), [tenant, actor], onError);
  const [area, setArea] = useState('questions');
  const [editing, setEditing] = useState(null);

  const rules = data?.items || [];
  const canChange = Boolean(data?.can_change);
  const shown = rules.filter((r) => areaOf(r.id) === area);
  const tally = Object.fromEntries(MODES.map((m) => [m, rules.filter((r) => r.mode === m).length]));
  const changedCount = rules.filter((r) => r.changed).length;

  return (
    <div className="layout layout-wide">
      <main className="main">
        <section className="page-head">
          <div>
            <h1>How the agent handles requests</h1>
            <p>
              For each kind of request, choose whether the agent answers on its own, drafts for
              approval, or passes it to HR. Your company rulebook sets the minimum; you can always be
              stricter. Relaxing a rule takes a second HR person, and every change is recorded.
            </p>
          </div>
          <dl className="mode-tally">
            {MODES.map((m) => (
              <div key={m}>
                <dt>
                  <span className={`mode mode-${m}`}>{modeOf(m).short}</span>
                </dt>
                <dd>{tally[m] || 0}</dd>
              </div>
            ))}
          </dl>
        </section>

        {!canChange && data && (
          <p className="notice">View only. People in an HR role can change these.</p>
        )}
        {changedCount > 0 && (
          <p className="notice notice-gold">
            {changedCount} rule{changedCount === 1 ? ' has' : 's have'} been made stricter by HR.
          </p>
        )}

        <div className="tabs area-tabs" role="tablist">
          {AREAS.map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              className="tab"
              aria-selected={area === id}
              onClick={() => {
                setArea(id);
                setEditing(null);
              }}
            >
              {label} <span className="count">{rules.filter((r) => areaOf(r.id) === id).length}</span>
            </button>
          ))}
        </div>

        <ul className="rule-list">
          {shown.map((rule) => (
            <li key={rule.id} className={`rule ${editing === rule.id ? 'rule-open' : ''}`}>
              <div className="rule-row">
                <div className="rule-text">
                  <h3>{ruleTitle(rule.id)}</h3>
                  <p>{rule.reason}</p>
                  {rule.changed && (
                    <p className="rule-changed">
                      Changed by {rule.changed.by_name} on {when(rule.changed.at)} · “{rule.changed.note}”
                    </p>
                  )}
                </div>
                <div className="rule-side">
                  <span className={`mode mode-${rule.mode}`}>{modeOf(rule.mode).label}</span>
                  {canChange && editing !== rule.id && (
                    <button type="button" className="btn btn-small btn-outline" onClick={() => setEditing(rule.id)}>
                      Change
                    </button>
                  )}
                </div>
              </div>
              {editing === rule.id && (
                <RuleEditor
                  tenant={tenant}
                  actor={actor}
                  rule={rule}
                  onError={onError}
                  onClose={() => setEditing(null)}
                  onSaved={(saved) => {
                    setEditing(null);
                    reload();
                    onChanged();
                    onNotice(
                      `Saved. ${ruleTitle(saved.rule_id)}: ${modeOf(saved.to).label}. The change is in the audit trail.`,
                    );
                  }}
                />
              )}
            </li>
          ))}
        </ul>

        {data?.segment_overlays?.length > 0 && (
          <p className="hint">
            Also stricter for some groups, set in the company rulebook:{' '}
            {data.segment_overlays
              .map((o) => `${o.label} (${o.rules.map((r) => `${ruleTitle(r.id)}: ${modeOf(r.mode).short}`).join(', ')})`)
              .join('; ')}
            .
          </p>
        )}
      </main>
    </div>
  );
}

function RuleEditor({ tenant, actor, rule, onError, onClose, onSaved }) {
  const [mode, setMode] = useState(rule.mode);
  const [note, setNote] = useState('');
  const [impact, setImpact] = useState(null);
  const [busy, run] = useBusy(onError);
  const relaxing = MODES.indexOf(mode) < MODES.indexOf(rule.mode);

  useEffect(() => {
    if (mode === rule.mode) {
      setImpact(null);
      return undefined;
    }
    let cancelled = false;
    setImpact('loading');
    api
      .previewRule(tenant, rule.id, mode)
      .then((result) => {
        if (!cancelled) setImpact(result);
      })
      .catch((err) => {
        if (cancelled) return;
        setImpact(null);
        onError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [tenant, rule.id, rule.mode, mode, onError]);

  const save = () =>
    run('save', async () => {
      onSaved(await api.changeRule(tenant, rule.id, { actor, mode, note }));
    });

  return (
    <div className="rule-editor">
      <div className="mode-choice" role="radiogroup" aria-label={`How to handle: ${ruleTitle(rule.id)}`}>
        {MODES.map((m) => {
          const allowed = rule.allowed_modes.includes(m);
          return (
            <button
              key={m}
              type="button"
              role="radio"
              aria-checked={mode === m}
              className={`mode-option mode-option-${m}`}
              disabled={!allowed}
              title={allowed ? undefined : `Your company rulebook does not allow less than “${modeOf(rule.pack_mode).label}”.`}
              onClick={() => setMode(m)}
            >
              <span className="mode-option-label">
                {modeOf(m).label}
                {m === rule.pack_mode && <span className="mode-option-default">company default</span>}
              </span>
              <span className="mode-option-text">{allowed ? modeOf(m).blurb : 'Not allowed by your company rulebook'}</span>
            </button>
          );
        })}
      </div>

      {mode !== rule.mode && (
        <>
          <div className="impact">
            {impact === 'loading' ? (
              <p className="hint">Checking against your sample requests…</p>
            ) : impact ? (
              impact.changed.length === 0 ? (
                <p>None of your {impact.examples_checked} sample requests would be handled differently.</p>
              ) : (
                <>
                  <p>
                    <strong>
                      {impact.changed.length} of {impact.examples_checked} sample requests
                    </strong>{' '}
                    would be handled differently:
                  </p>
                  <ul>
                    {impact.changed.map((c) => (
                      <li key={c.id}>
                        “{c.message}” <span className="arrow">—</span> {modeOf(c.before).short} → <strong>{modeOf(c.after).short}</strong>
                      </li>
                    ))}
                  </ul>
                </>
              )
            ) : null}
          </div>

          <label className="field-label" htmlFor={`note-${rule.id}`}>
            Why are you changing this?
          </label>
          <input
            id={`note-${rule.id}`}
            type="text"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="e.g. Reviewing every leave answer while the new policy beds in"
          />
          {relaxing && (
            <p className="hint hint-tight">
              Relaxing a rule: if you were the one who made it stricter, a second HR person has to do this.
            </p>
          )}
        </>
      )}

      <div className="decision-actions">
        <button
          type="button"
          className="btn btn-primary"
          disabled={Boolean(busy) || mode === rule.mode || note.trim().length < 5}
          onClick={save}
        >
          {busy === 'save' ? 'Saving…' : 'Save change'}
        </button>
        <button type="button" className="btn btn-outline" onClick={onClose}>
          Cancel
        </button>
      </div>
    </div>
  );
}
