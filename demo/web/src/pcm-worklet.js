class DemoPCM extends AudioWorkletProcessor {
  constructor() {
    super();
    this.samples = new Int16Array(320);
    this.offset = 0;
  }
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (channel) {
      for (const sample of channel) {
        this.samples[this.offset++] = Math.max(-1, Math.min(1, sample)) * 32767;
        if (this.offset === this.samples.length) {
          this.port.postMessage(this.samples.buffer, [this.samples.buffer]);
          this.samples = new Int16Array(320);
          this.offset = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor('demo-pcm', DemoPCM);
