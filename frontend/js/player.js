// Plays all rendered versions in lockstep and crossfades between them, so
// switching style never loses your place in the track.

const FADE = 0.012; // seconds (time constant)

export class Player extends EventTarget {
  ctx = null;
  buffers = {};
  nodes = null;
  active = null;
  playing = false;
  loop = false;
  offset = 0;
  startedAt = 0;
  solo = null; // one-off playback (the original hum)

  ensureContext() {
    if (!this.ctx) {
      this.ctx = new AudioContext({ latencyHint: "interactive" });
      this.master = this.ctx.createGain();
      this.master.connect(this.ctx.destination);
    }
    if (this.ctx.state === "suspended") this.ctx.resume();
    return this.ctx;
  }

  get duration() {
    return Math.max(0, ...Object.values(this.buffers).map((b) => b.duration));
  }

  get position() {
    if (!this.playing) return this.offset;
    const elapsed = Math.max(0, this.ctx.currentTime - this.startedAt);
    return Math.min(this.duration, this.offset + elapsed);
  }

  #emit() {
    this.dispatchEvent(new Event("change"));
  }

  #stopNodes() {
    if (this.nodes) {
      for (const { source, gain } of Object.values(this.nodes)) {
        try {
          source.stop();
        } catch {
          /* never started */
        }
        gain.disconnect();
      }
    }
    this.nodes = null;
    this.playing = false;
  }

  #start() {
    const ctx = this.ensureContext();
    this.stopSolo();
    const when = ctx.currentTime + 0.03;
    this.nodes = {};
    for (const [id, buffer] of Object.entries(this.buffers)) {
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      const gain = ctx.createGain();
      gain.gain.value = id === this.active ? 1 : 0;
      source.connect(gain).connect(this.master);
      if (this.offset < buffer.duration) source.start(when, this.offset);
      this.nodes[id] = { source, gain };
    }
    this.startedAt = when;
    this.playing = true;
  }

  /** Replace every buffer (after a re-arrange). Keeps playing from the same spot. */
  setBuffers(buffers) {
    const wasPlaying = this.playing;
    const position = this.position;
    this.#stopNodes();
    this.buffers = { ...buffers };
    this.offset = Math.min(position, Math.max(0, this.duration - 0.05));
    if (!(this.active in this.buffers)) this.active = Object.keys(this.buffers)[0] ?? null;
    if (wasPlaying && this.duration > 0) this.#start();
    this.#emit();
  }

  setBuffer(id, buffer) {
    this.setBuffers({ ...this.buffers, [id]: buffer });
  }

  select(id) {
    if (!(id in this.buffers)) return;
    this.active = id;
    if (this.nodes) {
      const now = this.ctx.currentTime;
      for (const [key, { gain }] of Object.entries(this.nodes)) {
        gain.gain.cancelScheduledValues(now);
        gain.gain.setTargetAtTime(key === id ? 1 : 0, now, FADE);
      }
    }
    this.#emit();
  }

  play() {
    if (this.playing || !this.duration) return;
    if (this.offset >= this.duration - 0.1) this.offset = 0;
    this.#start();
    this.#emit();
  }

  pause() {
    if (!this.playing) return;
    this.offset = this.position;
    this.#stopNodes();
    this.#emit();
  }

  toggle() {
    if (this.playing) this.pause();
    else this.play();
  }

  seek(seconds) {
    const wasPlaying = this.playing;
    this.#stopNodes();
    this.offset = Math.min(Math.max(0, seconds), this.duration);
    if (wasPlaying) this.#start();
    this.#emit();
  }

  /** Call once per animation frame: handles reaching the end. */
  tick() {
    if (this.playing && this.position >= this.duration - 0.01) {
      this.#stopNodes();
      this.offset = 0;
      if (this.loop) this.#start();
      this.#emit();
    }
  }

  playSolo(buffer, onEnd) {
    const ctx = this.ensureContext();
    this.pause();
    this.stopSolo();
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.master);
    source.onended = () => {
      if (this.solo === source) {
        this.solo = null;
        onEnd?.();
      }
    };
    source.start();
    this.solo = source;
    this.soloEnd = onEnd;
  }

  stopSolo() {
    if (!this.solo) return;
    const source = this.solo;
    this.solo = null;
    source.onended = null;
    source.stop();
    this.soloEnd?.();
  }
}
