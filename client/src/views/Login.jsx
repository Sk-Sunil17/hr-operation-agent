// Sign-in, shown instead of everything else when the server has authentication
// on. There is no role switcher in this mode: who you are decides what you see.

import { useState } from 'react';
import { api, session } from '../api.js';

export default function LoginView({ tenants, onSignedIn, reason }) {
  const [tenant, setTenant] = useState(tenants[0]?.id || '');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { token, expires_in: expiresIn } = await api.login(tenant, email, password);
      const signed = { tenant, token, expires_at: Date.now() + expiresIn * 1000 };
      session.set(signed);
      setPassword('');
      onSignedIn(signed);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="layout layout-wide">
      <section className="card panel login">
        <h1>Sign in</h1>
        {reason && <p className="hint" role="status">{reason}</p>}
        <form onSubmit={submit}>
          <label className="field-label" htmlFor="login-tenant">
            Company
          </label>
          <select id="login-tenant" value={tenant} onChange={(e) => setTenant(e.target.value)} required>
            {tenants.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>

          <label className="field-label" htmlFor="login-email">
            Email
          </label>
          <input
            id="login-email"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />

          <label className="field-label" htmlFor="login-password">
            Password
          </label>
          <input
            id="login-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />

          {error && (
            <p className="login-error" role="alert">
              {error}
            </p>
          )}
          <button type="submit" className="btn btn-primary btn-block" disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
        <p className="hint">
          Accounts are created by an administrator with <code>hrctl account</code>. What you may do
          comes from the roles your company directory gives you.
        </p>
      </section>
    </div>
  );
}
