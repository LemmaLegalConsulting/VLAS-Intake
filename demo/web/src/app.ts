import './costs';
import { PipecatClient, RTVIEvent, LogLevel } from '@pipecat-ai/client-js';
import { WebSocketTransport } from '@pipecat-ai/websocket-transport';
import './style.css';
import { BrowserAudio } from './audio';
import { updateInsights, showSessionInsights, type Explanations } from './insights';

const el = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
let client: PipecatClient | null = null;
let token = '';
let connectionError = '';
let timer: ReturnType<typeof setInterval> | undefined;
let ending = false;
let muted = false;
let refreshing = false;
let answerEndedAt = 0;
let data: Summary = { finished: false, error: null, sections: [] };
type Summary = { finished: boolean; error: string | null; sections: {title: string; data: unknown}[]; explanations?: Explanations };
function status(text: string) { el('status').textContent = text; }
function screen(id: string) {
  for (const name of ['welcome', 'conversation', 'results']) el(name).hidden = name !== id;
}
async function request(path: string, options: RequestInit = {}) {
  const response = await fetch(path, {...options, signal: AbortSignal.timeout(60000)});
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || 'The demo is unavailable. Please try again.');
  return body;
}
async function timeout<T>(work: Promise<T>, ms: number, message: string): Promise<T> {
  let handle: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([work, new Promise<never>((_, reject) => {
      handle = setTimeout(() => reject(new Error(message)), ms);
    })]);
  } finally { clearTimeout(handle); }
}
async function release(current: PipecatClient | null) {
  current?.tracks().local?.audio?.stop();
  try { if (current) await timeout(current.disconnect(), 4000, 'Disconnect timed out'); }
  catch { /* Microphone is already stopped; the server also enforces a deadline. */ }
}
function render() {
  updateInsights(data.explanations);
  const container = el('summary');
  container.replaceChildren();
  if (!data.sections.length) {
    const p = document.createElement('p');
    p.textContent = 'No intake details were captured yet. Try another conversation and answer the opening questions.';
    container.append(p);
  }
  for (const section of data.sections) {
    const article = document.createElement('article');
    const h = document.createElement('h2'); h.textContent = section.title; article.append(h);
    const dl = document.createElement('dl');
    function field(value: unknown, label: string) {
      if (value === null || value === undefined || value === '') return;
      if (typeof value === 'object') {
        for (const [key, item] of Object.entries(value)) {
          if (key === 'status' || key === 'error' || key.startsWith('_')) continue;
          field(item, label ? `${label} · ${key.replaceAll('_', ' ')}` : key.replaceAll('_', ' '));
        }
      } else {
        const dt = document.createElement('dt'); dt.textContent = label;
        const dd = document.createElement('dd'); dd.textContent = typeof value === 'boolean' ? (value ? 'Yes' : 'No') : String(value);
        dl.append(dt, dd);
      }
    }
    field(section.data, ''); article.append(dl); container.append(article);
  }
}
async function finish() {
  if (ending) return;
  ending = true;
  clearInterval(timer);
  const current = client; client = null;
  await release(current);
  screen('results'); status('Preparing your summary…');
  el<HTMLButtonElement>('restart').disabled = true;
  try {
    for (let attempt = 0; attempt < 15; attempt++) {
      data = await request('/api/results', {headers: {Authorization: `Bearer ${token}`}});
      render();
      if (data.finished) break;
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    status(data.error || connectionError || (data.finished ? 'Demo complete. These details have not been submitted.' : 'Showing the details captured so far.'));
  } catch (error) { render(); status(error instanceof Error ? error.message : 'Could not retrieve the summary.'); }
  el<HTMLButtonElement>('restart').disabled = false;
}
el('start').addEventListener('click', async () => {
  const start = el<HTMLButtonElement>('start'); start.disabled = true;
  ending = false; muted = false; token = ''; connectionError = ''; data = { finished: false, error: null, sections: [] };
  el('mute').textContent = 'Mute microphone';
  answerEndedAt = 0;
  el('response-time').textContent = '';
  status('Allow microphone access to start.');
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('Please open the demo in a browser with microphone support over HTTPS.');
    const media = new BrowserAudio();
    media.onInputLevel = level => { el<HTMLMeterElement>('mic-level').value = level; };
    el('resume').onclick = () => { void media.resume().then(() => { el('resume').hidden = !media.needsActivation; }).catch(() => { if (!ending) status('Audio could not start. Check your browser’s sound permissions.'); }); };
    status('Setting up your microphone and speakers…');
    client = new PipecatClient({
      transport: new WebSocketTransport({recorderSampleRate: 16000, playerSampleRate: 24000, mediaManager: media}),
      enableMic: true, enableCam: false,
      callbacks: {
        onConnected: () => { el('resume').hidden = !media.needsActivation; el('activity').textContent = 'Connected. Your assistant is getting ready…'; },
        onBotReady: () => { el('activity').textContent = 'Your assistant is ready'; },
        onDisconnected: () => { if (!ending) void finish(); },
        onError: () => { connectionError = 'The voice connection ended unexpectedly. Showing any details captured so far.'; void finish(); },
      },
    });
    client.setLogLevel(LogLevel.ERROR);
    client.on(RTVIEvent.UserStartedSpeaking, () => { void media.userStartedSpeaking(); });
    client.on(RTVIEvent.UserStoppedSpeaking, () => { answerEndedAt = Date.now(); el('activity').textContent = 'Thinking about your answer…'; });
    client.on(RTVIEvent.BotLlmStarted, () => { el('activity').textContent = 'Preparing a response…'; });
    client.on(RTVIEvent.BotStartedSpeaking, () => {
      el('activity').textContent = 'Your assistant is speaking';
      if (answerEndedAt) {
        el('response-time').textContent = `Response prepared in ${((Date.now() - answerEndedAt) / 1000).toFixed(1)}s after your turn was detected as complete.`;
        answerEndedAt = 0;
      }
    });
    client.on(RTVIEvent.BotStoppedSpeaking, () => { el('activity').textContent = 'Listening to you'; });
    client.on(RTVIEvent.UserTranscript, transcript => {
      if (transcript.final) el('heard').textContent = `Heard: “${transcript.text}”`;
    });
    await timeout(client.initDevices(), 15000, 'Audio setup took too long. Please retry or use another browser.');
    status('Waking up the voice assistant. The first connection may take a moment…');
    const session = await request('/api/sessions', {method: 'POST'}); token = session.token;
    showSessionInsights();
    const wsUrl = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/${token}`;
    el('timer').textContent = '0:00 / 10:00';
    screen('conversation'); el('activity').textContent = 'Connecting…';
    el('heard').textContent = '';
    await timeout(client.connect({wsUrl}), 60000, 'The voice connection timed out. Please try again.');
    if (ending) return;
    status('');
    const started = Date.now();
    timer = setInterval(() => {
      const elapsed = Math.floor((Date.now() - started) / 1000);
      el('timer').textContent = `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, '0')} / 10:00`;
      if (elapsed % 2 === 0 && !refreshing && !ending) {
        refreshing = true;
        const currentToken = token;
        void request('/api/results', {headers: {Authorization: `Bearer ${currentToken}`}})
          .then((latest: Summary) => { if (!ending && token === currentToken) updateInsights(latest.explanations); })
          .catch(() => { /* A transient refresh failure should not interrupt the voice session. */ })
          .finally(() => { refreshing = false; });
      }
      if (elapsed >= session.max_seconds) void finish();
    }, 1000);
  } catch (error) {
    if (ending) return;
    ending = true;
    await release(client);
    client = null; clearInterval(timer);
    screen('welcome');
    status(error instanceof Error ? error.message : 'Unable to start. Check your microphone and try again.');
  } finally { start.disabled = false; }
});
el('finish').addEventListener('click', () => void finish());
el('mute').addEventListener('click', () => { muted = !muted; client?.enableMic(!muted); el('mute').textContent = muted ? 'Unmute microphone' : 'Mute microphone'; });
el('restart').addEventListener('click', () => { token = ''; data = {finished: false, error: null, sections: []}; el('summary').replaceChildren(); screen('welcome'); status(''); });
el('download').addEventListener('click', () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify({sections: data.sections, explanations: data.explanations}, null, 2)], {type: 'application/json'}));
  const link = document.createElement('a'); link.href = url; link.download = 'lemma-demo-intake.json'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
window.addEventListener('pagehide', () => { void release(client); });
