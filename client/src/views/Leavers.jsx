// Starting a leaver. The agent never starts an exit: HR opens the case here,
// and only then will the agent prepare the company's departure checklist.

import { useState } from 'react';
import { api } from '../api.js';
import { useBusy, useLoad } from '../shared.jsx';
import { when } from '../format.js';

export default function Leavers({ tenant, actor, people, onError, onNotice, onChecklist }) {
  const [cases, , reload] = useLoad(() => api.exitCases(tenant), [tenant], onError);
  const [adding, setAdding] = useState(false);
  const [subject, setSubject] = useState('');
  const [lastDay, setLastDay] = useState('');
  const [note, setNote] = useState('');
  const [busy, run] = useBusy(onError);

  const open = cases?.items || [];
  const leaving = new Set(open.map((c) => c.subject_id));
  const candidates = people.filter((p) => p.id !== actor && !leaving.has(p.id));

  const start = () =>
    run('open', async () => {
      const opened = await api.openExit(tenant, { actor, subject, last_day: lastDay || null, note });
      setAdding(false);
      setSubject('');
      setLastDay('');
      setNote('');
      reload();
      onNotice(`Exit case opened for ${opened.subject_name}. Prepare the checklist when you are ready.`);
    });

  const checklist = (c) =>
    run(`list-${c.id}`, async () => {
      await onChecklist(`${c.subject_name} is leaving, what happens next?`);
    });

  const close = (c) =>
    run(`close-${c.id}`, async () => {
      await api.closeExit(tenant, c.id, { actor, note: 'Departure complete' });
      reload();
      onNotice(`Exit case for ${c.subject_name} closed.`);
    });

  return (
    <section className="card">
      <div className="panel-head">
        <h2>Leavers</h2>
        {!adding && (
          <button type="button" className="btn btn-small btn-outline" onClick={() => setAdding(true)}>
            Start a leaver
          </button>
        )}
      </div>

      {adding && (
        <div className="leaver-form">
          <label className="field-label" htmlFor="leaver-person">
            Who is leaving?
          </label>
          <select id="leaver-person" value={subject} onChange={(e) => setSubject(e.target.value)}>
            <option value="">Choose a person</option>
            {candidates.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <label className="field-label" htmlFor="leaver-day">
            Last day
          </label>
          <input id="leaver-day" type="date" value={lastDay} onChange={(e) => setLastDay(e.target.value)} />
          <label className="field-label" htmlFor="leaver-note">
            Reason
          </label>
          <input
            id="leaver-note"
            type="text"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="e.g. Resigned, notice received today"
          />
          <div className="decision-actions">
            <button
              type="button"
              className="btn btn-primary btn-small"
              disabled={Boolean(busy) || !subject || note.trim().length < 5}
              onClick={start}
            >
              {busy === 'open' ? 'Opening…' : 'Open exit case'}
            </button>
            <button type="button" className="btn btn-outline btn-small" onClick={() => setAdding(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {open.length === 0 && !adding ? (
        <p className="empty-list">No one is leaving right now.</p>
      ) : (
        <ul className="leavers">
          {open.map((c) => (
            <li key={c.id}>
              <div>
                <strong>{c.subject_name}</strong>
                {c.last_day && (
                  <span className="rec-meta">
                    Last day{' '}
                    {new Date(`${c.last_day}T00:00:00`).toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' })}
                  </span>
                )}
                <span className="rec-meta">
                  Opened by {c.owner_name} · {when(c.opened_at)}
                </span>
              </div>
              <div className="leaver-actions">
                <button type="button" className="btn btn-small btn-primary" disabled={Boolean(busy)} onClick={() => checklist(c)}>
                  {busy === `list-${c.id}` ? 'Preparing…' : 'Prepare checklist'}
                </button>
                <button type="button" className="btn btn-small btn-outline" disabled={Boolean(busy)} onClick={() => close(c)}>
                  Close
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
