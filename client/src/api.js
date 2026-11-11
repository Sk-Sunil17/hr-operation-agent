// Thin wrapper around the agent's JSON API. Every function returns parsed JSON
// or throws an Error whose message is safe to show to the user.
const BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/+$/, '');

const UNREACHABLE =
  'Cannot reach the agent. Start it with `python3 -m hr_agent.cli serve` (it listens on 127.0.0.1:4000).';

async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(`${BASE}/api${path}`, {
      ...options,
      headers: { 'X-HRA-Request': '1', ...(options.headers || {}) },
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
  tenants: () => request('/tenants'),
  people: (tenant) => request(`/people${query({ tenant })}`),
  config: (tenant) => request(`/config${query({ tenant })}`),
  rules: (tenant) => request(`/rules${query({ tenant })}`),
  policies: (tenant) => request(`/policies${query({ tenant })}`),
  policySearch: (tenant, q, asPerson) => request(`/policies/search${query({ tenant, q, as_person: asPerson })}`),
  tools: (tenant) => request(`/tools${query({ tenant })}`),
  redaction: (tenant) => request(`/redaction${query({ tenant })}`),
  tryRedaction: (tenant, text) => request(`/redaction/try${query({ tenant })}`, json({ text })),

  explain: (tenant, body) => request(`/explain${query({ tenant })}`, json(body)),
  chat: (tenant, body) => request(`/chat${query({ tenant })}`, json(body)),
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
