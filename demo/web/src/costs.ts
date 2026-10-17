// Planning assumptions, not billing telemetry. See the visible methodology.
const ids = ['calls', 'minutes', 'hours', 'stt', 'tts', 'model'];
const fields = Object.fromEntries(ids.map(id => [id, document.getElementById(`cost-${id}`) as HTMLInputElement]));
const money = (n: number) => new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD'}).format(n);
function calculate() {
  const read = (id: string) => Math.max(0, Number(fields[id].value) || 0);
  const calls = Math.min(100000, read('calls'));
  const minutes = Math.min(120, read('minutes'));
  const hours = Math.min(720, read('hours'));
  const recognition = calls * minutes / 60 * read('stt');
  const synthesis = calls * minutes * .4 * 150 * 6 / 1e6 * read('tts');
  const models = calls * read('model');
  const usage = recognition + synthesis + models;
  const scenarios = [
    ['Sleep when idle', 13.39 * hours / 720 + .30 * (1 - hours / 720), 'One machine; entered active hours'],
    ['Keep one machine warm', 13.39, 'One machine, running 24/7'],
    ['Keep two machines warm', 26.78, 'Two machines; same total call volume'],
  ] as const;
  const output = document.getElementById('cost-results')!;
  output.replaceChildren();
  for (const [title, hosting, description] of scenarios) {
    const article = document.createElement('article');
    const h = document.createElement('h3'); h.textContent = title;
    const total = document.createElement('p'); total.className = 'cost-total'; total.textContent = `${money(hosting + usage)} / month`;
    const detail = document.createElement('p'); detail.textContent = `${money(hosting)} hosting + ${money(usage)} speech and models`;
    const per = document.createElement('p'); per.className = 'note'; per.textContent = calls ? `${money((hosting + usage) / calls)} per intake at this volume. ${description}.` : `No intakes; hosting only. ${description}.`;
    article.append(h, total, detail, per); output.append(article);
  }
  document.getElementById('cost-formula')!.textContent = `Monthly usage = recognition ${money(recognition)} + voice ${money(synthesis)} + model allowance ${money(models)}. Hosting is added separately.`;
  document.getElementById('cost-capacity')!.textContent = calls * minutes / 60 > hours * 2
    ? 'The entered active hours cannot accommodate this call volume at the demo’s two-session limit, even with perfect overlap. Increase active hours or plan additional capacity.'
    : 'Active hours include startup, idle gaps, and conversations; they are not automatically inferred from call count. This is a cost comparison, not a capacity guarantee.';
}
for (const field of Object.values(fields)) field.addEventListener('input', calculate);
calculate();
