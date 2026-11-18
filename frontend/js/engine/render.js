// Renders one arrangement to a stereo AudioBuffer with OfflineAudioContext.
// Pre-rendering every style is what makes switching between them instant:
// playback is just synced buffers behind a crossfade.

import { INSTRUMENTS, sharedFor } from "./instruments.js";

export const SAMPLE_RATE = 44100;
export const START_PAD = 0.05; // seconds of silence before beat 0, so the first transient isn't clipped
const WINDOW = 0.5; // seconds of notes scheduled per render pause
const LOOKAHEAD = 0.05;
const MAX_VOICE_TAIL = 4.5; // longest any voice keeps sounding after its note ends
const TARGET_RMS = 0.11;
const PEAK_CEILING = 0.95;

export const beatToTime = (beat, bpm) => START_PAD + (beat * 60) / bpm;
export const timeToBeat = (time, bpm) => ((time - START_PAD) * bpm) / 60;

const COMB_DELAYS = [1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617]; // samples at 44.1 kHz
const ALLPASS_DELAYS = [556, 341];
const STEREO_SPREAD = 23;

/**
 * Freeverb-style reverb built from delay loops: eight damped feedback combs
 * into a short allpass chain per channel. A ConvolverNode sounds a little
 * smoother but costs ~2 s of offline render time per version; this is a
 * fraction of that. Node count is kept low on purpose.
 */
function createReverb(ctx, seconds, damping) {
  const input = ctx.createGain();
  input.channelCount = 1;
  input.channelCountMode = "explicit";
  input.gain.value = 0.125;
  const predelay = ctx.createDelay(0.1);
  predelay.delayTime.automationRate = "k-rate";
  predelay.delayTime.value = 0.012;
  input.connect(predelay);

  // even combs feed both sides in phase, odd combs feed the right side
  // inverted: the two sides hear different sums, which widens the image
  const left = ctx.createGain();
  const right = ctx.createGain();
  const inverted = ctx.createGain();
  inverted.gain.value = -1;
  inverted.connect(right);
  COMB_DELAYS.forEach((samples, i) => {
    const time = samples / 44100;
    const sum = ctx.createGain();
    const delay = ctx.createDelay(0.1);
    delay.delayTime.automationRate = "k-rate";
    delay.delayTime.value = time;
    const damp = ctx.createBiquadFilter();
    damp.type = "lowpass";
    damp.frequency.value = damping;
    damp.Q.value = -3; // dB: no resonant peak, or the loop gain would exceed 1 at the cutoff
    const feedback = ctx.createGain();
    feedback.gain.value = 10 ** ((-3 * time) / seconds); // -60 dB after `seconds`
    predelay.connect(sum).connect(delay).connect(damp).connect(feedback).connect(sum);
    damp.connect(left);
    damp.connect(i % 2 ? inverted : right);
  });

  const allpass = (source, samples, g = 0.5) => {
    const sum = ctx.createGain();
    const delay = ctx.createDelay(0.1);
    delay.delayTime.automationRate = "k-rate";
    delay.delayTime.value = samples / 44100;
    const feedback = ctx.createGain();
    feedback.gain.value = g;
    const direct = ctx.createGain();
    direct.gain.value = -g;
    const out = ctx.createGain();
    source.connect(sum).connect(delay).connect(feedback).connect(sum);
    sum.connect(direct).connect(out);
    delay.connect(out);
    return out;
  };

  const output = ctx.createChannelMerger(2);
  [left, right].forEach((side, channel) => {
    let node = side;
    for (const samples of ALLPASS_DELAYS) node = allpass(node, samples + channel * STEREO_SPREAD);
    node.connect(output, 0, channel);
  });
  return { input, output };
}

function kickTimes(arrangement, bpm) {
  const times = [];
  for (const track of arrangement.tracks) {
    if (track.instrument !== "drums") continue;
    for (const [pitch, start] of track.notes) if (pitch === 36) times.push(beatToTime(start, bpm));
  }
  return times.sort((a, b) => a - b);
}

/** Loudness-match the styles so a comparison isn't won by whichever is loudest. */
function normalize(buffer) {
  let peak = 0;
  let sum = 0;
  let count = 0;
  for (let ch = 0; ch < buffer.numberOfChannels; ch++) {
    const data = buffer.getChannelData(ch);
    for (let i = 0; i < data.length; i++) {
      const v = Math.abs(data[i]);
      if (v > peak) peak = v;
      if (v > 0.003) {
        sum += v * v;
        count++;
      }
    }
  }
  if (!count) return buffer;
  const scale = Math.min(TARGET_RMS / Math.sqrt(sum / count), PEAK_CEILING / peak);
  for (let ch = 0; ch < buffer.numberOfChannels; ch++) {
    const data = buffer.getChannelData(ch);
    for (let i = 0; i < data.length; i++) data[i] *= scale;
  }
  return buffer;
}

