// Synthesised instrument voices for the Web Audio renderer. No samples: plucked
// strings are Karplus-Strong buffers, everything else is oscillators + filters.
//
// Every voice has the signature (ctx, out, midi, t, dur, vel, shared) and
// schedules one note starting at time `t` (seconds) into the node `out`.
// The backend refers to voices by name via each track's `instrument` field.

export const mtof = (m) => 440 * 2 ** ((m - 69) / 12);

function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ---------------------------------------------------------------- shared

const sharedByContext = new WeakMap();

/** Per-context resources: a noise buffer and custom waveforms. */
export function sharedFor(ctx) {
  let s = sharedByContext.get(ctx);
  if (s) return s;
  const rand = mulberry32(1234);
  const noise = ctx.createBuffer(1, ctx.sampleRate * 2, ctx.sampleRate);
  const data = noise.getChannelData(0);
  for (let i = 0; i < data.length; i++) data[i] = rand() * 2 - 1;

  // piano-ish spectrum: strong fundamental, harmonics falling off ~1/n^1.4
  const n = 20;
  const real = new Float32Array(n);
  const imag = new Float32Array(n);
  for (let h = 1; h < n; h++) imag[h] = 1 / h ** 1.4;
  const pianoWave = ctx.createPeriodicWave(real, imag);

  s = { noise, pianoWave };
  sharedByContext.set(ctx, s);
  return s;
}

// ---------------------------------------------------------------- helpers

/**
 * Oscillators -> lowpass -> amp envelope -> out.
 * spec: { oscs: [[typeOrWave, freq, detuneCents, gain]], cutoff, q, attack,
 *         release, peak, decay?: {to, tau}, vibrato?: {rate, depth, delay} }
 */
function voice(ctx, out, t, end, spec) {
  const amp = ctx.createGain();
  amp.gain.value = 0;
  const filter = ctx.createBiquadFilter();
  filter.type = "lowpass";
  filter.frequency.value = spec.cutoff;
  filter.frequency.automationRate = "k-rate"; // per-sample filter sweeps are costly and inaudible here
  filter.Q.value = spec.q ?? 0.7;
  filter.connect(amp).connect(out);

  const releaseAt = Math.max(end, t + spec.attack);
  const stopAt = releaseAt + spec.release * 4.6 + 0.02; // -40 dB: inaudible under the mix

  let lfoGain = null;
  if (spec.vibrato) {
    const { rate, depth, delay } = spec.vibrato;
    const lfo = ctx.createOscillator();
    lfo.frequency.value = rate;
    lfoGain = ctx.createGain();
    lfoGain.gain.value = 0;
    lfoGain.gain.setValueAtTime(0, t + delay);
    lfoGain.gain.linearRampToValueAtTime(depth, t + delay + 0.25);
    lfo.connect(lfoGain);
    lfo.start(t);
    lfo.stop(stopAt);
  }

  for (const [type, freq, detune, gain] of spec.oscs) {
    const o = ctx.createOscillator();
    if (typeof type === "string") o.type = type;
    else o.setPeriodicWave(type);
    o.frequency.value = freq;
    o.detune.value = detune;
    o.detune.automationRate = "k-rate";
    if (lfoGain) lfoGain.connect(o.detune);
    if (gain === 1) {
      o.connect(filter); // every extra node costs render time, so skip unity gains
    } else {
      const g = ctx.createGain();
      g.gain.value = gain;
      o.connect(g).connect(filter);
    }
    o.start(t);
    o.stop(stopAt);
  }

  amp.gain.setValueAtTime(0, t);
  amp.gain.linearRampToValueAtTime(spec.peak, t + spec.attack);
  if (spec.decay) amp.gain.setTargetAtTime(spec.peak * spec.decay.to, t + spec.attack, spec.decay.tau);
  amp.gain.setTargetAtTime(0, releaseAt, spec.release);
  return { amp, filter };
}

/** Filtered noise burst with an exponential decay. */
function noiseHit(ctx, out, t, shared, { type, freq, q = 0.7, attack = 0.001, decay, gain }) {
  const src = ctx.createBufferSource();
  src.buffer = shared.noise;
  const filter = ctx.createBiquadFilter();
  filter.type = type;
  filter.frequency.value = freq;
  filter.Q.value = q;
  const amp = ctx.createGain();
  amp.gain.setValueAtTime(0, t);
  amp.gain.linearRampToValueAtTime(gain, t + attack);
  amp.gain.setTargetAtTime(0, t + attack, decay);
  src.connect(filter).connect(amp).connect(out);
  src.start(t, (t * 0.37) % 1.5);
  src.stop(t + attack + decay * 7);
}

// ---------------------------------------------------------------- plucked strings

