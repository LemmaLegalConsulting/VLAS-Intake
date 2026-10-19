import test from 'node:test';
import assert from 'node:assert/strict';
import {estimate,defaults as productionDefaults,normalize} from '../src/cost-model.ts';
import {models,stt,tts,hosts,transports} from '../src/pricing-data.ts';
const uiDefaults={...productionDefaults,modelExtra:0,stackIntegration:0,blandIntegration:0,telnyxIntegration:0,dialpadIntegration:0,maintenance:0,setupBudget:0,hostedMaintenance:0,hostedSetup:0,dialpadMonthly:0,dialpadCarrier:0};
const defaults={...uiDefaults,minutesPerCall:22000/1500};
const close=(actual,expected)=>assert.ok(Math.abs(actual-expected)<1e-8,`${actual} != ${expected}`);
const line=(result,name)=>result.lines.find(l=>l.label===name).cost;
test('22,000-minute baseline uses current rates without an opt-out multiplier',()=>{
 const r=estimate(defaults);
 close(line(r,'Speech recognition'),171.6);
 close(line(r,'Voice synthesis'),506.385);
 close(line(r,'Language model'),10.1464);
 close(line(r,'Telephone & transfers'),396);
 close(line(r,'Hosting'),220);
 close(r.total,1304.1314);
});
test('free character allowances stop at zero and match the selected SKU',()=>{
 for(const tts of ['chirp','neural2'])close(line(estimate({...defaults,minutesPerCall:1,calls:1,tts,googleFree:true}),'Voice synthesis'),0);
 close(line(estimate({...defaults,tts:'neural2',googleFree:true}),'Voice synthesis'),164.048);
});
test('Deepgram Growth shares its prepaid floor across recognition and voice',()=>{
 const r=estimate({...defaults,minutesPerCall:10,calls:10,growth:true});
 close(line(r,'Speech recognition')+line(r,'Voice synthesis')+line(r,'Deepgram commitment balance'),4000/12);
 const high=estimate({...defaults,growth:true});
 close(line(high,'Deepgram commitment balance'),0);
});
test('Deepgram promotion toggle preserves regular and Growth prices separately',()=>{
 close(line(estimate({...defaults,stt:'flux'}),'Speech recognition'),143);
 close(line(estimate({...defaults,stt:'flux',regular:true}),'Speech recognition'),169.4);
 close(line(estimate({...defaults,stt:'flux',growth:true}),'Speech recognition'),125.4);
});
test('Krisp free minutes are bounded and limited to Daily on Pipecat Cloud',()=>{
 close(line(estimate({...defaults,krisp:true}),'Krisp noise suppression'),18);
 close(line(estimate({...defaults,minutesPerCall:10,calls:50,krisp:true}),'Krisp noise suppression'),0);
 assert.equal(estimate({...defaults,host:'flyWarm',krisp:true}).lines.some(l=>l.label==='Krisp noise suppression'),false);
});
test('REFER fees use transferred calls; Bland BYOT waives platform transfer fees',()=>{
 const r=estimate({...defaults,transferPercent:20,transferMinutes:3});
 close(line(r,'Telephone & transfers'),456);
 close(r.comparisons[1].lines.find(l=>l.label==='Bland transfer fee').cost,0);
 close(r.comparisons[2].lines.find(l=>l.label==='Bland transfer fee').cost,0);
});
test('Twilio includes updated Media Streams and local number rental',()=>{
 close(line(estimate({...defaults,transport:'twilioPstn'}),'Telephone & transfers'),284.95);
});
test('Google STT uses marginal volume tiers',()=>{
 close(line(estimate({...defaults,stt:'google',minutesPerCall:60,calls:10000}),'Speech recognition'),9000);
});
test('Azure hour and character units, OpenAI TTS token units',()=>{
 const r=estimate({...defaults,stt:'azure',tts:'azure'});
 close(line(r,'Speech recognition'),22000/60);
 close(line(r,'Voice synthesis'),168.795);
 close(line(estimate({...defaults,tts:'openai'}),'Voice synthesis'),160.4724);
});
test('sleeping Fly bills only uptime plus stopped rootfs, reserved cloud separate',()=>{
 close(line(estimate({...defaults,host:'flySleep',hours:0,minutesPerCall:15,calls:0}),'Hosting'),.3);
 close(line(estimate({...defaults,host:'flySleep',hours:720}),'Hosting'),13.39);
 close(line(estimate({...defaults,reserveHours:100}),'Hosting'),223);
});
test('zero usage preserves fixed subscription charges without NaN',()=>{
 const r=estimate({...defaults,minutesPerCall:15,calls:0});
 assert.equal(r.averageMinutes,null);
 close(r.total,0);
 close(r.comparisons[2].monthly,300.15);
 close(r.comparisons[3].monthly,1);
});
test('Telnyx voice engine rounds average duration up to whole minutes',()=>{
 const r=estimate({...defaults,minutesPerCall:22000/1500,calls:1500});
 assert.equal(r.roundedMinutes,22500);
 close(r.comparisons[3].lines[0].cost,1125);
});
test('all provider choices produce finite nonnegative totals',()=>{
 for(const [key,list] of Object.entries({model:models,stt,tts,host:hosts,transport:transports}))for(const v of list){
  const r=estimate({...defaults,[key]:v.id});
  for(const c of r.comparisons)assert.ok(Number.isFinite(c.monthly)&&c.monthly>=0);
 }
});
test('invalid numeric input is bounded and custom model rates are honored',()=>{
 const x=normalize({...defaults,minutesPerCall:-100,hours:Infinity,transferPercent:200,calls:NaN});
 assert.equal(x.minutesPerCall,0);assert.equal(x.hours,0);assert.equal(x.transferPercent,100);assert.equal(x.calls,0);
 close(line(estimate({...defaults,model:'custom',customInput:1,customOutput:2}),'Language model'),18.458);
});

