// The main screen: ask HR something and see what happens to it.

import { useState } from 'react';
import { api } from '../api.js';
import { useBusy, useLoad } from '../shared.jsx';
import { KIND, areaOf } from '../format.js';
import RequestSummary from './Request.jsx';

const SHOWN = 6;

const CAPABILITIES = [
  ['questions', 'Policy questions', 'Answers quoting your own policy, word for word.'],
  ['records', 'Record changes', 'Prepares the change; HR approves before anything is written.'],
  ['documents', 'Documents', 'Reads forms against your templates and names what is missing.'],
  ['joiners', 'Onboarding', 'Checks a new starter is ready and lists the gaps.'],
  ['interviews', 'Interviews', 'Finds slots within your scheduling rules.'],
  ['safeguards', 'Sensitive matters', 'Goes straight to a person. Never answered by the agent.'],
];

export default function AskView({ tenant, actor, me, people, onError, onChanged }) {
  const [message, setMessage] = useState('');
  const [attachment, setAttachment] = useState('');
  const [attaching, setAttaching] = useState(false);
  const [result, setResult] = useState(null);
  const [showAll, setShowAll] = useState(false);
  const [busy, run] = useBusy(onError);
  const [cases] = useLoad(() => api.goldenCases(tenant), [tenant], onError);

  const examples = cases?.items || [];
  const firstName = (me?.name || '').split(' ')[0];

  const send = () => {
    if (!message.trim()) return;
    const attached = attaching && attachment.trim() ? { text: attachment } : undefined;
    run('ask', async () => {
      setResult(await api.chat(tenant, { message, requester: actor, attachment: attached }));
      onChanged();
    });
  };

  const useExample = (example) => {
    setMessage(example.message);
    setAttachment(example.attachment?.text || '');
    setAttaching(Boolean(example.attachment?.text));
    setResult(null);
  };

  const exampleFor = (area) => examples.find((c) => areaOf(c.expect_rule) === area);

  return (
    <div className="layout">
      <aside className="side">
        <section className="card">
          <h2>Ask HR</h2>
          <textarea
            id="message"
            aria-label="Your message"
            rows={5}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send();
            }}
            placeholder="How many days of annual leave do I get in a year?"
          />
          <label className="check-line">
            <input type="checkbox" checked={attaching} onChange={(e) => setAttaching(e.target.checked)} />
            Attach a document
          </label>
          {attaching && (
            <textarea
              id="attachment"
              aria-label="Document text"
              rows={7}
              value={attachment}
              onChange={(e) => setAttachment(e.target.value)}
              placeholder={'NEW STARTER FORM\nFull name: Lena Fischer\nStart date: 2026-11-24\nPersonal email: lena.fischer@example.test'}
            />
          )}
          <button type="button" className="btn btn-primary btn-block" onClick={send} disabled={Boolean(busy) || !message.trim()}>
            {busy === 'ask' ? 'Working on it…' : 'Send'}
          </button>
          <p className="hint">Sending as {me?.name || actor}.</p>
        </section>

        {examples.length > 0 && (
          <section className="card">
            <h2>Try an example</h2>
            <ul className="examples">
              {(showAll ? examples : examples.slice(0, SHOWN)).map((c) => (
                <li key={c.id}>
                  <button type="button" className="example" onClick={() => useExample(c)}>
                    <span className="example-kind">{KIND[areaOf(c.expect_rule)]}</span>
                    <span className="example-text">{c.message}</span>
                  </button>
                </li>
              ))}
            </ul>
            {examples.length > SHOWN && (
              <button type="button" className="link-btn" onClick={() => setShowAll((v) => !v)}>
                {showAll ? 'Show fewer' : `Show all ${examples.length}`}
              </button>
            )}
          </section>
        )}
      </aside>

      <main className="main">
        {result ? (
          <RequestSummary data={result} people={people} fresh />
        ) : (
          <div className="welcome">
            <h1>{firstName ? `Hi ${firstName}, what can HR help with?` : 'What can HR help with?'}</h1>
            <p className="welcome-sub">
              Answers come from your company's own policies. Anything that changes a record, or needs
              judgement, goes to a person before it happens.
            </p>
            <div className="capabilities">
              {CAPABILITIES.map(([area, title, blurb]) => {
                const example = exampleFor(area);
                return (
                  <button
                    key={area}
                    type="button"
                    className="capability"
                    disabled={!example}
                    onClick={() => example && useExample(example)}
                  >
                    <span className="capability-title">{title}</span>
                    <span className="capability-text">{blurb}</span>
                    {example && <span className="capability-try">Try: “{example.message}”</span>}
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