const PLUCKS = {
  nylon_guitar: { damp: 0.5, t60: 2.6, bright: 0.45, pick: 0.18, release: 0.09, gain: 0.9 },
  steel_guitar: { damp: 0.32, t60: 3.6, bright: 0.85, pick: 0.12, release: 0.22, gain: 0.7 },
  acoustic_bass: { damp: 0.5, t60: 2.0, bright: 0.12, pick: 0.3, release: 0.07, gain: 1.2 },
  harp: { damp: 0.42, t60: 3.2, bright: 0.6, pick: 0.4, release: 0.5, gain: 0.8 },
};
const PLUCK_SECONDS = 4;
const pluckCache = new Map();

/** Karplus-Strong string with fractional delay, so high notes stay in tune. */
function pluckBuffer(kind, midi, sampleRate) {
  const key = `${kind}:${midi}:${sampleRate}`;
  let buffer = pluckCache.get(key);
  if (buffer) return buffer;

  const { damp, t60, bright, pick } = PLUCKS[kind];
  const freq = mtof(midi);
  const n = Math.floor(PLUCK_SECONDS * sampleRate);
  const y = new Float32Array(n);
  const period = sampleRate / freq - damp; // the 2-tap loop filter adds `damp` samples of delay
  const head = Math.ceil(period) + 2;
  const rand = mulberry32(midi * 131 + kind.length);

  // excitation: one period of low-passed noise, combed by the pick position
  let lp = 0;
  let mean = 0;
  for (let i = 0; i < head; i++) {
    lp += bright * (rand() * 2 - 1 - lp);
    y[i] = lp;
    mean += lp;
  }
  mean /= head;
  const comb = Math.max(1, Math.round(pick * period));
  for (let i = head - 1; i >= 0; i--) y[i] = y[i] - mean - (i >= comb ? y[i - comb] - mean : 0);

  const loopGain = 0.001 ** (1 / (freq * t60));
  for (let i = head; i < n; i++) {
    const x = i - period;
    const i0 = Math.floor(x);
    const fr = x - i0;
    const a = y[i0] * (1 - fr) + y[i0 + 1] * fr;
    const b = y[i0 - 1] * (1 - fr) + y[i0] * fr;
    y[i] = loopGain * ((1 - damp) * a + damp * b);
  }

  let peak = 1e-6;
  for (let i = 0; i < n; i++) peak = Math.max(peak, Math.abs(y[i]));
  for (let i = 0; i < n; i++) y[i] /= peak;
  const fade = Math.floor(0.05 * sampleRate);
  for (let i = 0; i < fade; i++) y[n - 1 - i] *= i / fade;

  buffer = new AudioBuffer({ length: n, sampleRate, numberOfChannels: 1 });
  buffer.copyToChannel(y, 0);
  pluckCache.set(key, buffer);
  return buffer;
}

function pluck(kind) {
  const { release, gain } = PLUCKS[kind];
  return (ctx, out, midi, t, dur, vel) => {
    const src = ctx.createBufferSource();
    src.buffer = pluckBuffer(kind, midi, ctx.sampleRate);
    const amp = ctx.createGain();
    amp.gain.setValueAtTime(gain * vel ** 1.4 * 0.5, t);
    amp.gain.setTargetAtTime(0, t + dur, release); // finger damps the string
    src.connect(amp).connect(out);
    src.start(t);
    src.stop(Math.min(t + PLUCK_SECONDS, t + dur + release * 7));
  };
}

// ---------------------------------------------------------------- keys, strings, winds

function piano(ctx, out, midi, t, dur, vel, shared) {
  const f = mtof(midi);
  const ring = Math.min(3.5, Math.max(0.35, 2.2 * 2 ** ((60 - midi) / 24))); // low notes ring longer
  const { filter } = voice(ctx, out, t, t + dur, {
    oscs: [
      [shared.pianoWave, f, -1.5, 1],
      [shared.pianoWave, f, 1.8, 1],
    ],
    cutoff: Math.min(14000, f * (3 + 9 * vel)),
    q: 0.5,
    attack: 0.004,
    release: 0.09,
    peak: 0.2 * vel ** 1.5,
    decay: { to: 0, tau: ring },
  });
  // the hammer strike is bright, then the tone mellows
  filter.frequency.setTargetAtTime(Math.min(9000, f * 2.2 + 300), t + 0.004, 0.22);
}

function stringSection(detunes, attack, brightness) {
  return (ctx, out, midi, t, dur, vel) => {
    const f = mtof(midi);
    voice(ctx, out, t, t + dur, {
      oscs: detunes.map((d) => ["sawtooth", f, d, 1]),
      cutoff: Math.min(6000, f * brightness) * (0.6 + 0.6 * vel),
      q: 0.4,
      attack,
      release: 0.22,
      peak: (0.3 / detunes.length) * vel ** 1.2,
      vibrato: { rate: 5.4 + (midi % 5) * 0.12, depth: 11, delay: 0.18 },
    });
  };
}

