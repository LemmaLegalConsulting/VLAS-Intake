import {models, stt, tts, transports, hosts} from './pricing-data.ts';
export type Inputs = {
  minutesPerCall:number; calls:number; transferPercent:number; transferMinutes:number; numbers:number;
  model:string; stt:string; tts:string; transport:string; host:string;
  inputTokens:number; outputTokens:number; characters:number; audioTokens:number;
  customInput:number; customOutput:number; modelExtra:number;
  growth:boolean; regular:boolean; googleFree:boolean; krisp:boolean;
  hours:number; machines:number; rootGb:number; reserveHours:number; vps:number;
  sipRate:number; carrierRate:number; extraMonthly:number;
  telnyxModel:number; telnyxCarrier:number;
  stackIntegration:number; blandIntegration:number; telnyxIntegration:number; dialpadIntegration:number;
  maintenance:number; setupBudget:number; hostedMaintenance:number; hostedSetup:number; dialpadConversation:number; dialpadBillablePercent:number; dialpadMonthly:number; dialpadCarrier:number;
  blandCarrier:number; blandTransferCarrier:number; blandNumber:number;
};
export const defaults: Inputs = {
  minutesPerCall:15,calls:1500,transferPercent:0,transferMinutes:3,numbers:1,
  model:'gpt41mini',stt:'fluxMulti',tts:'fluxTts',transport:'dailyPstn',host:'cloud',
  inputTokens:525,outputTokens:157,characters:511.5,audioTokens:600,
  customInput:.4,customOutput:1.6,modelExtra:.03,
  growth:false,regular:false,googleFree:false,krisp:false,
  hours:400,machines:1,rootGb:2,reserveHours:0,vps:125,sipRate:.005,carrierRate:0,extraMonthly:0,
  stackIntegration:25,blandIntegration:50,telnyxIntegration:50,dialpadIntegration:50,maintenance:600,setupBudget:6000,hostedMaintenance:300,hostedSetup:3000,
  dialpadConversation:1.99,dialpadBillablePercent:100,dialpadMonthly:250,dialpadCarrier:.01,
  telnyxModel:.0064,telnyxCarrier:.0032,blandCarrier:.0085,blandTransferCarrier:.0225,blandNumber:1.15,
};
export const limits: Partial<Record<keyof Inputs, number>> = {
  dialpadBillablePercent:100,minutesPerCall:120,calls:1e6,transferPercent:100,transferMinutes:120,numbers:10000,
  inputTokens:1e7,outputTokens:1e7,characters:1e6,audioTokens:1e6,
  hours:720,machines:10000,rootGb:10000,reserveHours:1e7,
};
export function normalize(input: Inputs): Inputs {
  const result = {...input};
  for (const key of Object.keys(result) as (keyof Inputs)[]) {
    const value = result[key];
    if (typeof value === 'number') {
      const n = Math.min(limits[key] ?? 1e6, Math.max(0, Number.isFinite(value) ? value : 0));
      (result as unknown as Record<string,unknown>)[key] = ['calls','numbers','machines'].includes(key) ? Math.floor(n) : n;
    }
  }
  result.machines = Math.max(1,result.machines);
  return result;
}
export type Line = {label:string;cost:number;basis:string;source?:string};
export type Comparison = {name:string;monthly:number;setup:number;note:string;source?:string;lines:Line[]};
const pick = <T extends {id:string}>(values:T[],id:string):T => values.find(v=>v.id===id) ?? values[0];
export function estimate(raw: Inputs) {
  const x=normalize(raw), m=x.minutesPerCall*x.calls;
  const model=pick(models,x.model), recognition=pick(stt,x.stt), voice=pick(tts,x.tts);
  const transport=pick(transports,x.transport),host=pick(hosts,x.host);
  const notes:string[]=[];
  const selected=[model,recognition,voice,transport,host];
  for(const item of selected) if(item.note) notes.push(item.note);
  const rate = (item:typeof recognition) => x.growth && item.growth !== undefined
    ? (x.regular ? item.growthRegular ?? item.growth : item.growth)
    : (x.regular ? item.regular ?? item.rate : item.rate);
  const sttRate=rate(recognition),ttsRate=rate(voice);
  const sttCost=recognition.id==='google'
    ? Math.min(m,500000)*.016 + Math.min(Math.max(0,m-500000),500000)*.01 + Math.min(Math.max(0,m-1000000),1000000)*.008 + Math.max(0,m-2000000)*.004
    : m*sttRate;
  const freeChars=x.googleFree ? voice.freeChars ?? 0 : 0;
  const charCount=m*x.characters;
  const ttsCost=voice.unit==='tokens'
    ? m*(x.outputTokens*.6+x.audioTokens*12)/1e6
    : Math.max(0,charCount-freeChars)*ttsRate/1e6;
  const inRate=model.id==='custom'?x.customInput:model.input, outRate=model.id==='custom'?x.customOutput:model.output;
  const modelCost=m*(model.perMinute ?? (x.inputTokens*inRate+x.outputTokens*outRate)/1e6);
  const isCloud=host.id.startsWith('cloud'),isDaily=transport.id.startsWith('daily');
  const isBrowser=transport.id==='browser';
  const referCount=isBrowser?0:x.calls*x.transferPercent/100;
  const carrier=isBrowser?0:x.carrierRate;
  const phoneRate=transport.id==='dailySip'?x.sipRate:transport.minute;
  const phoneCost=m*(phoneRate+transport.stream+carrier)+x.numbers*transport.number+referCount*transport.refer;
  const activeHours=host.id==='flyWarm'?720:x.hours;
  const hosting=host.id.startsWith('fly')
    ? x.machines*(host.monthly*activeHours/720+x.rootGb*.15*(1-activeHours/720))
    : host.id==='vps'?x.vps:m*host.minute+x.reserveHours*60*host.reserved;
  const lines:Line[]=[
    {label:'Speech recognition',cost:sttCost,basis:`${recognition.name} · $${sttRate.toFixed(4)}/audio min${recognition.id==='google'?' before volume tiers':''}`,source:recognition.source},
    {label:'Language model',cost:modelCost,basis:`${model.name} · ${model.perMinute!==undefined?'$'+model.perMinute+'/min':`$${inRate} input / $${outRate} output per M tokens`}`,source:model.source},
    {label:'Voice synthesis',cost:ttsCost,basis:`${voice.name} · ${voice.unit==='tokens'?'text + audio tokens':`$${ttsRate}/M characters`}${freeChars?' · 1M free characters applied':''}`,source:voice.source},
    {label:'Telephone & transfers',cost:phoneCost,basis:`${transport.name} · $${(phoneRate+transport.stream+carrier).toFixed(4)}/min${transport.number ? ` + ${x.numbers} number(s) × $${transport.number}/mo` : isDaily ? ' · number rental excluded (confirm quote)' : ''}${referCount ? ` + ${referCount.toFixed(1)} expected REFERs × $${transport.refer}` : ''}`,source:transport.source},
    {label:'Hosting',cost:hosting,basis:`${host.name}${host.id.startsWith('fly')?` · ${x.machines} machine(s), ${activeHours} hours each`:isCloud?` · $${host.minute}/active min + ${x.reserveHours} reserved agent-hours`:''}`,source:host.source},
  ];
  if(x.krisp && isCloud && (isDaily || isBrowser)) lines.push({label:'Krisp noise suppression',cost:Math.max(0,m-10000)*.0015,basis:'First 10,000 Pipecat Cloud minutes free; then $0.0015/min',source:host.source});
  else if(x.krisp) notes.push('Krisp allowance applies only to Pipecat Cloud with Daily transport; no Krisp charge included for this selection.');
  const dgUsage=(recognition.growth!==undefined?sttCost:0)+(voice.growth!==undefined?ttsCost:0);
  if(x.growth && (recognition.growth!==undefined || voice.growth!==undefined)) {
    lines.push({label:'Deepgram commitment balance',cost:Math.max(0,4000/12-dgUsage),basis:'$4,000/year minimum prepaid credit, allocated monthly; shared across STT and TTS',source:recognition.growth!==undefined?recognition.source:voice.source});
    notes.push('Growth totals allocate the annual prepaid minimum across 12 equal months. Assumes enough prepaid credit for usage; overage fees and unused-credit expiry can change the bill.');
  }
  if(x.extraMonthly) lines.push({label:'Other monthly costs',cost:x.extraMonthly,basis:'Your allowance; applied only to the component stack.'});
  if(isDaily && !isCloud) notes.push('Daily transport pricing shown is the Pipecat Cloud rate card. Confirm eligibility for Fly/VPS hosting; this combination needs a quote.');
  if(host.id.startsWith('fly') && m/60>x.machines*activeHours*2) notes.push('Capacity: these Fly active hours cannot fit the volume at two concurrent sessions per machine, even with perfect overlap.');
  if(host.id.startsWith('fly') && x.machines>1) notes.push('Multiple Fly machines require shared session routing/state; the current demo runs on one machine.');
  if(x.calls>0 && x.minutesPerCall===0) notes.push('Enter minutes per call greater than zero, or set calls per month to zero.');
  const integrationLines=(monthly:number,hosted=false):Line[]=>[
    {label:'Custom integration hosting',cost:monthly,basis:'Your monthly allowance for classification and LegalServer services; zero means not budgeted'},
    {label:'Extra classification usage',cost:x.calls*x.modelExtra,basis:`${x.calls} calls × $${x.modelExtra}; shared assumption, exclude work already in the voice model`},
    {label:'Maintenance & support',cost:x.maintenance,basis:'Planning assumption: 4 hours/month × $150; editable, not a service quote'},
    ...(hosted?[{label:'Platform-to-backend upkeep',cost:x.hostedMaintenance,basis:'Planning assumption: 2 extra hours/month × $150 for the hosted-platform connection'}]:[]),
  ];
  lines.push(...integrationLines(x.stackIntegration));
  notes.push('Totals combine published usage rates with editable planning allowances, not measured production costs or vendor quotes. Zero custom-work allowances mean not budgeted, not free. Setup is separate; LegalServer fees and unmodeled account charges are excluded.');
  const total=lines.reduce((sum,line)=>sum+line.cost,0);
  const transfers=x.calls*x.transferPercent/100, transferMinutes=transfers*x.transferMinutes;
  // Telnyx bills the voice engine in 60-second increments. With only aggregate
  // inputs, rounding every call at the average duration is an estimate, not telemetry.
  const roundedMinutes=x.calls?x.calls*Math.ceil(x.minutesPerCall):m;
  const comparisons:Comparison[]=[{name:'Your component stack',monthly:total,setup:x.setupBudget,note:'Selected voice services plus classification, operations, and maintenance allowances.',lines}];
  for(const [name,fee,minute] of [['Bland Start',0,.14],['Bland Build',299,.12]] as const){
    const breakdown:Line[]=[
      {label:'AI talk time',cost:m*minute,basis:`$${minute}/connected minute`},
      {label:'Platform fee',cost:fee,basis:'Monthly subscription'},
      {label:'Bland transfer fee',cost:0,basis:'Waived for BYOT per Bland FAQ; carrier transfer legs still billed'},
      {label:'BYOT carrier & numbers',cost:m*x.blandCarrier+transferMinutes*x.blandTransferCarrier+x.numbers*x.blandNumber,basis:'Editable Twilio US local assumptions; transfer rate covers inbound + outbound legs'},
    ];
    breakdown.push(...integrationLines(x.blandIntegration,true));
    comparisons.push({name,setup:x.setupBudget+x.hostedSetup,monthly:breakdown.reduce((s,l)=>s+l.cost,0),note:'BYOT estimate: AI and speech bundled; carrier charges separate. No Bland transfer fee for BYOT. Custom-work allowances added; plan limits apply.',source:'https://www.bland.ai/pricing',lines:breakdown});
  }
  const telnyxLines:Line[]=[
    {label:'Voice engine',cost:roundedMinutes*.05,basis:`$0.05/min; ${roundedMinutes.toLocaleString('en-US')} estimated rounded minutes`},
    {label:'Model allowance',cost:m*x.telnyxModel,basis:`$${x.telnyxModel}/min estimate, not token telemetry`},
    {label:'Carrier & numbers',cost:m*x.telnyxCarrier+x.numbers,basis:`$${x.telnyxCarrier}/min + $1/local number/month`},
    {label:'Transfers',cost:transfers*.1,basis:'$0.10/transfer; downstream call excluded'},
  ];
  telnyxLines.push(...integrationLines(x.telnyxIntegration,true));
  comparisons.push({name:'Telnyx Voice AI',setup:x.setupBudget+x.hostedSetup,monthly:telnyxLines.reduce((s,l)=>s+l.cost,0),note:'PAYG voice engine + model allowance + local inbound carrier. Uses average-call rounding; actual call lengths can cost more.',source:'https://telnyx.com/pricing/voice-ai-agents',lines:telnyxLines});
  const dialpadLines:Line[]=[
    {label:'AI voice conversations',cost:x.calls*x.dialpadBillablePercent/100*x.dialpadConversation,basis:`$${x.dialpadConversation}/billable conversation; ${x.dialpadBillablePercent}% of calls assumed billable`,source:'https://www.dialpad.com/rates/ai-agent/'},
    {label:'Dialpad plan / seats allowance',cost:x.dialpadMonthly,basis:'Planning allowance for account-specific fees; not a published Dialpad plan price'},
    {label:'Dialpad calling allowance',cost:m*x.dialpadCarrier,basis:'Editable per-minute allowance; confirm calling charges with Dialpad'},
    ...integrationLines(x.dialpadIntegration,true),
  ];
  comparisons.push({name:'Dialpad AI Agent',setup:x.setupBudget+x.hostedSetup,monthly:dialpadLines.reduce((s,l)=>s+l.cost,0),note:'Published $1.99/voice conversation, not per minute. Assumes one session per call and all calls billable unless adjusted. Plan, seats, calling and integration charges need allowances; this is a subtotal.',source:'https://www.dialpad.com/rates/ai-agent/',lines:dialpadLines});
  return {setupBudget:x.setupBudget,inputs:x,monthlyMinutes:m,lines,total,comparisons,notes,referCount,roundedMinutes,averageMinutes:x.calls?m/x.calls:null};
}
