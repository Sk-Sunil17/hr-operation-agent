import { useCallback, useEffect, useState } from 'react';
import { api, session, SIGNED_OUT } from './api.js';
import { useBusy } from './shared.jsx';
import LoginView from './views/Login.jsx';
import AskView from './views/Ask.jsx';
import QueueView from './views/Queue.jsx';
import ItemsView from './views/Items.jsx';
import InterviewsView from './views/Interviews.jsx';
import RulesView from './views/Rules.jsx';
import PoliciesView from './views/Policies.jsx';
import ToolsView from './views/Tools.jsx';
import AuditView from './views/Audit.jsx';
import MetricsView from './views/Metrics.jsx';
import PackView from './views/Pack.jsx';

const TABS = [
  ['ask', 'Ask', AskView],
  ['queue', 'Approvals', QueueView],
  ['items', 'Work items', ItemsView],
  ['interviews', 'Interviews', InterviewsView],
  ['rules', 'Rules', RulesView],
  ['policies', 'Policies', PoliciesView],
  ['tools', 'Systems', ToolsView],
  ['audit', 'Audit', AuditView],
  ['metrics', 'Measurement', MetricsView],
  ['pack', 'Configuration', PackView],
];

export default function App() {
  const [health, setHealth] = useState(null);
  const [unreachable, setUnreachable] = useState(false);
  const [tenants, setTenants] = useState([]);
  const [tenant, setTenant] = useState(null);
  const [people, setPeople] = useState([]);
  const [actor, setActor] = useState(null);
  const [tab, setTab] = useState('ask');
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [queueCount, setQueueCount] = useState(0);
  const [version, setVersion] = useState(0);
  const [, run] = useBusy(setError);

  // With authentication on there is no role switcher: the session decides who
  // is acting, and in which company.
  const [signedIn, setSignedIn] = useState(() => {
    const saved = session.get();
    return saved && saved.expires_at > Date.now() ? saved : null;
  });
  const [signedOutReason, setSignedOutReason] = useState(null);
  const authOn = Boolean(health?.auth?.enabled);

  const bump = useCallback(() => setVersion((v) => v + 1), []);

  const signOut = useCallback((reason = null) => {
    session.clear();
    setSignedIn(null);
    setActor(null);
    setPeople([]);
    setSignedOutReason(reason);
  }, []);

  useEffect(() => {
    const onSignedOut = (event) => signOut(event.detail || 'Your session ended. Sign in again.');
    window.addEventListener(SIGNED_OUT, onSignedOut);
    return () => window.removeEventListener(SIGNED_OUT, onSignedOut);
  }, [signOut]);

  useEffect(() => {
    Promise.all([api.health(), api.tenants()])
      .then(([h, t]) => {
        setHealth(h);
        setTenants(t.items);
        setTenant((current) => current || t.items[0]?.id || null);
      })
      .catch((err) => {
        setError(err.message);
        setUnreachable(true);
      });
  }, []);

  useEffect(() => {
    if (authOn && signedIn) setTenant(signedIn.tenant);
  }, [authOn, signedIn]);

  // Switching company replaces the directory, so the signed-in person changes too.
  useEffect(() => {
    if (!tenant || !health) return;
    if (authOn && (!signedIn || signedIn.tenant !== tenant)) return;
    let cancelled = false;
    Promise.all([api.people(tenant), authOn ? api.me(tenant) : null])
      .then(([data, who]) => {
        if (cancelled) return;
        setPeople(data.items);
        if (authOn) {
          if (!who?.authenticated) {
            signOut('Your session ended. Sign in again.');
            return;
          }
          setActor(who.id);
          return;
        }
        // Prefer somebody the company's approvals actually route to, then any
        // HR role, then anyone. Picking the first HR-role holder alone landed
        // on a person with an empty queue next to a list of six waiting items,
        // which looks like a fault rather than the access rule it is.
        const routed = data.items.find((p) => p.roles.includes(data.default_role));
        const hr = data.items.find((p) => p.is_hr);
        setActor((routed || hr || data.items[0])?.id || null);
      })
      .catch((err) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, [tenant, health, authOn, signedIn, signOut]);

  useEffect(() => {
    if (!tenant || !actor) return;
    api
      .queue(tenant, actor)
      .then((data) => setQueueCount(data.mine))
      .catch(() => setQueueCount(0));
  }, [tenant, actor, version]);

  const company = tenants.find((t) => t.id === tenant);
  const me = people.find((p) => p.id === actor);
  const View = (TABS.find(([id]) => id === tab) || TABS[0])[2];
  const showLogin = authOn && !signedIn;

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-row">
          <div className="brand">
            <span className="brand-name">HR Operations Agent</span>
            <span className="brand-sub">
              {showLogin ? 'sign in to continue' : company ? `${company.name} · ${company.config_hash}` : 'loading…'}
            </span>
          </div>
          <div className="topbar-right">
            {health && (
              <>
                <span className="pill">{health.guardrails.model_provider}</span>
                <span className={`pill ${health.guardrails.redaction ? 'pill-gold' : 'pill-warn'}`}>
                  {health.guardrails.redaction ? 'redaction on' : 'REDACTION OFF'}
                </span>
              </>
            )}
            {authOn ? (
              signedIn &&
              me && (
                <>
                  <span className="signed-in">
                    {me.name}
                    {me.role_labels.length ? ` — ${me.role_labels.join(', ')}` : ''}
                  </span>
                  <button type="button" className="btn btn-small btn-on-navy" onClick={() => signOut()}>
                    Sign out
                  </button>
                </>
              )
            ) : (
              <>
                <span className="switcher">
                  <label htmlFor="tenant">Company</label>
                  <select id="tenant" value={tenant || ''} onChange={(e) => setTenant(e.target.value)}>
                    {tenants.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                      </option>
                    ))}
                  </select>
                </span>
                <span className="switcher">
                  <label htmlFor="actor">Acting as</label>
                  <select id="actor" value={actor || ''} onChange={(e) => setActor(e.target.value)}>
                    {people.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                        {p.role_labels.length ? ` — ${p.role_labels.join(', ')}` : ''}
                      </option>
                    ))}
                  </select>
                </span>
              </>
            )}
          </div>
        </div>
        {!showLogin && (
          <nav className="mainnav" aria-label="Screens">
            {TABS.map(([id, label]) => (
              <button
                key={id}
                type="button"
                className="mainnav-item"
                aria-current={tab === id ? 'page' : undefined}
                onClick={() => setTab(id)}
              >
                {label}
                {id === 'queue' && queueCount > 0 && <span className="mainnav-count">{queueCount}</span>}
              </button>
            ))}
          </nav>
        )}
      </header>

      {health?.demo?.public && !authOn && (
        <div className="banner" role="status">
          <span>
            <strong>Public demo.</strong> Three fictional companies, invented people, invented
            policies. Anyone with this link can act as anyone in the directory and approve things —
            that is the point of the demo, and it is why no real data is here. Without a persistent
            disk, state resets when the server restarts.
          </span>
        </div>
      )}
      {error && (
        <div className="banner" role="alert">
          <span>{error}</span>
          <button type="button" className="banner-close" onClick={() => setError(null)}>
            Dismiss
          </button>
        </div>
      )}
      {notice && !error && (
        <div className="banner banner-ok" role="status">
          <span>{notice}</span>
          <button type="button" className="banner-close" onClick={() => setNotice(null)}>
            Dismiss
          </button>
        </div>
      )}

      {showLogin ? (
        <LoginView
          tenants={tenants}
          reason={signedOutReason}
          onSignedIn={(signed) => {
            setSignedOutReason(null);
            setError(null);
            setSignedIn(signed);
          }}
        />
      ) : tenant && actor ? (
        <View
          // Remount when the screen or the company changes, and ONLY then. The
          // version counter refreshes the queue badge above; putting it in this
          // key would remount the view on every action and discard its result.
          key={`${tab}-${tenant}`}
          tenant={tenant}
          company={company}
          actor={actor}
          me={me}
          people={people}
          health={health}
          onError={setError}
          onNotice={setNotice}
          onChanged={bump}
          goTo={setTab}
        />
      ) : (
        <div className="layout layout-wide">
          <div className="blank">
            {unreachable ? (
              <>
                <h1>The agent is not answering</h1>
                <p>
                  If it is hosted on a free tier it sleeps when idle and takes about 30 seconds to
                  wake — wait, then reload.
                </p>
                <p>
                  Locally, start it with <code>python3 -m hr_agent.cli serve</code> and open{' '}
                  <code>http://127.0.0.1:4000</code>. If you have not loaded the companies yet, run{' '}
                  <code>python3 -m hr_agent.cli init</code> first.
                </p>
              </>
            ) : (
              <p>Loading…</p>
            )}
          </div>
        </div>
      )}

      <p className="footer">
        {authOn
          ? 'Signed-in build. What you can see and decide comes from the roles your company directory gives you. '
          : health?.demo?.public
            ? 'Public demo build. Anyone can act as anyone from the company directory, and only fictional data is loaded. '
            : 'Demo build. Anyone can act as anyone from the company directory, which is why the agent refuses to listen on anything but localhost. '}
        No model is called: replies are templates wrapped around policy text quoted verbatim.
      </p>
    </div>
  );
}
