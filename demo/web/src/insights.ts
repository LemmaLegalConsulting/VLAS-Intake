type Vote = {provider: string; code: string; confidence: number; weight: number};
type Classification = {status: string; description: string; code?: string; score?: number; votes: Vote[]; questions: string[]};
type Income = {rows: {member: string; source: string; amount: number; period: string; periods_per_year: number; monthly: number}[];
  total: number; household_size: number; annual_limit: number; monthly_limit: number; base: number; increment: number; year: number; multiplier: number; within_demo_limit: boolean};
export type Explanations = {classification: Classification | null; income: Income | null;
  conflict: {status: string; searched: string[]; matches: {name: string; record: string; matter: string; searched_name: string}[]}};
type Catalog = {counties: string[]; cities: string[]; example: Explanations};
const byId = (id: string) => document.getElementById(id)!;
const dollars = (n: number) => new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD', maximumFractionDigits: 2}).format(n);
const empty: Explanations = {classification: null, income: null, conflict: {status: 'waiting', searched: [], matches: []}};
let example = empty;
let current = empty;
let showingExample = true;
let lastRendered = '';

function add(parent: HTMLElement, tag: string, text: string, className = '') {
  const node = document.createElement(tag); node.textContent = text; node.className = className; parent.append(node); return node;
}
function table(parent: HTMLElement, headings: string[], rows: string[][]) {
  const wrap = add(parent, 'div', '', 'table-scroll'); const t = add(wrap, 'table', '');
  const head = add(add(t, 'thead', ''), 'tr', '');
  for (const label of headings) { const th = add(head, 'th', label); th.setAttribute('scope', 'col'); }
  const body = add(t, 'tbody', '');
  for (const values of rows) { const row = add(body, 'tr', ''); for (const value of values) add(row, 'td', value); }
}
function render() {
  const data = showingExample ? example : current;
  const signature = JSON.stringify([showingExample, data]);
  if (signature === lastRendered) return;
  lastRendered = signature;
  byId('example-view').setAttribute('aria-pressed', String(showingExample));
  byId('session-view').setAttribute('aria-pressed', String(!showingExample));
  byId('explanation-source').textContent = showingExample
    ? 'Worked fictional example — these details are not answers from your conversation.'
    : 'Your conversation — updates as information is recorded. Missing information stays blank.';
  const c = byId('classification-detail'); c.replaceChildren();
  const classification = data.classification;
  if (!classification) add(c, 'p', 'Waiting for your description of the legal problem.', 'empty-state');
  else {
    add(c, 'blockquote', classification.description);
    add(c, 'p', classification.code || 'Comparing the story with the problem-code taxonomy…', 'result-value');
    if (classification.status === 'example') {
      add(c, 'p', 'Private rental + landlord/tenant eviction dispute → 63 Private Landlord/Tenant. Subsidized housing or a discrimination claim may need a different code.');
      add(c, 'p', 'Illustrative mapping, not a live model result.', 'note');
    } else {
      add(c, 'p', 'Two language models and a keyword matcher compare the description with the repository’s LSC code list. Their weighted votes select the suggested code.');
      if (classification.votes?.length) table(c, ['Matcher', 'Suggested code', 'Score × weight'], classification.votes.map(v => [v.provider, v.code, `${v.confidence.toFixed(2)} × ${v.weight.toFixed(2)}`]));
      if (classification.score != null) add(c, 'p', `Combined score: ${classification.score.toFixed(2)}. Scores below 0.70 can trigger follow-up questions. This is a model-voting score, not a probability or legal conclusion.`, 'note');
      if (classification.status === 'needs_details') add(c, 'p', 'Provisional match — more detail is needed.', 'badge');
      for (const question of classification.questions || []) add(c, 'p', question);
    }
  }
  const i = byId('income-detail'); i.replaceChildren(); const income = data.income;
  if (!income) add(i, 'p', 'Waiting for household size and income amounts and frequencies.', 'empty-state');
  else {
    add(i, 'p', `${income.household_size} household members · convert each income source to a monthly amount.`);
    table(i, ['Person / source', 'Reported income', 'Monthly conversion'], income.rows.map(r => [
      `${r.member} · ${r.source}`, `${dollars(r.amount)} · ${r.period}`,
      `${dollars(r.amount)} × ${r.periods_per_year} ÷ 12 = ${dollars(r.monthly)}`,
    ]));
    add(i, 'p', `${dollars(income.total)} / month total`, 'result-value');
    add(i, 'p', `Demo threshold (${income.year} bundled data): (${dollars(income.base)} + (${income.household_size} − 1) × ${dollars(income.increment)}) × ${income.multiplier} ÷ 12 = ${dollars(income.monthly_limit)} / month.`, 'formula');
    add(i, 'p', `${dollars(income.total)} ${income.within_demo_limit ? '≤' : '>'} ${dollars(income.monthly_limit)} — ${income.within_demo_limit ? 'within' : 'above'} this demo’s income limit.`, 'badge');
    add(i, 'p', 'The intake adds all converted income, then drops cents from the total and rounds the monthly threshold. This repository uses 300% of its bundled federal poverty guideline; this is a demonstration screening rule, not a determination of eligibility for legal help.', 'note');
  }
  const f = byId('conflict-detail'); f.replaceChildren(); const conflict = data.conflict;
  if (conflict.status === 'waiting') add(f, 'p', 'Waiting for the opposing party’s name. Try Jordan Vale to see a fictional match.', 'empty-state');
  else {
    add(f, 'p', `Names checked: ${conflict.searched.join(', ') || 'No opposing parties reported'}.`);
    add(f, 'p', 'Compares full names after ignoring case, punctuation, and repeated spaces against two invented client records.', 'note');
    if (!conflict.matches.length) add(f, 'p', 'No match in the fictional dataset. This is not a real conflict clearance.');
    for (const match of conflict.matches) {
      add(f, 'p', `${match.searched_name} → ${match.name}`, 'result-value');
      add(f, 'p', `${match.record} · ${match.matter}`);
      add(f, 'p', 'Potential conflict: staff review needed', 'badge warning');
    }
  }
}
export function updateInsights(data?: Explanations) { current = data || empty; render(); }
export function showSessionInsights() {
  showingExample = false; (byId('session-view') as HTMLButtonElement).disabled = false; current = empty; render();
}
byId('example-view').addEventListener('click', () => { showingExample = true; render(); });
byId('session-view').addEventListener('click', () => { showingExample = false; render(); });
render();
void fetch('/api/demo').then(r => { if (!r.ok) throw Error('Demo details unavailable'); return r.json(); }).then((data: Catalog) => {
  example = data.example;
  const chips = byId('county-examples');
  for (const name of ['Amherst County', 'Appomattox County', 'Campbell County', 'Halifax County', 'Henry County', 'Pittsylvania County']) {
    if (data.counties.includes(name)) add(chips, 'span', name);
  }
  byId('all-locations').textContent = `Counties: ${data.counties.join(', ')}. Independent cities: ${data.cities.join(', ')}. From the demo’s configured service area.`;
  render();
}).catch(() => { byId('all-locations').textContent = 'Sample locations could not load. Please refresh to try again.'; });
