import { WavMediaManager } from '@pipecat-ai/websocket-transport';

/** Browser audio adapter: create/resume Web Audio during the Start click.
 * The SDK's default manager loads a remote Daily bundle; its WAV manager
 * creates the playback context after awaiting microphone permission, which
 * loses the user activation required by some browsers.
 */
export class BrowserAudio extends WavMediaManager {
  private context = new AudioContext({sampleRate: 16000});
  onInputLevel: ((level: number) => void) | undefined;
  private stream: MediaStream | null = null;
  private input: MediaStreamAudioSourceNode | null = null;
  private recorder: AudioWorkletNode | null = null;
  private outputs = new Set<AudioBufferSourceNode>();
  private nextTime = 0;
  private connected = false;
  private closed = false;
  private activeMic = true;

  constructor() {
    super();
    // Must run synchronously inside the visitor's click handler.
    void this.context.resume().catch(() => {});
  }

  override async initialize() {
    const stream = await navigator.mediaDevices.getUserMedia({audio: {
      echoCancellation: true, noiseSuppression: true, autoGainControl: true,
    }});
    if (this.closed) { stream.getTracks().forEach(t => t.stop()); return; }
    this.stream = stream;
    // Chrome on Windows can cancel all system playback, including Web Audio.
    // Older browsers retain the standard cancellation requested above.
    try {
      await stream.getAudioTracks()[0].applyConstraints({
        echoCancellation: {exact: 'all'},
      } as unknown as MediaTrackConstraints);
    } catch { /* Unsupported mode: retain echoCancellation: true. */ }
    if (this.closed) return;
    await this.context.audioWorklet.addModule(new URL('./pcm-worklet.js', import.meta.url).href);
    if (this.closed) return;
    this.recorder = new AudioWorkletNode(this.context, 'demo-pcm');
    this.recorder.port.onmessage = event => {
      const samples = new Int16Array(event.data);
      let peak = 0;
      for (const sample of samples) peak = Math.max(peak, Math.abs(sample) / 32768);
      this.onInputLevel?.(this.activeMic ? peak : 0);
      if (this.connected) this._userAudioCallback(this.activeMic ? event.data : new ArrayBuffer(event.data.byteLength));
    };
    this.input = this.context.createMediaStreamSource(stream);
    this.input.connect(this.recorder);
    // The worklet outputs silence; connecting it keeps the capture clock running.
    this.recorder.connect(this.context.destination);
  }

  get needsActivation() { return this.context.state === "suspended"; }
  resume() { return this.context.resume(); }
  override async connect() { this.connected = true; void this.context.resume().catch(() => {}); }
  override async disconnect() {
    if (this.closed) return;
    this.closed = true; this.connected = false;
    this.stream?.getTracks().forEach(t => t.stop());
    this.input?.disconnect(); this.recorder?.disconnect(); this.recorder?.port.close();
    await this.userStartedSpeaking();
    if (this.context.state !== 'closed') await this.context.close();
  }
  override async userStartedSpeaking() {
    for (const node of this.outputs) { try { node.stop(); } catch { /* Already ended. */ } }
    this.outputs.clear(); this.nextTime = 0;
  }
  override bufferBotAudio(data: ArrayBuffer | Int16Array) {
    const pcm = data instanceof Int16Array ? data : new Int16Array(data);
    if (this.closed || !pcm.length || !pcm.some(n => n !== 0)) return pcm;
    const buffer = this.context.createBuffer(1, pcm.length, 24000);
    const samples = buffer.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) samples[i] = pcm[i] / 32768;
    const source = this.context.createBufferSource(); source.buffer = buffer;
    source.connect(this.context.destination);
    this.outputs.add(source);
    source.onended = () => { source.disconnect(); this.outputs.delete(source); };
    const when = Math.max(this.context.currentTime + .025, this.nextTime);
    source.start(when); this.nextTime = when + buffer.duration;
    return pcm;
  }
  override tracks() { return {local: {audio: this.stream?.getAudioTracks()[0]}}; }
  override async enableMic(enable: boolean) {
    this.activeMic = enable;
    this.stream?.getAudioTracks().forEach(t => { t.enabled = enable; });
  }
  override get isMicEnabled() { return this.activeMic; }
  override get isCamEnabled() { return false; }
  override get selectedMic() { return {}; }
  override get selectedCam() { return {}; }
  override get selectedSpeaker() { return {}; }
  override async getAllMics() { return (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'audioinput'); }
  override async getAllCams() { return []; }
  override async getAllSpeakers() { return (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'audiooutput'); }
  override async updateMic(_id: string) { /* Uses the browser-selected default device. */ }
  override updateCam(_id: string) { /* Audio-only demo. */ }
  override updateSpeaker(_id: string) { /* Uses the browser-selected output. */ }
  override enableCam(_enable: boolean) { /* Audio-only demo. */ }
}
