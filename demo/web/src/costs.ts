import {defaults, estimate, limits, normalize, type Inputs} from './cost-model.ts';
import {models, stt, tts, transports, hosts, sources, checkedOn} from './pricing-data.ts';
const byId=(id:string)=>document.getElementById(id)!;
const money=(n:number,digits=2)=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:digits,maximumFractionDigits:digits}).format(n);
const number=(n:number)=>new Intl.NumberFormat('en-US',{maximumFractionDigits:1}).format(n);
function node<K extends keyof HTMLElementTagNameMap>(tag:K,text='',className=''){
  const e=document.createElement(tag);e.textContent=text;e.className=className;return e;
}
const fields=new Map<keyof Inputs,HTMLInputElement|HTMLSelectElement>();
function input(parent:string,key:keyof Inputs,label:string,step='any',help=''){
  const wrapper=node('div','', 'cost-field'), l=node('label',label), field=node('input');
  l.htmlFor=field.id=`cost-${key}`;field.name=key;field.type='number';field.min=key==='machines'?'1':'0';field.max=String(limits[key]??1e6);field.step=step;field.value=String(defaults[key]);
  fields.set(key,field);wrapper.append(l,field);
  if(help){const hint=node('small',help);hint.id=`cost-${key}-hint`;field.setAttribute('aria-describedby',hint.id);wrapper.append(hint)}
  byId(parent).append(wrapper);
}
function select(parent:string,key:keyof Inputs,label:string,values:{id:string;name:string}[]){
  const wrapper=node('div','', 'cost-field'),l=node('label',label),field=node('select');
  l.htmlFor=field.id=`cost-${key}`;field.name=key;
  for(const v of values){const opt=node('option',v.name);opt.value=v.id;field.append(opt)}
  field.value=String(defaults[key]);fields.set(key,field);wrapper.append(l,field);byId(parent).append(wrapper);
}
function checkbox(parent:string,key:keyof Inputs,label:string){
  const l=node('label','', 'check-field'),f=node('input');f.type='checkbox';f.id=`cost-${key}`;f.checked=Boolean(defaults[key]);fields.set(key,f);l.append(f,node('span',label));byId(parent).append(l);
}
input('cost-volume','minutesPerCall','Minutes / call','0.5');
input('cost-volume','calls','Calls / month','1');
input('cost-volume','transferPercent','Calls transferred (%)');
select('cost-stack','stt','Speech recognition',stt);
select('cost-stack','model','Language model',models);
select('cost-stack','tts','Voice synthesis',tts);
select('cost-stack','transport','Telephone / browser',transports);
select('cost-stack','host','Hosting',hosts);
input('cost-usage','inputTokens','Model input tokens / call minute','any','Count all billed context, instructions, tool calls, and retries—not just new words.');
input('cost-usage','outputTokens','Model output tokens / call minute','any','Include reasoning tokens when billed.');
input('cost-usage','characters','Voice characters / call minute','any','Workbook assumption: 511.5, independent of model token counts.');
input('cost-usage','audioTokens','TTS audio tokens / call minute','any','OpenAI TTS only; 600 is an editable estimate, not measured usage.');
input('cost-usage','modelExtra','Additional model budget / call ($)','0.01','For classification or other work not counted in the token estimate.');
input('cost-custom-model','customInput','Custom input rate / million tokens ($)');
input('cost-custom-model','customOutput','Custom output rate / million tokens ($)');
checkbox('cost-discounts','growth','Deepgram Growth: $4,000/year prepaid minimum');
checkbox('cost-discounts','regular','Use Deepgram regular STT rates instead of current promotions');
checkbox('cost-discounts','googleFree','Apply unused Google TTS monthly free allowance (1M characters)');
checkbox('cost-discounts','krisp','Add Krisp noise suppression on Daily / Pipecat Cloud');
input('cost-telephone','numbers','Local phone numbers','1');
input('cost-telephone','transferMinutes','Minutes after transfer','any','Used for Bland’s transfer-time billing. Component stack uses SIP REFER event fees.');
input('cost-telephone','sipRate','Daily SIP rate / minute ($)','any','Workbook $0.005 assumption; published range $0.003–$0.02.');
input('cost-telephone','carrierRate','Extra upstream carrier / minute ($)','any','For SIP or destination surcharges on your component stack.');
input('cost-hosting','machines','Fly machines','1');
input('cost-hosting','hours','Fly active hours / machine / month','any','Sleeping option only. A 30-day month has 720 hours.');
input('cost-hosting','rootGb','Fly stopped filesystem / machine (GB)');
input('cost-hosting','reserveHours','Pipecat reserved agent-hours / month','any','Total idle reserved capacity; excludes active minutes already charged.');
input('cost-hosting','vps','VPS budget / month ($)');
input('cost-hosting','extraMonthly','Other stack costs / month ($)','any','Add number rental, monitoring, storage, or support.');
input('cost-platforms','telnyxModel','Telnyx model allowance / minute ($)','any','Provider’s Kimi example: $0.0064. Independent of the component model selection.');
input('cost-platforms','telnyxCarrier','Telnyx inbound carrier / minute ($)','any','Published starting US local rate; replace with your destination rate.');
input('cost-platforms','blandCarrier','Bland BYOT inbound carrier / minute ($)');
input('cost-platforms','blandTransferCarrier','Bland BYOT carrier / transfer minute ($)','any','Twilio assumption: $0.0085 inbound + $0.014 outbound.');
input('cost-platforms','blandNumber','Bland BYOT number rental / month ($)');
function read():Inputs{
  const result={...defaults};
  for(const [key,f] of fields){
    (result as unknown as Record<string,unknown>)[key]=f instanceof HTMLInputElement && f.type==='checkbox'?f.checked:typeof defaults[key]==='number'?Number(f.value):f.value;
  }
  return normalize(result);
}
function link(text:string,url:string){const a=node('a',text);a.href=url;a.target='_blank';a.rel='noopener noreferrer';return a}
let current=estimate(defaults);
function render(){
  const x=read();current=estimate(x);
  byId('cost-custom-model').hidden=x.model!=='custom';
  const invalid=x.calls>0 && x.minutesPerCall===0;
  byId('cost-invalid').hidden=!invalid;
  byId('cost-output').hidden=invalid;
  byId('cost-download').toggleAttribute('disabled',invalid);
  byId('cost-volume-summary').textContent=`${number(x.minutesPerCall)} min/call × ${number(x.calls)} calls/month = ${number(current.monthlyMinutes)} minutes/month · USD`;
  if(invalid)return;
  byId('cost-monthly').textContent=money(current.total);
  byId('cost-yearly').textContent=`${money(current.total*12)} / year`;
  byId('cost-unit').textContent=x.calls?`${money(current.total/x.calls)} / call · ${money(current.total/current.monthlyMinutes,4)} / minute`:'No usage; fixed fees and commitments still apply.';
  const body=byId('cost-breakdown');body.replaceChildren();
  const bar=byId('cost-bar');bar.replaceChildren();
  current.lines.forEach((line,i)=>{
    const row=node('tr');const label=node('th',line.label);label.scope='row';
    const basis=node('td');basis.append(node('span',line.basis));if(line.source)basis.append(document.createTextNode(' · '),link('Rate source',line.source));
    row.append(label,basis,node('td',money(line.cost)));body.append(row);
    if(line.cost>0){const segment=node('span','',`cost-segment segment-${i%7}`);segment.style.flexGrow=String(line.cost);segment.title=`${line.label}: ${money(line.cost)}`;bar.append(segment)}
  });
  const compare=byId('cost-comparisons');compare.replaceChildren();
  for(const item of current.comparisons){
    const article=node('article'),title=node('h3',item.name),total=node('p',money(item.monthly),'cost-total');
    const unit=node('p',`${money(item.monthly*12)} / year${x.calls?' · '+money(item.monthly/x.calls)+'/call':''}`,'note');
    const details=node('details');details.append(node('summary','Cost breakdown'));
    const list=node('dl');for(const line of item.lines)list.append(node('dt',line.label),node('dd',money(line.cost)));details.append(list);
    article.append(title,total,node('p','per month','cost-period'),unit,node('p',item.note,'note'),details);
    if(item.source)article.append(link('Provider pricing ↗',item.source));compare.append(article);
  }
  const notes=byId('cost-notes');notes.replaceChildren();
  for(const note of current.notes)notes.append(node('li',note));
  byId('cost-notes-wrap').hidden=current.notes.length===0;
}
for(const f of fields.values()){
  f.addEventListener('input',render);
  f.addEventListener('change',()=>{if(f instanceof HTMLInputElement && f.type==='number'){const key=f.name as keyof Inputs;f.value=String(read()[key])}render()});
}
function preset(values:Inputs){
  for(const [key,f] of fields){if(f instanceof HTMLInputElement && f.type==='checkbox')f.checked=Boolean(values[key]);else f.value=String(values[key])}render();
}
byId('cost-reset').addEventListener('click',()=>preset(defaults));
byId('cost-browser').addEventListener('click',()=>preset({...defaults,minutesPerCall:10,calls:100,model:'gpt41mini',stt:'fluxMulti',tts:'fluxTts',transport:'browser',host:'flySleep',hours:30,numbers:0}));
byId('cost-download').addEventListener('click',()=>{
  const quote=(v:unknown)=>`"${String(v).replaceAll('"','""')}"`;
  const rows:unknown[][]=[['Voice intake cost estimate','USD',`Rates checked ${checkedOn}`],['Assumption','Value'],...Object.entries(current.inputs),['monthlyMinutes',current.monthlyMinutes],[],['Component','Monthly USD','Basis','Source'],...current.lines.map(l=>[l.label,l.cost.toFixed(6),l.basis,l.source??'User/workbook assumption']),[],['Comparison','Monthly USD','Annual USD','Notes'],...current.comparisons.map(c=>[c.name,c.monthly.toFixed(6),(c.monthly*12).toFixed(6),c.note]),[],['Exclusions','Taxes, implementation, downstream REFER carrier, compliance, unmodeled support, and account-specific fees.'],...current.notes.map(n=>['Note',n])];
  const url=URL.createObjectURL(new Blob([rows.map(r=>r.map(quote).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}));
  const a=node('a');a.href=url;a.download='lemma-voice-cost-estimate.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
const sourceList=byId('cost-source-links');
for(const [label,url] of [['Deepgram',sources.deepgram],['Opt-out pricing change',sources.mip],['OpenAI','https://developers.openai.com/api/docs/pricing'],['Gemini',sources.googleModel],['Azure East US 2 retail rates',sources.azure],['Google STT',sources.googleStt],['Google TTS',sources.googleTts],['Daily / Pipecat',sources.daily],['Twilio US',sources.twilio],['Telnyx',sources.telnyx],['Bland',sources.bland],['Fly',sources.fly],['Rime',sources.rime],['LMNT closure',sources.lmnt]]){const li=node('li');li.append(link(label,url));sourceList.append(li)}
render();
