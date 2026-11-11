// Interviews that are actually booked, and a read-only look at when a panel is free.
//
// The availability panel calls the SAME solver the agent uses, through the API,
// rather than reimplementing the constraints in JavaScript. A screen that
// disagrees with the thing it previews is worse than no screen: somebody would
// trust the one that was wrong.

import { useState } from 'react';
import { api } from '../api.js';
import { useBusy, useLoad } from '../shared.jsx';

const STATUS = {
  scheduled: { label: 'Booked', tone: 'ok' },
  cancelled: { label: 'Cancelled', tone: 'err' },
  superseded: { label: 'Moved', tone: 'neutral' },
};

const statusOf = (s) => STATUS[s] || { label: s || '—', tone: 'neutral' };

function When({ row }) {
  const away =
    row.days_away === 0
      ? 'today'
      : row.days_away === 1
        ? 'tomorrow'
        : row.days_away < 0
          ? `${Math.abs(row.days_away)} days ago`
          : `in ${row.days_away} days`;
  return (
    <>
      <span className="rec-title">
        {row.when_label || row.on_date} · {row.start_time}–{row.end_time}
      </span>
      <span className="rec-meta">{away}</span>
    </>
  );
}

export default function InterviewsView({ tenant, people, onError }) {
  const [data, loading, reload] = useLoad(() => api.interviews(tenant), [tenant], onError);
  const [panel, setPanel] = useState([]);
  const [stage, setStage] = useState('');
  const [on, setOn] = useState('');
  const [found, setFound] = useState(null);
  const [busy, run] = useBusy(onError);

  const settings = data?.settings;
  const stages = settings?.stages || [];
  const rows = data?.items || [];
  const roleLabel = (id) => data?.roles?.[id] || id;

  const check = () =>
    run('check', async () => {
      setFound(
        await api.availability(tenant, {
          panel: panel.join(','),
          stage: stage || undefined,
          on: on || undefined,
        }),
      );
    });

  const toggle = (id) =>
    setPanel((current) => (current.includes(id) ? current.filter((p) => p !== id) : [...current, id]));

  return (
    <div className="layout">
      <aside className="side">
        <section className="card">
          <h2>This company's scheduling rules</h2>
          <p className="hint" style={{ marginTop: 0 }}>
            Every number here is configuration, not code. The agent does no working-time
            arithmetic: the notice period is this company's courtesy to a candidate, not a legal
            calculation.
          </p>
          {settings && (
            <dl className="defs">
              <dt>Notice to a candidate</dt>
              <dd>at least {settings.min_notice_hours} hours</dd>
              <dt>Clear either side</dt>
              <dd>{settings.buffer_minutes} minutes</dd>
              <dt>Most per interviewer, per day</dt>
              <dd>{settings.max_per_day_per_interviewer}</dd>
              <dt>Working hours</dt>
              <dd>
                {data.working_hours.start}–{data.working_hours.end} {data.timezone}
              </dd>
              <dt>Working days</dt>
              <dd>{data.working_hours.days.length} a week</dd>
              <dt>Tells the candidate</dt>
              <dd>{settings.notify_candidate ? 'yes, with a calendar invitation' : 'no'}</dd>
            </dl>
          )}
          {stages.length > 0 && (
            <>
              <h3>Stages</h3>
              {stages.map((s) => (
                <p key={s.id} className="hint hint-tight">
                  <strong>{s.label}</strong> — {s.duration_minutes} min, at least {s.panel_min}{' '}
                  interviewer{s.panel_min === 1 ? '' : 's'}
                  {s.panel_roles?.length
                    ? `, including a ${s.panel_roles.map(roleLabel).join(' and a ')}`
                    : ''}
                </p>
              ))}
            </>
          )}
        </section>

        <section className="card">
          <h2>When is a panel free?</h2>
          <p className="hint" style={{ marginTop: 0 }}>
            Reads free/busy and applies the rules above. Books nothing and tells nobody.
          </p>
          <p className="field-label">Interviewers</p>
          <div className="fields" style={{ maxHeight: 190, overflowY: 'auto' }}>
            {people.map((p) => (
              <label key={p.id} className="hint hint-tight" style={{ display: 'block' }}>
                <input type="checkbox" checked={panel.includes(p.id)} onChange={() => toggle(p.id)} />{' '}
                {p.name}
              </label>
            ))}
          </div>
          <p className="field-label" style={{ marginTop: 12 }}>
            <label htmlFor="stage">Kind of interview</label>
          </p>
          <select id="stage" value={stage} onChange={(e) => setStage(e.target.value)}>
            <option value="">Default length</option>
            {stages.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label} ({s.duration_minutes} min)
              </option>
            ))}
          </select>
          <p className="field-label" style={{ marginTop: 12 }}>
            <label htmlFor="on">A particular day (optional)</label>
          </p>
          <input id="on" type="date" value={on} onChange={(e) => setOn(e.target.value)} />
          <button
            type="button"
            className="btn btn-primary btn-block"
            style={{ marginTop: 12 }}
            onClick={check}
            disabled={Boolean(busy) || panel.length === 0}
          >
            {busy === 'check' ? 'Reading calendars…' : 'Find a time'}
          </button>
          {panel.length === 0 && (
            <p className="hint hint-tight">Pick at least one interviewer — there is no calendar to read otherwise.</p>
          )}

          {found && (
            <>
              <h3 style={{ marginTop: 16 }}>
                {found.proposal.slots.length > 0 ? 'Everyone is free' : 'No time works'}
              </h3>
              {found.proposal.slots.map((s) => (
                <p key={`${s.date}${s.start}`} className="hint hint-tight">
                  <strong>
                    {s.label || s.date} · {s.start}–{s.end}
                  </strong>
                </p>
              ))}
              {found.proposal.rejected.length > 0 && (
                <>
                  <h3>Ruled out</h3>
                  {found.proposal.rejected.map((r) => (
                    <p key={r.when + r.why} className="hint hint-tight">
                      <strong>{r.label}</strong> — {r.why}
                    </p>
                  ))}
                </>
              )}
              <p className="hint">
                Read from <code>{found.calendar}</code>. Nothing was booked and nobody was told.
              </p>
            </>
          )}
        </section>
      </aside>

      <main className="main">
        <section className="panel">
          <div className="panel-head">
            <h2>Interviews booked</h2>
            <button type="button" className="btn btn-outline" onClick={reload}>
              Refresh
            </button>
          </div>
          <p className="hint" style={{ marginTop: 0 }}>
            An interview appears here only once a person approved it. Until then it is a proposal in
            the approvals queue, and nothing is in anyone's calendar.
          </p>

          {loading && <p className="empty-list">Loading…</p>}
          {!loading && rows.length === 0 && (
            <p className="empty-list">
              Nothing is booked from today onwards. Ask the agent to arrange one on the Ask screen —
              it will propose times and wait for an approval.
            </p>
          )}

          <ul className="rec-list">
            {rows.map((row) => {
              const panelists = (row.attendees || []).filter((a) => a.part !== 'candidate');
              const candidate = (row.attendees || []).find((a) => a.part === 'candidate');
              const status = statusOf(row.status);
              return (
                <li key={row.id}>
                  <div className="rec-item" style={{ cursor: 'default' }}>
                    <span className="rec-top">
                      <When row={row} />
                    </span>
                    <span className="rec-bottom">
                      <span className={`status status-${status.tone}`}>{status.label}</span>
                      <span className="rec-meta">
                        {row.stage_label}
                        {row.role_title ? ` · ${row.role_title}` : ''}
                      </span>
                    </span>
                    <span className="rec-bottom">
                      <span className="rec-meta">
                        Candidate: {candidate?.name || row.candidate_name || 'not recorded'}
                        {candidate?.email || row.candidate_email ? '' : ' (no address on file)'}
                      </span>
                    </span>
                    <span className="rec-bottom">
                      <span className="rec-meta">
                        {panelists.length > 0
                          ? `Panel: ${panelists.map((a) => a.name || a.person_id).join(', ')}`
                          : 'No interviewer recorded'}
                      </span>
                    </span>
                    <span className="rec-bottom">
                      <span className="rec-meta">
                        {row.location || 'Location not set'} · {row.timezone}
                      </span>
                      {row.calendar_ref && <span className="tag tag-mono">{row.calendar_ref}</span>}
                    </span>
                    {row.supersedes && (
                      <span className="rec-bottom">
                        <span className="rec-meta">Replaced an earlier time ({row.supersedes})</span>
                      </span>
                    )}
                    {row.status === 'cancelled' && row.cancel_reason && (
                      <span className="rec-bottom">
                        <span className="rec-meta">Cancelled: {row.cancel_reason}</span>
                      </span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      </main>
    </div>
  );
}
