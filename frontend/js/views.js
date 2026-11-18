// Canvas drawing: live pitch trace, piano roll, waveforms.

const NOTE_NAMES = ["C", "C♯", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B"];
export const noteName = (midi) => `${NOTE_NAMES[((Math.round(midi) % 12) + 12) % 12]}${Math.floor(Math.round(midi) / 12) - 1}`;

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/** Size the backing store to the element and return a context scaled to CSS pixels. */
function fit(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
  }
  const g = canvas.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, width, height);
  return { g, width, height };
}

/** Scrolling trace of what the mic is hearing. trace: [{midi|null, rms}] newest last. */
export function drawLive(canvas, trace) {
  const { g, width, height } = fit(canvas);
  const voiced = trace.filter((p) => p.midi != null).map((p) => p.midi);
  if (!voiced.length) return;
  const sorted = [...voiced].sort((a, b) => a - b);
  const mid = sorted[sorted.length >> 1];
  const lo = mid - 10;
  const hi = mid + 10;
  const stepX = 4;
  const start = width - trace.length * stepX;
  g.fillStyle = css("--accent");
  trace.forEach((p, i) => {
    if (p.midi == null) return;
    const x = start + i * stepX;
    if (x < 0) return;
    const y = height - ((p.midi - lo) / (hi - lo)) * height;
    g.globalAlpha = 0.25 + 0.75 * (i / trace.length);
    g.beginPath();
    g.arc(x, Math.min(height - 3, Math.max(3, y)), 2.2, 0, Math.PI * 2);
    g.fill();
  });
  g.globalAlpha = 1;
}

/** Map the raw (sung) timeline onto the quantised beat grid, note by note. */
function timeWarp(project) {
  const xs = [];
  const ys = [];
  project.raw_notes.forEach((raw, i) => {
    const note = project.melody[i];
    if (!note) return;
    xs.push(raw.start, raw.end);
    ys.push(note[1], note[1] + note[2]);
  });
  return (t) => {
    if (!xs.length) return 0;
    if (t <= xs[0]) return ys[0] - ((xs[0] - t) * project.bpm) / 60;
    for (let i = 1; i < xs.length; i++) {
      if (t <= xs[i]) {
        const span = xs[i] - xs[i - 1] || 1e-6;
        return ys[i - 1] + ((t - xs[i - 1]) / span) * (ys[i] - ys[i - 1]);
      }
    }
    return ys[ys.length - 1] + ((t - xs[xs.length - 1]) * project.bpm) / 60;
  };
}

/** Piano roll: quantised notes, the sung pitch contour behind them, chords along the top. */
export function drawRoll(canvas, project, playheadBeat, color) {
  const { g, width, height } = fit(canvas);
  const { melody, chords, total_beats: total, beats_per_bar: bpb } = project;
  const top = 26;
  const bottom = height - 8;
  const pitches = melody.map((n) => n[0]);
  const lo = Math.min(...pitches) - 2;
  const hi = Math.max(...pitches) + 2;
  const rowH = (bottom - top) / (hi - lo + 1);
  const x = (beat) => (beat / total) * width;
  const y = (pitch) => bottom - (pitch - lo + 0.5) * rowH;

  // bars and beats
  for (let beat = 0; beat <= total; beat++) {
    const isBar = beat % bpb === 0;
    g.strokeStyle = css(isBar ? "--line-strong" : "--line");
    g.globalAlpha = isBar ? 1 : 0.5;
    g.beginPath();
    g.moveTo(Math.round(x(beat)) + 0.5, isBar ? 0 : top);
    g.lineTo(Math.round(x(beat)) + 0.5, height);
    g.stroke();
  }
  g.globalAlpha = 1;

  // chord names
  g.font = `600 12px ${css("--font-ui")}`;
  g.textBaseline = "middle";
  g.fillStyle = css("--muted");
  let lastName = null;
  for (const chord of chords) {
    if (chord.name !== lastName || chord.start % bpb === 0) g.fillText(chord.name, x(chord.start) + 6, 13);
    lastName = chord.name;
  }

  // what was actually sung
  const warp = timeWarp(project);
  g.strokeStyle = css("--muted");
  g.globalAlpha = 0.55;
  g.lineWidth = 1.2;
  g.beginPath();
  let pen = false;
  project.contour.t.forEach((t, i) => {
    const m = project.contour.midi[i];
    if (m == null) {
      pen = false;
      return;
    }
    const px = x(warp(t));
    const py = y(m);
    if (pen) g.lineTo(px, py);
    else g.moveTo(px, py);
    pen = true;
  });
  g.stroke();
  g.globalAlpha = 1;
  g.lineWidth = 1;

  // quantised notes
  const noteH = Math.max(4, Math.min(14, rowH * 0.9));
  for (const [pitch, start, dur] of melody) {
    const played = playheadBeat != null && playheadBeat >= start && playheadBeat < start + dur;
    g.fillStyle = color;
    g.globalAlpha = played ? 1 : 0.78;
    const w = Math.max(3, x(start + dur) - x(start) - 2);
    g.beginPath();
    g.roundRect(x(start) + 1, y(pitch) - noteH / 2, w, noteH, 3);
    g.fill();
    if (played) {
      g.globalAlpha = 1;
      g.fillStyle = css("--text");
      g.font = `600 11px ${css("--font-ui")}`;
      g.fillText(noteName(pitch), x(start) + 2, y(pitch) - noteH / 2 - 8);
    }
  }
  g.globalAlpha = 1;

  if (playheadBeat != null && playheadBeat >= 0 && playheadBeat <= total) {
    g.fillStyle = css("--text");
    g.fillRect(x(playheadBeat) - 0.75, 0, 1.5, height);
  }
}

/** Reduce a buffer to `count` peak values (0..1) for drawing. */
export function computePeaks(buffer, count = 160) {
  const peaks = new Float32Array(count);
  const size = Math.floor(buffer.length / count);
  const left = buffer.getChannelData(0);
  const right = buffer.numberOfChannels > 1 ? buffer.getChannelData(1) : left;
  let max = 1e-6;
  for (let i = 0; i < count; i++) {
    let peak = 0;
    for (let j = i * size; j < (i + 1) * size; j += 8) {
      const v = Math.max(Math.abs(left[j]), Math.abs(right[j]));
      if (v > peak) peak = v;
    }
    peaks[i] = peak;
    if (peak > max) max = peak;
  }
  for (let i = 0; i < count; i++) peaks[i] /= max;
  return peaks;
}

export function drawWave(canvas, peaks, progress, color, active) {
  const { g, width, height } = fit(canvas);
  if (!peaks) return;
  const gap = 1.5;
  const barW = width / peaks.length - gap;
  const mid = height / 2;
  for (let i = 0; i < peaks.length; i++) {
    const h = Math.max(1.5, peaks[i] * (height - 4));
    const played = i / peaks.length < progress;
    g.fillStyle = played && active ? color : css("--wave");
    g.globalAlpha = active ? 1 : 0.7;
    g.beginPath();
    g.roundRect(i * (barW + gap), mid - h / 2, Math.max(1, barW), h, 1);
    g.fill();
  }
  g.globalAlpha = 1;
}
