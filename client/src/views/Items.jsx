// Requests the agent has handled: everyone's for HR, your own for anyone else.

import { useState } from 'react';
import { api } from '../api.js';
import { RecordList, useLoad } from '../shared.jsx';
import { KIND, areaOf, statusOf, when } from '../format.js';
import RequestSummary from './Request.jsx';

const FILTERS = [
  ['all', 'All', null],
  ['queued', 'Waiting', 'queued'],
  ['answered', 'Answered', 'answered'],
  ['handed_over', 'With HR', 'handed_over'],
  ['refused', 'Declined', 'refused'],
];

export default function ItemsView({ tenant, actor, me, people, onError }) {
  const mine = !me?.is_hr;
  const [filter, setFilter] = useState('all');
  const [selected, setSelected] = useState(null);
  const [list, loading] = useLoad(
    () => api.workItems(tenant, mine ? { requester: actor } : {}),
    [tenant, actor, mine],
    onError,
  );
  const [detail] = useLoad(
    () => (selected ? api.workItem(tenant, selected) : Promise.resolve(null)),
    [tenant, selected],
    onError,
  );

  const all = list?.items || [];
  const wanted = FILTERS.find(([id]) => id === filter)?.[2];
  const counts = Object.fromEntries(
    FILTERS.map(([id, , status]) => [id, all.filter((i) => !status || i.status === status).length]),
  );

  const rows = all
    .filter((i) => !wanted || i.status === wanted)
    .map((i) => ({
      id: i.id,
      title: i.message || 'Request',
      status: statusOf(i.status),
      meta: `${mine ? KIND[areaOf(i.rule_id)] || '' : i.requester_name} · ${when(i.created_at)}`,
    }));

  return (
    <div className="layout">
      <aside className="side">
        <section className="card">
          <h2>{mine ? 'My requests' : 'Requests'}</h2>
          <div className="tabs" role="tablist">
            {FILTERS.map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="tab"
                className="tab"
                aria-selected={filter === id}
                onClick={() => setFilter(id)}
              >
                {label} <span className="count">{counts[id] ?? 0}</span>
              </button>
            ))}
          </div>
          <RecordList rows={rows} selectedId={selected} onSelect={setSelected} empty={loading ? 'Loading…' : 'Nothing here yet.'} />
        </section>
      </aside>

      <main className="main">
        {detail ? (
          <RequestSummary data={detail} people={people} withTrail={!mine} />
        ) : (
          <div className="blank">
            <h1>{mine ? 'Your requests' : 'Every request, in one place'}</h1>
            <p>
              Pick one to see the reply, the policy it quoted, and what is still waiting on a person.
            </p>
          </div>
        )}
      </main>
    </div>
  );
}
