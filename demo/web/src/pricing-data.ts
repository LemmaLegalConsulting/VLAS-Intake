// Public rate snapshot checked 2026-10-19. Account contracts can differ.
export const checkedOn = 'October 19, 2026';
export const sources = {
  deepgram: 'https://deepgram.com/pricing',
  mip: 'https://developers.deepgram.com/changelog/2026/3/5',
  googleModel: 'https://ai.google.dev/gemini-api/docs/pricing',
  googleStt: 'https://cloud.google.com/speech-to-text/pricing',
  googleTts: 'https://cloud.google.com/text-to-speech/pricing',
  azure: "https://prices.azure.com/api/retail/prices?$filter=armRegionName%20eq%20'eastus2'%20and%20productName%20eq%20'Azure%20Speech'%20and%20skuName%20eq%20'S1'",
  daily: 'https://www.daily.co/pricing/pipecat-cloud/',
  twilio: 'https://www.twilio.com/en-us/voice/pricing/us',
  telnyx: 'https://telnyx.com/pricing/voice-ai-agents',
  telnyxVoice: 'https://telnyx.com/pricing/voice-api',
  bland: 'https://www.bland.ai/pricing',
  fly: 'https://docs.fly.io/about/pricing',
  rime: 'https://www.rime.ai/pricing',
  rimeLegacy: 'https://www.rime.ai/resources/introducing-new-pricing',
  lmnt: 'https://www.lmnt.com/',
};
const openai = (model: string) => `https://developers.openai.com/api/docs/models/${model}`;
export type Model = {id: string; name: string; input: number; output: number; perMinute?: number; source?: string; note?: string};
export const models: Model[] = [
  {id:'gpt41mini', name:'OpenAI GPT-4.1 mini', input:.4, output:1.6, source:openai('gpt-4.1-mini')},
  {id:'gpt41', name:'OpenAI GPT-4.1', input:2, output:8, source:openai('gpt-4.1')},
  {id:'gpt4o', name:'OpenAI GPT-4o', input:2.5, output:10, source:openai('gpt-4o')},
  {id:'gpt4omini', name:'OpenAI GPT-4o mini (deprecated)', input:.15, output:.6, source:openai('gpt-4o-mini'), note:'Deprecated model; retained for existing deployments.'},
  {id:'geminiPro', name:'Gemini 2.5 Pro', input:1.25, output:10, source:sources.googleModel, note:'Standard text pricing for prompts up to 200k tokens.'},
  {id:'geminiFlash', name:'Gemini 2.5 Flash', input:.3, output:2.5, source:sources.googleModel},
  {id:'geminiLite', name:'Gemini 2.5 Flash-Lite', input:.1, output:.4, source:sources.googleModel},
  {id:'phone', name:'PhoneLLM / Modal (workbook estimate)', input:0, output:0, perMinute:.0025, note:'Unverified workbook allowance; not a current provider quote.'},
  {id:'phone2', name:'PhoneLLM / Modal ×2 (workbook estimate)', input:0, output:0, perMinute:.005, note:'Unverified workbook allowance; not a current provider quote.'},
  {id:'custom', name:'Custom model rates', input:0, output:0, note:'Enter your contracted rates below.'},
];
export type Speech = {id: string; name: string; rate: number; unit: 'minute' | 'millionChars' | 'tokens'; source: string; growth?: number; regular?: number; growthRegular?: number; freeChars?: number; note?: string};
export const stt: Speech[] = [
  {id:'fluxMulti',name:'Deepgram Flux Multilingual',rate:.0078,growth:.0068,unit:'minute',source:sources.deepgram},
  {id:'flux',name:'Deepgram Flux English',rate:.0065,regular:.0077,growth:.0057,growthRegular:.0065,unit:'minute',source:sources.deepgram,note:'Current streaming promotion; end date is not published.'},
  {id:'nova',name:'Deepgram Nova-3 Monolingual',rate:.0048,regular:.0077,growth:.0042,growthRegular:.0065,unit:'minute',source:sources.deepgram,note:'Current streaming promotion; end date is not published.'},
  {id:'novaMulti',name:'Deepgram Nova-3 Multilingual',rate:.0058,regular:.0092,growth:.005,growthRegular:.0078,unit:'minute',source:sources.deepgram,note:'Current streaming promotion; end date is not published.'},
  {id:'azure',name:'Azure Speech S1 · East US 2',rate:1/60,unit:'minute',source:sources.azure,note:'$1/audio hour, USD retail; no free-tier allowance on S1.'},
  {id:'google',name:'Google Cloud STT V2',rate:.016,unit:'minute',source:sources.googleStt,note:'Standard recognition; automatic volume tiers apply above 500k minutes.'},
  {id:'openai',name:'OpenAI GPT-4o Transcribe',rate:.006,unit:'minute',source:'https://developers.openai.com/api/docs/pricing',note:'Published estimate per audio minute; actual billing is token-based.'},
];
export const tts: Speech[] = [
  {id:'fluxTts',name:'Deepgram Flux TTS',rate:45,growth:40.5,unit:'millionChars',source:sources.deepgram},
  {id:'aura',name:'Deepgram Aura-2',rate:30,growth:27,unit:'millionChars',source:sources.deepgram},
  {id:'azure',name:'Azure Neural TTS · East US 2',rate:15,unit:'millionChars',source:sources.azure},
  {id:'chirp',name:'Google Chirp 3 HD',rate:30,freeChars:1e6,unit:'millionChars',source:sources.googleTts},
  {id:'neural2',name:'Google Neural2',rate:16,freeChars:1e6,unit:'millionChars',source:sources.googleTts},
  {id:'rimeMist',name:'Rime Mist',rate:30,unit:'millionChars',source:sources.rime},
  {id:'rimeCoda',name:'Rime Coda',rate:50,unit:'millionChars',source:sources.rime},
  {id:'arcana',name:'Rime Arcana (legacy rate)',rate:30,unit:'millionChars',source:sources.rimeLegacy,note:'January 2026 rate; no longer listed on the current pricing page. Confirm availability and quote.'},
  {id:'openai',name:'OpenAI GPT-4o mini TTS (deprecated)',rate:12,unit:'tokens',source:openai('gpt-4o-mini-tts'),note:'$0.60/M text input + $12/M audio output tokens. Deprecated; confirm availability.'},
];
export type Transport = {id:string; name:string; minute:number; stream:number; number:number; refer:number; source?:string; note?:string};
export const transports: Transport[] = [
  {id:'dailyPstn',name:'Daily PSTN',minute:.018,stream:0,number:0,refer:.2,source:sources.daily,note:'Includes SIP transport. Number rental is not published here; add any charge under extra monthly costs.'},
  {id:'dailySip',name:'Daily SIP (editable rate)',minute:.005,stream:0,number:0,refer:.2,source:sources.daily,note:'Published range $0.003–$0.02/min. $0.005 is a workbook assumption. Upstream carrier costs are separate.'},
  {id:'twilioPstn',name:'Twilio US local inbound + Media Streams',minute:.0085,stream:.0044,number:1.15,refer:.1,source:sources.twilio},
  {id:'twilioSip',name:'Twilio SIP + Media Streams',minute:.004,stream:.0044,number:0,refer:.1,source:sources.twilio,note:'SIP interface only; add upstream carrier fees.'},
  {id:'telnyxPstn',name:'Telnyx US inbound + WebSockets',minute:.0052,stream:.0035,number:1,refer:.1,source:sources.telnyx,note:'$0.0032 starting carrier rate + $0.002 Voice API fee; destination rates vary.'},
  {id:'telnyxSip',name:'Telnyx SIP interface + WebSockets',minute:.002,stream:.0035,number:0,refer:.1,source:sources.telnyxVoice,note:'SIP interface only; add upstream carrier fees.'},
  {id:'browser',name:'Browser · no telephone',minute:0,stream:0,number:0,refer:0,note:'Direct WebSocket on Fly/VPS; free 1:1 Daily voice transport on Pipecat Cloud.'},
];
export type Host = {id:string;name:string;minute:number;monthly:number;reserved:number;source?:string;note?:string};
export const hosts: Host[] = [
  {id:'cloud',name:'Pipecat Cloud · agent-1x',minute:.01,monthly:0,reserved:.0005,source:sources.daily},
  {id:'cloud2',name:'Pipecat Cloud · agent-2x',minute:.02,monthly:0,reserved:.001,source:sources.daily},
  {id:'flySleep',name:'Fly · sleep when idle',minute:0,monthly:13.39,reserved:0,source:sources.fly},
  {id:'flyWarm',name:'Fly · always warm',minute:0,monthly:13.39,reserved:0,source:sources.fly},
  {id:'vps',name:'Self-hosted VPS (workbook budget)',minute:0,monthly:125,reserved:0,note:'Editable $125/month workbook budget; not a provider quote.'},
];
