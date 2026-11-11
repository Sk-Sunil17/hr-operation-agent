// Labels and tones. Nothing here decides anything; the agent does that.

export const MODE = {
  auto: { label: 'Answered alone', tone: 'ok', short: 'auto' },
  prepare: { label: 'Waiting for a person', tone: 'wait', short: 'prepare' },
  hand_over: { label: 'A person has it', tone: 'wait', short: 'hand over' },
  refuse: { label: 'Declined', tone: 'err', short: 'refuse' },
  none: { label: '—', tone: 'neutral', short: '—' },
};

export const STATUS = {
  answered: { label: 'Answered', tone: 'ok' },
  queued: { label: 'Waiting for approval', tone: 'wait' },
  handed_over: { label: 'With a person', tone: 'wait' },
  refused: { label: 'Declined', tone: 'err' },
  suppressed: { label: 'Duplicate, ignored', tone: 'neutral' },
  split: { label: 'Split into parts', tone: 'neutral' },
  open: { label: 'Open', tone: 'neutral' },
  failed: { label: 'Could not process', tone: 'err' },
};

export const SENSITIVITY = {
  routine: { label: 'Routine', tone: 'ok' },
  personal: { label: 'Personal', tone: 'wait' },
  restricted: { label: 'Restricted', tone: 'err' },
};

export const statusOf = (s) => STATUS[s] || { label: s || '—', tone: 'neutral' };
export const modeOf = (m) => MODE[m] || { label: m || '—', tone: 'neutral', short: m };

export const when = (iso) =>
  iso ? new Date(iso).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '';

export const clock = (iso) => (iso ? new Date(iso).toLocaleTimeString('en-GB', { hour12: false }) : '');

export const pct = (n) => (n === null || n === undefined ? '—' : `${Math.round(n * 100)}%`);

export const minutes = (n) => {
  if (n === null || n === undefined) return '—';
  if (n < 60) return `${n} min`;
  const h = Math.floor(n / 60);
  const m = n % 60;
  return m ? `${h}h ${m}m` : `${h}h`;
};

// `{"intent":"enquiry","sensitivity":"routine"}` -> `intent = enquiry, sensitivity = routine`
export const readable = (obj) =>
  Object.keys(obj || {}).length === 0
    ? 'everything'
    : Object.entries(obj)
        .map(([k, v]) => `${k} = ${Array.isArray(v) ? v.join(' or ') : String(v)}`)
        .join(', ');