/**
 * @param arrangement one entry of project.arrangements
 * @param project     needs bpm and total_beats
 * @param muted       Set of track ids to leave out
 */
export async function renderArrangement(arrangement, project, muted = new Set()) {
  const { bpm, total_beats: totalBeats } = project;
  const spb = 60 / bpm;
  const reverb = arrangement.reverb;
  const length = START_PAD + totalBeats * spb + reverb.seconds + 0.4;
  const ctx = new OfflineAudioContext(2, Math.ceil(length * SAMPLE_RATE), SAMPLE_RATE);
  const shared = sharedFor(ctx);

  const master = ctx.createDynamicsCompressor();
  master.threshold.value = -16;
  master.knee.value = 12;
  master.ratio.value = 3;
  master.attack.value = 0.01;
  master.release.value = 0.2;
  master.connect(ctx.destination);

  const room = createReverb(ctx, reverb.seconds, reverb.damping);
  const reverbReturn = ctx.createGain();
  reverbReturn.gain.value = reverb.level;
  room.output.connect(reverbReturn).connect(master);

  const kicks = kickTimes(arrangement, bpm);
  const events = [];

  for (const track of arrangement.tracks) {
    if (muted.has(track.id)) continue;
    const play = INSTRUMENTS[track.instrument];
    if (!play) {
      console.warn(`No voice for instrument "${track.instrument}"`);
      continue;
    }

    const input = ctx.createGain();
    const bus = ctx.createGain();
    bus.gain.value = track.gain;
    const panner = ctx.createStereoPanner();
    panner.pan.value = track.pan;
    const send = ctx.createGain();
    send.gain.value = track.reverb;
    input.connect(bus).connect(panner).connect(master);
    panner.connect(send).connect(room.input);

    const { delay, duck } = track.fx || {};
    if (delay) {
      const line = ctx.createDelay(2);
      line.delayTime.automationRate = "k-rate";
      line.delayTime.value = delay.beats * spb;
      const feedback = ctx.createGain();
      feedback.gain.value = delay.feedback;
      const tone = ctx.createBiquadFilter();
      tone.type = "lowpass";
      tone.frequency.value = 3200;
      const wet = ctx.createGain();
      wet.gain.value = delay.mix;
      input.connect(line).connect(tone).connect(feedback).connect(line);
      tone.connect(wet).connect(bus);
    }
    if (duck && kicks.length) {
      // side-chain pump: dip on every kick, recover before the next
      const recover = Math.min(0.3, spb * 0.6);
      let last = -Infinity;
      bus.gain.setValueAtTime(track.gain, 0);
      for (const k of kicks) {
        if (k - last < recover + 0.03) continue;
        bus.gain.setValueAtTime(track.gain, Math.max(0, k - 0.01));
        bus.gain.linearRampToValueAtTime(track.gain * (1 - duck), k);
        bus.gain.linearRampToValueAtTime(track.gain, k + recover);
        last = k;
      }
    }

    for (const [pitch, start, dur, vel] of track.notes) {
      events.push({ play, input, pitch, t: beatToTime(start, bpm), dur: dur * spb, vel: vel / 127 });
    }
  }
  events.sort((a, b) => a.t - b.t);

  // Every node in the graph costs time on every render block, even before it
  // sounds, so notes are created just ahead of the render position and their
  // nodes are detached once they've rung out. This is ~10x faster than
  // scheduling the whole piece up front.
  let next = 0;
  let groups = [];
  const scheduleUntil = (limit, now) => {
    groups = groups.filter((group) => {
      if (group.retireAt > now) return true;
      group.node.disconnect();
      return false;
    });
    const byInput = new Map();
    while (next < events.length && events[next].t < limit) {
      const e = events[next++];
      let group = byInput.get(e.input);
      if (!group) {
        group = { node: ctx.createGain(), retireAt: 0 };
        group.node.connect(e.input);
        byInput.set(e.input, group);
        groups.push(group);
      }
      e.play(ctx, group.node, e.pitch, e.t, e.dur, e.vel, shared);
      group.retireAt = Math.max(group.retireAt, e.t + e.dur + MAX_VOICE_TAIL);
    }
  };

  if (typeof ctx.suspend === "function") {
    scheduleUntil(WINDOW + LOOKAHEAD, 0);
    for (let at = WINDOW; at < length; at += WINDOW) {
      ctx.suspend(at).then(() => {
        try {
          scheduleUntil(at + WINDOW + LOOKAHEAD, at);
        } finally {
          ctx.resume();
        }
      });
    }
  } else {
    scheduleUntil(Infinity, 0); // no offline suspend (Firefox): slower, same result
  }

  return normalize(await ctx.startRendering());
}
