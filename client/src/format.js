// Labels and tones. Nothing here decides anything; the agent does that.

// `label` is the instruction HR gives the agent; `short` is the badge.
export const MODE = {
  auto: {
    label: 'Answer automatically',
    tone: 'ok',
    short: 'Automatic',
    blurb: 'The agent replies straight away, quoting the policy.',
  },
  prepare: {
    label: 'Draft for approval',
    tone: 'wait',
    short: 'Needs approval',
    blurb: 'The agent prepares the reply or change; an HR person approves it first.',
  },
  hand_over: {
    label: 'Send to HR',
    tone: 'wait',
    short: 'To HR',
    blurb: 'A person handles it from the start. The agent only acknowledges.',
  },
  refuse: {
    label: 'Decline',
    tone: 'err',
    short: 'Declined',
    blurb: 'The agent declines politely and says why.',
  },
  none: { label: '—', tone: 'neutral', short: '—', blurb: '' },
};
export const MODES = ['auto', 'prepare', 'hand_over', 'refuse'];

// Rule ids are shared by every company's rulebook; these are what HR calls them.
export const RULE_TITLES = {
  T1: 'A person has taken the case over',
  R1: 'Sensitive or restricted matters',
  P1: 'Requests to change a policy',
  W1: "Asking about someone else's record",
  E0: 'Two versions of a policy in force',
  E4: 'Unclear requests',
  E1: 'Routine policy questions',
  E2: 'Questions about personal circumstances',
  E3: 'Questions no policy covers',
  A1: 'Changes to your own record',
  A2: 'Changes to a field no system owns',
  A3: "A manager changing a report's record",
  A4: "HR changing someone's record",
  D0: 'Attachment mentioned but missing',
  D1: 'Restricted documents',
  D2: 'Unrecognised documents',
  D3: 'Incomplete forms',
  D4: 'Complete forms ready to file',
  O1: 'New starter readiness checks',
  O2: 'Readiness check requested too early',
  X1: 'Leaving, with no exit case open',
  X2: 'Leaver checklist',
  I0: 'Interviews with no calendar connected',
  I1: 'Booking an interview',
  I2: 'Moving an interview',
  I3: 'No interview slot available',
  I4: 'Cancelling an interview',
  S1: 'Status of your own request',
  J1: 'Scheduled checks',
  Z1: 'Anything else',
};

export const AREAS = [
  ['questions', 'Policy questions', /^E/],
  ['records', 'Employee records', /^[AS]/],
  ['documents', 'Documents', /^D/],
  ['joiners', 'Joiners & leavers', /^[OX]/],
  ['interviews', 'Interviews', /^I/],
  ['safeguards', 'Safeguards', /./],
];

export const areaOf = (ruleId) => (AREAS.find(([, , re]) => re.test(ruleId || '')) || AREAS[AREAS.length - 1])[0];
export const ruleTitle = (ruleId) => RULE_TITLES[ruleId] || ruleId;

// What kind of request an example is, from the rule it is meant to reach.
export const KIND = {
  questions: 'Policy question',
  records: 'Record change',
  documents: 'Document',
  joiners: 'Onboarding',
  interviews: 'Interview',
  safeguards: 'Sensitive',
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