function horn(ctx, out, midi, t, dur, vel) {
  const f = mtof(midi);
  const { filter } = voice(ctx, out, t, t + dur, {
    oscs: [
      ["sawtooth", f, 0, 1],
      ["sine", f, 0, 1],
    ],
    cutoff: f * 1.4,
    q: 1.0,
    attack: 0.07,
    release: 0.18,
    peak: 0.16 * vel ** 1.2,
  });
  // brass opens up as the note speaks, then settles
  filter.frequency.setValueAtTime(f * 1.4, t);
  filter.frequency.linearRampToValueAtTime(f * (2.2 + 2.2 * vel), t + 0.09);
  filter.frequency.setTargetAtTime(f * (1.8 + 1.4 * vel), t + 0.09, 0.3);
}

function flute(ctx, out, midi, t, dur, vel, shared) {
  const f = mtof(midi);
  voice(ctx, out, t, t + dur, {
    oscs: [
      ["sine", f, 0, 0.8],
      ["triangle", f, 0, 0.2],
      ["sine", f * 2, 0, 0.08],
    ],
    cutoff: 6000,
    attack: 0.05,
    release: 0.1,
    peak: 0.3 * vel,
    vibrato: { rate: 5, depth: 9, delay: 0.2 },
  });
  // breath
  const src = ctx.createBufferSource();
  src.buffer = shared.noise;
  src.loop = true;
  const band = ctx.createBiquadFilter();
  band.type = "bandpass";
  band.frequency.value = f * 2;
  band.Q.value = 2;
  const amp = ctx.createGain();
  amp.gain.setValueAtTime(0, t);
  amp.gain.linearRampToValueAtTime(0.05 * vel, t + 0.04);
  amp.gain.setTargetAtTime(0.02 * vel, t + 0.04, 0.1);
  amp.gain.setTargetAtTime(0, t + dur, 0.08);
  src.connect(band).connect(amp).connect(out);
  src.start(t);
  src.stop(t + dur + 0.6);
}

function timpani(ctx, out, midi, t, _dur, vel, shared) {
  const f = mtof(midi);
  for (const [ratio, gain, tau] of [[1, 0.8, 0.55], [1.5, 0.3, 0.3], [1.98, 0.15, 0.2]]) {
    const o = ctx.createOscillator();
    o.frequency.setValueAtTime(f * ratio * 1.25, t);
    o.frequency.exponentialRampToValueAtTime(f * ratio, t + 0.05);
    const amp = ctx.createGain();
    amp.gain.setValueAtTime(0, t);
    amp.gain.linearRampToValueAtTime(gain * vel ** 1.3, t + 0.004);
    amp.gain.setTargetAtTime(0, t + 0.004, tau);
    o.connect(amp).connect(out);
    o.start(t);
    o.stop(t + tau * 7);
  }
  noiseHit(ctx, out, t, shared, { type: "lowpass", freq: 500, decay: 0.05, gain: 0.5 * vel });
}

// ---------------------------------------------------------------- synths

function synthLead(ctx, out, midi, t, dur, vel) {
  const f = mtof(midi);
  const { filter } = voice(ctx, out, t, t + dur, {
    oscs: [
      ["sawtooth", f, -7, 1],
      ["sawtooth", f, 7, 1],
      ["square", f / 2, 0, 0.45],
    ],
    cutoff: 1200 + 5500 * vel,
    q: 2.5,
    attack: 0.008,
    release: 0.07,
    peak: 0.11 * vel,
    vibrato: { rate: 5.5, depth: 7, delay: 0.3 },
  });
  filter.frequency.setTargetAtTime(1400 + 1200 * vel, t, 0.25);
}

function synthPad(ctx, out, midi, t, dur, vel) {
  const f = mtof(midi);
  const { filter } = voice(ctx, out, t, t + dur, {
    oscs: [
      ["sawtooth", f, -12, 1],
      ["sawtooth", f, 0, 1],
      ["sawtooth", f, 12, 1],
    ],
    cutoff: 700 + 900 * vel,
    q: 0.6,
    attack: 0.3,
    release: 0.35,
    peak: 0.06 * vel,
  });
  filter.frequency.setValueAtTime(700 + 900 * vel, t);
  filter.frequency.linearRampToValueAtTime(1100 + 1800 * vel, t + dur); // slow filter sweep
}

