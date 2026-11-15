// Thin wrapper around the agent's JSON API. Every function returns parsed JSON
// or throws an Error whose message is safe to show to the user.
//
// The server that hands out this dashboard also answers /api, so every call is
// same-origin. Under `npm run dev`, Vite forwards /api to the server on 4000.
// VITE_API_BASE_URL exists only for the rare dashboard hosted somewhere else.
const RAW = (import.meta.env.VITE_API_BASE_URL || '').trim().replace(/\/+$/, '');
const BASE = RAW && !/^https?:\/\//i.test(RAW) ? `https://${RAW}` : RAW;

const UNREACHABLE =
  'Cannot reach the agent. If it is hosted on a free tier it may be waking up — wait about 30 ' +
  'seconds and reload. Locally, start it with `python3 -m hr_agent.cli serve`.';

// The signed-in session, when the server has authentication on. Kept for the
// browser tab only: closing the tab signs out, which is the right default for
// a screen showing other people's HR records.
const SESSION_KEY = 'hra.session';
export const SIGNED_OUT = 'hra:signed-out';

export const session = {
  get() {
    try {
      return JSON.parse(sessionStorage.getItem(SESSION_KEY)) || null;
    } catch {
      return null;
    }
  },
  set(value) {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(value));
  },
  clear() {
    sessionStorage.removeItem(SESSION_KEY);
  },
};

async function request(path, options = {}) {
  const current = session.get();
  let res;
  try {
    res = await fetch(`${BASE}/api${path}`, {
      ...options,
      headers: {
        'X-HRA-Request': '1',
        ...(current?.token ? { Authorization: `Bearer ${current.token}` } : {}),
        ...(options.headers || {}),
      },
    });
  } catch {
    throw new Error(UNREACHABLE);
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* not JSON: handled below */
  }
  if (res.status === 401 && current?.token) {
    // Expired, or signed by a server that has since restarted with a new key.
    session.clear();
    window.dispatchEvent(new CustomEvent(SIGNED_OUT, { detail: data?.error || data?.detail }));
  }
  if (!res.ok) {
    throw new Error(data?.error || data?.detail || (res.status >= 500 ? UNREACHABLE : `The request failed (${res.status}).`));
  }
  return data;
}

const query = (params = {}) => {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ''));
  return q.toString() ? `?${q}` : '';
};

const json = (body, method = 'POST') => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body ?? {}),
});

export const api = {
  health: () => request('/health'),
  login: (tenant, email, password) => request(`/auth/login${query({ tenant })}`, json({ email, password })),
  me: (tenant) => request(`/auth/me${query({ tenant })}`),
  tenants: () => request('/tenants'),
  people: (tenant) => request(`/people${query({ tenant })}`),
  config: (tenant) => request(`/config${query({ tenant })}`),
  rules: (tenant, actor) => request(`/rules${query({ tenant, actor })}`),
  changeRule: (tenant, ruleId, body) => request(`/rules/${ruleId}${query({ tenant })}`, json(body)),
  previewRule: (tenant, ruleId, mode) => request(`/rules/${ruleId}/preview${query({ tenant })}`, json({ mode })),
  policies: (tenant) => request(`/policies${query({ tenant })}`),
  policyText: (tenant, id, version) =>
    request(`/policies/${encodeURIComponent(id)}/${encodeURIComponent(version)}${query({ tenant })}`),
  policySearch: (tenant, q, asPerson) => request(`/policies/search${query({ tenant, q, as_person: asPerson })}`),
  tools: (tenant) => request(`/tools${query({ tenant })}`),
  integrations: (tenant) => request(`/integrations${query({ tenant })}`),
  redaction: (tenant) => request(`/redaction${query({ tenant })}`),
  tryRedaction: (tenant, text) => request(`/redaction/try${query({ tenant })}`, json({ text })),

  explain: (tenant, body) => request(`/explain${query({ tenant })}`, json(body)),
  chat: (tenant, body) => request(`/chat${query({ tenant })}`, json(body)),
  exitCases: (tenant) => request(`/cases/exit${query({ tenant })}`),
  openExit: (tenant, body) => request(`/cases/exit${query({ tenant })}`, json(body)),
  closeExit: (tenant, caseId, body) => request(`/cases/exit/${caseId}/close${query({ tenant })}`, json(body)),
  workItems: (tenant, params) => request(`/work-items${query({ tenant, ...params })}`),
  workItem: (tenant, id) => request(`/work-items/${id}${query({ tenant })}`),

  queue: (tenant, actor) => request(`/queue${query({ tenant, actor })}`),
  reasonCodes: () => request('/reason-codes'),
  decide: (tenant, approvalId, body) => request(`/approvals/${approvalId}/decide${query({ tenant })}`, json(body)),

  interviews: (tenant, params) => request(`/interviews${query({ tenant, ...params })}`),
  availability: (tenant, params) => request(`/interviews/availability${query({ tenant, ...params })}`),
  runJob: (tenant, job, on) => request(`/jobs/${job}${query({ tenant, on })}`, json({})),

  audit: (tenant, workItem) => request(`/audit${query({ tenant, work_item: workItem })}`),
  metrics: (tenant) => request(`/metrics${query({ tenant })}`),
  goldenCases: (tenant) => request(`/golden-cases${query({ tenant })}`),
  simulate: (tenant, on) => request(`/simulate${query({ tenant, on })}`, json({})),
};