test('duration times calls drives every usage-based comparison',()=>{
 const r=estimate({...uiDefaults,minutesPerCall:10,calls:200});
 assert.equal(r.monthlyMinutes,2000);
 assert.equal(r.averageMinutes,10);
 close(line(r,'Speech recognition'),15.6);
 close(r.comparisons[1].lines[0].cost,280);
 close(r.comparisons[3].lines[0].cost,100);
 const longer=estimate({...r.inputs,minutesPerCall:20});
 assert.equal(longer.monthlyMinutes,4000);
 close(longer.total,2*r.total);
 const more=estimate({...r.inputs,calls:400});
 assert.equal(more.monthlyMinutes,4000);
 close(more.total,longer.total);
 assert.equal(estimate(uiDefaults).monthlyMinutes,22500);
});
test('fractional call lengths and zero calls produce correct monthly volume',()=>{
 const r=estimate({...uiDefaults,minutesPerCall:2.5,calls:3});
 assert.equal(r.monthlyMinutes,7.5);
 assert.equal(r.roundedMinutes,9);
 assert.equal(estimate({...uiDefaults,calls:0}).monthlyMinutes,0);
});

test('Dialpad prices billable conversations independently of call duration',()=>{
 const r=estimate(uiDefaults);
 close(r.comparisons[4].monthly,2985);
 close(estimate({...uiDefaults,minutesPerCall:30}).comparisons[4].monthly,2985);
 close(estimate({...uiDefaults,dialpadBillablePercent:50,dialpadMonthly:200,dialpadCarrier:.01}).comparisons[4].monthly,1492.5+200+225);
 close(estimate({...uiDefaults,calls:0,dialpadMonthly:200}).comparisons[4].monthly,200);
 assert.equal(normalize({...uiDefaults,dialpadBillablePercent:150}).dialpadBillablePercent,100);
});
test('custom work is budgeted for all platforms, setup stays separate',()=>{
 const baseline=estimate(uiDefaults);
 const r=estimate({...uiDefaults,stackIntegration:10,blandIntegration:20,telnyxIntegration:30,dialpadIntegration:40,modelExtra:.02,maintenance:100,setupBudget:5000});
 const hosting=[10,20,20,30,40];
 r.comparisons.forEach((c,i)=>close(c.monthly-baseline.comparisons[i].monthly,hosting[i]+30+100));
 close(r.total-baseline.total,140);
 assert.equal(r.setupBudget,5000);
 close(r.total,r.lines.reduce((s,l)=>s+l.cost,0));
});

test('public defaults include paid operations and hosted-backend work',()=>{
 const r=estimate(productionDefaults);
 close(r.total,2003.77075);
 close(r.comparisons[3].monthly,2337);
 close(r.comparisons[4].monthly,4455);
 close(r.comparisons[0].setup,6000);
 close(r.comparisons[3].setup,9000);
 close(r.comparisons[3].monthly*12+r.comparisons[3].setup,37044);
 for(const c of r.comparisons){
  assert.ok(c.lines.find(l=>l.label==='Extra classification usage').cost>0);
  assert.ok(c.lines.find(l=>l.label==='Maintenance & support').cost>0);
 }
 const zero=estimate({...productionDefaults,calls:0});
 close(zero.total,625);
 close(zero.comparisons[3].monthly,951);
});
test('hosted upkeep and setup affect only hosted platforms',()=>{
 const r=estimate({...uiDefaults,hostedMaintenance:200,hostedSetup:3000});
 const base=estimate(uiDefaults);
 r.comparisons.forEach((c,i)=>{
  close(c.monthly-base.comparisons[i].monthly,i?200:0);
  close(c.setup,i?3000:0);
 });
});
