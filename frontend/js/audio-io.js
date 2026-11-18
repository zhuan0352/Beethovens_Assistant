// Getting audio in (microphone, files) and out (WAV) of the browser.

const ANALYSIS_RATE = 16000;
export const MAX_TAKE_SECONDS = 90;

/** 16-bit PCM WAV from an AudioBuffer (any channel count). */
export function encodeWav(buffer) {
  const channels = buffer.numberOfChannels;
  const frames = buffer.length;
  const bytes = new ArrayBuffer(44 + frames * channels * 2);
  const view = new DataView(bytes);
  const text = (offset, s) => [...s].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  text(0, "RIFF");
  view.setUint32(4, 36 + frames * channels * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, channels, true);
  view.setUint32(24, buffer.sampleRate, true);
  view.setUint32(28, buffer.sampleRate * channels * 2, true);
  view.setUint16(32, channels * 2, true);
  view.setUint16(34, 16, true);
  text(36, "data");
  view.setUint32(40, frames * channels * 2, true);
  const data = Array.from({ length: channels }, (_, ch) => buffer.getChannelData(ch));
  let offset = 44;
  for (let i = 0; i < frames; i++) {
    for (let ch = 0; ch < channels; ch++) {
      const v = Math.max(-1, Math.min(1, data[ch][i]));
      view.setInt16(offset, v < 0 ? v * 0x8000 : v * 0x7fff, true);
      offset += 2;
    }
  }
  return new Blob([bytes], { type: "audio/wav" });
}

/** Downmix + resample a take to the mono 16 kHz WAV the backend analyses. */
export async function toAnalysisWav(buffer) {
  const length = Math.ceil(buffer.duration * ANALYSIS_RATE);
  const ctx = new OfflineAudioContext(1, length, ANALYSIS_RATE);
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.connect(ctx.destination);
  source.start();
  return encodeWav(await ctx.startRendering());
}

/** Decode any audio the browser understands (wav, mp3, m4a, ogg, webm…). */
export async function decodeAudio(arrayBuffer) {
  const ctx = new OfflineAudioContext(1, 1, 44100);
  try {
    return await ctx.decodeAudioData(arrayBuffer);
  } catch {
    throw new Error("That file isn't audio this browser can read. Try a WAV, MP3 or M4A.");
  }
}

/** Rough live pitch (autocorrelation) — only for the on-screen trace while recording. */
export function detectPitch(samples, sampleRate) {
  const n = samples.length;
  let energy = 0;
  for (let i = 0; i < n; i++) energy += samples[i] * samples[i];
  const rms = Math.sqrt(energy / n);
  if (rms < 0.012) return { rms, midi: null };

  const minLag = Math.floor(sampleRate / 900);
  const maxLag = Math.min(Math.floor(sampleRate / 75), n >> 1);
  const span = n - maxLag;
  let bestLag = -1;
  let best = 0;
  let prev = 0;
  let rising = false;
  let zero = 0;
  for (let i = 0; i < span; i++) zero += samples[i] * samples[i];
  for (let lag = minLag; lag <= maxLag; lag++) {
    let sum = 0;
    for (let i = 0; i < span; i += 2) sum += samples[i] * samples[i + lag];
    const r = (2 * sum) / (zero || 1);
    if (r > prev) rising = true;
    else if (rising && prev > 0.6 && prev > best + 0.05) {
      best = prev; // first strong peak wins: avoids octave-down errors
      bestLag = lag - 1;
      rising = false;
    } else if (r < prev) rising = false;
    prev = r;
  }
  if (bestLag < 0) return { rms, midi: null };
  return { rms, midi: 69 + 12 * Math.log2(sampleRate / bestLag / 440) };
}

export class Recorder {
  #stream = null;
  #ctx = null;
  #node = null;
  #analyser = null;
  #chunks = [];
  #scratch = null;

  async start() {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("This browser can't record audio here. Upload a file instead.");
    }
    try {
      this.#stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      });
    } catch (err) {
      const denied = err?.name === "NotAllowedError" || err?.name === "SecurityError";
      throw new Error(
        denied
          ? "Microphone access was blocked. Allow it for this page, or upload a file instead."
          : "No microphone was found. Plug one in, or upload a file instead.",
      );
    }
    this.#ctx = new AudioContext();
    await this.#ctx.audioWorklet.addModule("js/recorder-worklet.js");
    const source = this.#ctx.createMediaStreamSource(this.#stream);
    this.#node = new AudioWorkletNode(this.#ctx, "recorder");
    // a silent path to the output keeps the browser pulling audio through the worklet
    const silent = this.#ctx.createGain();
    silent.gain.value = 0;
    this.#node.connect(silent).connect(this.#ctx.destination);
    this.#chunks = [];
    this.#node.port.onmessage = (e) => this.#chunks.push(e.data);
    this.#analyser = this.#ctx.createAnalyser();
    this.#analyser.fftSize = 2048;
    this.#scratch = new Float32Array(this.#analyser.fftSize);
    source.connect(this.#node);
    source.connect(this.#analyser);
  }

  /** Current loudness and pitch, for the live display. */
  probe() {
    if (!this.#analyser) return { rms: 0, midi: null };
    this.#analyser.getFloatTimeDomainData(this.#scratch);
    return detectPitch(this.#scratch, this.#ctx.sampleRate);
  }

  async stop() {
    const sampleRate = this.#ctx.sampleRate;
    this.#node.port.postMessage("flush");
    await new Promise((resolve) => setTimeout(resolve, 60));
    this.#stream.getTracks().forEach((t) => t.stop());
    this.#node.disconnect();
    await this.#ctx.close();
    this.#analyser = null;

    const length = this.#chunks.reduce((n, c) => n + c.length, 0);
    if (!length) throw new Error("Nothing was recorded. Check your microphone and try again.");
    const buffer = new AudioBuffer({ length, sampleRate, numberOfChannels: 1 });
    const data = buffer.getChannelData(0);
    let offset = 0;
    for (const chunk of this.#chunks) {
      data.set(chunk, offset);
      offset += chunk.length;
    }
    this.#chunks = [];
    return buffer;
  }
}