function synthArp(ctx, out, midi, t, dur, vel) {
  const f = mtof(midi);
  const { filter } = voice(ctx, out, t, t + dur, {
    oscs: [
      ["square", f, 0, 1],
      ["sawtooth", f, 6, 1],
    ],
    cutoff: 800 + 4500 * vel,
    q: 4,
    attack: 0.003,
    release: 0.06,
    peak: 0.077 * vel,
    decay: { to: 0.15, tau: 0.1 },
  });
  filter.frequency.setTargetAtTime(600, t, 0.08);
}

function synthBass(ctx, out, midi, t, dur, vel) {
  const f = mtof(midi);
  const { filter } = voice(ctx, out, t, t + dur, {
    oscs: [
      ["sawtooth", f, 0, 1],
      ["sine", f, 0, 1.4],
    ],
    cutoff: 300 + 1500 * vel,
    q: 3,
    attack: 0.005,
    release: 0.04,
    peak: 0.2 * vel,
    decay: { to: 0.7, tau: 0.15 },
  });
  filter.frequency.setTargetAtTime(260, t, 0.12);
}

// ---------------------------------------------------------------- drums (General MIDI note numbers)

const DRUMS = {
  36(ctx, out, t, vel, shared) { // kick
    const o = ctx.createOscillator();
    o.frequency.setValueAtTime(165, t);
    o.frequency.exponentialRampToValueAtTime(46, t + 0.1);
    const amp = ctx.createGain();
    amp.gain.setValueAtTime(vel, t);
    amp.gain.setTargetAtTime(0, t + 0.03, 0.09);
    o.connect(amp).connect(out);
    o.start(t);
    o.stop(t + 0.6);
    noiseHit(ctx, out, t, shared, { type: "lowpass", freq: 2500, decay: 0.006, gain: 0.35 * vel });
  },
  37(ctx, out, t, vel, shared) { // rim click
    const o = ctx.createOscillator();
    o.type = "triangle";
    o.frequency.value = 880;
    const amp = ctx.createGain();
    amp.gain.setValueAtTime(0.5 * vel, t);
    amp.gain.setTargetAtTime(0, t, 0.012);
    o.connect(amp).connect(out);
    o.start(t);
    o.stop(t + 0.1);
    noiseHit(ctx, out, t, shared, { type: "bandpass", freq: 2600, q: 1.5, decay: 0.012, gain: 0.6 * vel });
  },
  38(ctx, out, t, vel, shared) { // snare
    const o = ctx.createOscillator();
    o.type = "triangle";
    o.frequency.setValueAtTime(220, t);
    o.frequency.exponentialRampToValueAtTime(160, t + 0.06);
    const amp = ctx.createGain();
    amp.gain.setValueAtTime(0.5 * vel, t);
    amp.gain.setTargetAtTime(0, t, 0.05);
    o.connect(amp).connect(out);
    o.start(t);
    o.stop(t + 0.3);
    noiseHit(ctx, out, t, shared, { type: "highpass", freq: 1400, decay: 0.07, gain: 0.7 * vel });
  },
  39(ctx, out, t, vel, shared) { // clap
    for (const dt of [0, 0.011, 0.023]) {
      noiseHit(ctx, out, t + dt, shared, { type: "bandpass", freq: 1300, q: 1.2, decay: 0.008, gain: 0.6 * vel });
    }
    noiseHit(ctx, out, t + 0.03, shared, { type: "bandpass", freq: 1300, q: 1.0, decay: 0.07, gain: 0.5 * vel });
  },
  42(ctx, out, t, vel, shared) { // closed hat
    noiseHit(ctx, out, t, shared, { type: "highpass", freq: 7500, decay: 0.022, gain: 0.4 * vel });
  },
  46(ctx, out, t, vel, shared) { // open hat
    noiseHit(ctx, out, t, shared, { type: "highpass", freq: 7000, decay: 0.12, gain: 0.35 * vel });
  },
  70(ctx, out, t, vel, shared) { // shaker
    noiseHit(ctx, out, t, shared, { type: "bandpass", freq: 6200, q: 1.1, attack: 0.012, decay: 0.03, gain: 0.5 * vel });
  },
};

function drums(ctx, out, midi, t, _dur, vel, shared) {
  DRUMS[midi]?.(ctx, out, t, vel, shared);
}

// ---------------------------------------------------------------- registry

export const INSTRUMENTS = {
  piano,
  nylon_guitar: pluck("nylon_guitar"),
  steel_guitar: pluck("steel_guitar"),
  acoustic_bass: pluck("acoustic_bass"),
  harp: pluck("harp"),
  strings: stringSection([-9, 0, 8], 0.09, 7),
  cello: stringSection([-6, 5], 0.12, 6),
  contrabass: stringSection([-5, 4], 0.14, 5),
  horn,
  flute,
  timpani,
  synth_lead: synthLead,
  synth_pad: synthPad,
  synth_arp: synthArp,
  synth_bass: synthBass,
  drums,
};
