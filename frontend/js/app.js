import * as api from "./api.js";
import { MAX_TAKE_SECONDS, Recorder, decodeAudio, encodeWav, toAnalysisWav } from "./audio-io.js";
import { beatToTime, renderArrangement, timeToBeat } from "./engine/render.js";
import { Player } from "./player.js";
import { computePeaks, drawLive, drawRoll, drawWave, noteName } from "./views.js";

const $ = (id) => document.getElementById(id);
const el = {
  capture: $("capture"), working: $("working"), workspace: $("workspace"), transport: $("transport"),
  rec: $("rec"), recStatus: $("rec-status"), live: $("live"), upload: $("upload"), file: $("file"),
  demo: $("demo"), captureError: $("capture-error"), workingTitle: $("working-title"), steps: $("steps"),
  newIdea: $("new-idea"), brand: $("brand"), facts: $("melody-facts"), playHum: $("play-hum"),
  key: $("key"), bpm: $("bpm"), tempoHalf: $("tempo-half"), tempoDouble: $("tempo-double"),
  reset: $("reset-settings"), roll: $("roll"), workspaceError: $("workspace-error"),
  versions: $("versions"), play: $("play"), playIcon: $("play-icon"), nowStyle: $("now-style"),
  time: $("time"), seek: $("seek"), loop: $("loop"),
};
const metreButtons = [...document.querySelectorAll("[data-bpb]")];

const MAX_RECORD_SECONDS = 60;
const KEY_NAMES = {
  major: ["C", "D♭", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B"],
  minor: ["C", "C♯", "D", "E♭", "E", "F", "F♯", "G", "G♯", "A", "B♭", "B"],
};
const PLAY_PATH = "M7 4.5v15l13-7.5z";
const PAUSE_PATH = "M6.5 4.5h4v15h-4zM13.5 4.5h4v15h-4z";

const player = new Player();
const state = {
  project: null,
  hum: null, // AudioBuffer of the original take
  peaks: {},
  muted: {}, // style id -> Set of muted track ids
  busy: false,
  seeking: false,
  dirty: true,
};

const styleIds = () => Object.keys(state.project?.arrangements ?? {});
const styleColor = (id) => getComputedStyle(document.documentElement).getPropertyValue(`--${id}`).trim() || "#e8b25e";
const fmtTime = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

// ---------------------------------------------------------------- screens

function show(screen) {
  el.capture.hidden = screen !== "capture";
  el.working.hidden = screen !== "working";
  el.workspace.hidden = screen !== "workspace";
  el.transport.hidden = screen !== "workspace";
  el.newIdea.hidden = screen !== "workspace";
  document.body.dataset.screen = screen;
}

function showError(target, message) {
  target.textContent = message ?? "";
  target.hidden = !message;
}

function showSteps(title, labels) {
  el.workingTitle.textContent = title;
  el.steps.innerHTML = "";
  for (const label of labels) {
    const li = document.createElement("li");
    li.textContent = label;
    el.steps.append(li);
  }
  show("working");
}

function setStep(index, stepState) {
  const li = el.steps.children[index];
  if (li) li.dataset.state = stepState;
}

// ---------------------------------------------------------------- capture

const recorder = new Recorder();
let recording = null; // { startedAt, trace, frame }

async function startRecording() {
  showError(el.captureError, null);
  el.rec.disabled = true;
  try {
    await recorder.start();
  } catch (err) {
    showError(el.captureError, err.message);
    return;
  } finally {
    el.rec.disabled = false;
  }
  recording = { startedAt: performance.now(), trace: [] };
  el.rec.setAttribute("aria-pressed", "true");
  el.rec.setAttribute("aria-label", "Stop recording");
  el.capture.classList.add("is-recording");

  const loop = () => {
    if (!recording) return;
    const { rms, midi } = recorder.probe();
    recording.trace.push({ rms, midi });
    if (recording.trace.length > 400) recording.trace.shift();
    drawLive(el.live, recording.trace);
    el.rec.style.setProperty("--level", Math.min(1, rms * 6).toFixed(3));
    const seconds = (performance.now() - recording.startedAt) / 1000;
    el.recStatus.textContent = `${fmtTime(seconds)} · ${midi != null ? noteName(midi) : "listening…"} — press again when you're done`;
    if (seconds >= MAX_RECORD_SECONDS) stopRecording();
    else recording.frame = requestAnimationFrame(loop);
  };
  loop();
}

async function stopRecording() {
  if (!recording) return;
  cancelAnimationFrame(recording.frame);
  recording = null;
  el.rec.setAttribute("aria-pressed", "false");
  el.rec.setAttribute("aria-label", "Start recording");
  el.rec.style.removeProperty("--level");
  el.capture.classList.remove("is-recording");
  el.recStatus.textContent = "Press to record. 5–30 seconds of humming works best.";
  try {
    await handleTake(await recorder.stop());
  } catch (err) {
    backToCapture(err.message);
  }
}

function backToCapture(message) {
  show("capture");
  showError(el.captureError, message);
}

async function handleTake(buffer) {
  if (buffer.duration > MAX_TAKE_SECONDS) {
    return backToCapture(`That take is ${Math.round(buffer.duration)} seconds long. Keep it under ${MAX_TAKE_SECONDS}.`);
  }
  if (buffer.duration < 1.5) {
    return backToCapture("That take is very short. Hum for at least a couple of seconds.");
  }
  state.hum = buffer;
  state.muted = {};
  beginWorking("Listening to your take…");
  try {
    const project = await api.createProject(await toAnalysisWav(buffer));
    await openProject(project);
  } catch (err) {
    backToCapture(err.message);
  }
}

function beginWorking(title) {
  showSteps(title, [
    "Transcribing your melody — notes, tempo, key and chords",
    "Producing the piano version",
    "Producing the guitar version",
    "Producing the synth version",
    "Producing the orchestral version",
  ]);
  setStep(0, "active");
}

// ---------------------------------------------------------------- project

/** Render every style at once; each OfflineAudioContext runs on its own thread. */
async function renderAll(project, onDone) {
  const ids = Object.keys(project.arrangements);
  const rendered = await Promise.all(
    ids.map(async (id, i) => {
      const buffer = await renderArrangement(project.arrangements[id], project, state.muted[id]);
      onDone?.(i);
      return [id, buffer];
    }),
  );
  return Object.fromEntries(rendered);
}

function adopt(project, buffers) {
  state.project = project;
  state.peaks = Object.fromEntries(Object.entries(buffers).map(([id, b]) => [id, computePeaks(b)]));
  player.setBuffers(buffers);
  if (!player.active) player.select(Object.keys(buffers)[0]);
  history.replaceState(null, "", `#${project.id}`);
  buildWorkspace();
  show("workspace");
  state.dirty = true;
}

async function openProject(project) {
  setStep(0, "done");
  for (let i = 1; i < el.steps.children.length; i++) setStep(i, "active");
  const buffers = await renderAll(project, (i) => setStep(1 + i, "done"));
  player.pause();
  player.seek(0);
  adopt(project, buffers);
}

/** Re-run arrangement with new settings, keeping the listener's place in the music. */
async function applySettings(settings) {
  if (state.busy) return;
  const before = state.project;
  const beat = timeToBeat(player.position, before.bpm);
  setBusy(true);
  showError(el.workspaceError, null);
  try {
    const project = await api.rearrange(before.id, settings);
    const buffers = await renderAll(project);
    adopt(project, buffers);
    player.seek(Math.max(0, beatToTime(Math.min(beat, project.total_beats), project.bpm)));
  } catch (err) {
    showError(el.workspaceError, err.message);
    buildWorkspace();
  } finally {
    setBusy(false);
  }
}

function setBusy(busy) {
  state.busy = busy;
  el.workspace.classList.toggle("is-busy", busy);
  for (const control of [el.key, el.bpm, el.tempoHalf, el.tempoDouble, el.reset, ...metreButtons]) control.disabled = busy;
}

function currentSettings() {
  const s = state.project.settings ?? {};
  return { bpm: s.bpm ?? null, key: s.key ?? null, beats_per_bar: s.beats_per_bar ?? 4 };
}

// ---------------------------------------------------------------- workspace UI

function buildWorkspace() {
  const p = state.project;
  const settings = currentSettings();

  const cents = Math.round(p.tuning_cents);
  const tuning =
    Math.abs(cents) < 10
      ? "sung right on concert pitch"
      : `sung ${Math.abs(cents)} cents ${cents > 0 ? "sharp" : "flat"} — corrected`;
  el.facts.textContent = `${p.melody.length} notes over ${Math.round(p.total_beats / p.beats_per_bar) - 1} bars · ${tuning}`;

  el.key.innerHTML = "";
  for (const mode of ["major", "minor"]) {
    const group = document.createElement("optgroup");
    group.label = mode === "major" ? "Major" : "Minor";
    KEY_NAMES[mode].forEach((name, tonic) => {
      const option = document.createElement("option");
      option.value = `${tonic}:${mode}`;
      const detected = p.detected_key.tonic === tonic && p.detected_key.mode === mode;
      option.textContent = `${name} ${mode}${detected ? " (detected)" : ""}`;
      group.append(option);
    });
    el.key.append(group);
  }
  el.key.value = `${p.key.tonic}:${p.key.mode}`;
  el.bpm.value = Math.round(p.bpm);
  metreButtons.forEach((b) => b.setAttribute("aria-pressed", String(Number(b.dataset.bpb) === p.beats_per_bar)));
  el.reset.hidden = settings.bpm == null && settings.key == null && settings.beats_per_bar === 4;

  el.versions.innerHTML = "";
  styleIds().forEach((id, index) => {
    const arrangement = p.arrangements[id];
    const muted = state.muted[id] ?? new Set();
    const card = document.createElement("article");
    card.className = "card";
    card.dataset.style = id;
    card.style.setProperty("--c", `var(--${id})`);
    card.innerHTML = `
      <button class="card-main" aria-pressed="false">
        <span class="card-top"><span class="num">${index + 1}</span><span class="name"></span><span class="playing-dot" aria-hidden="true"></span></span>
        <span class="tag"></span>
        <canvas class="wave" aria-hidden="true"></canvas>
      </button>
      <div class="tracks" role="group" aria-label="Instruments"></div>
      <div class="card-actions">
        <button class="link" data-download="wav">Download WAV</button>
        <a class="link" download></a>
      </div>`;
    card.querySelector(".name").textContent = arrangement.name;
    card.querySelector(".tag").textContent = arrangement.tagline;
    card.querySelector(".card-main").addEventListener("click", () => {
      player.select(id);
      player.play();
    });

    const tracks = card.querySelector(".tracks");
    for (const track of arrangement.tracks) {
      const chip = document.createElement("button");
      chip.className = "track";
      chip.textContent = track.name;
      chip.title = `Mute or unmute ${track.name}`;
      chip.setAttribute("aria-pressed", String(!muted.has(track.id)));
      chip.addEventListener("click", () => toggleTrack(id, track.id));
      tracks.append(chip);
    }

    card.querySelector("[data-download]").addEventListener("click", () => downloadWav(id));
    const midi = card.querySelector("a.link");
    midi.href = api.midiUrl(p.id, id);
    midi.textContent = "Download MIDI";
    el.versions.append(card);
  });
  syncPlayerUi();
}

async function toggleTrack(style, trackId) {
  if (state.busy) return;
  const muted = (state.muted[style] ??= new Set());
  if (muted.has(trackId)) muted.delete(trackId);
  else muted.add(trackId);
  const card = el.versions.querySelector(`[data-style="${style}"]`);
  card.classList.add("is-rendering");
  buildTrackStates(card, style);
  try {
    const p = state.project;
    const buffer = await renderArrangement(p.arrangements[style], p, muted);
    state.peaks[style] = computePeaks(buffer);
    player.setBuffer(style, buffer);
  } finally {
    card.classList.remove("is-rendering");
    state.dirty = true;
  }
}

function buildTrackStates(card, style) {
  const muted = state.muted[style] ?? new Set();
  const tracks = state.project.arrangements[style].tracks;
  [...card.querySelectorAll(".track")].forEach((chip, i) => {
    chip.setAttribute("aria-pressed", String(!muted.has(tracks[i].id)));
  });
}

function downloadWav(style) {
  const buffer = player.buffers[style];
  if (!buffer) return;
  const url = URL.createObjectURL(encodeWav(buffer));
  const a = document.createElement("a");
  a.href = url;
  a.download = `beethoven-${style}.wav`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

function syncPlayerUi() {
  for (const card of el.versions.children) {
    const active = card.dataset.style === player.active;
    card.classList.toggle("is-active", active);
    card.classList.toggle("is-playing", active && player.playing);
    card.querySelector(".card-main").setAttribute("aria-pressed", String(active));
  }
  const arrangement = state.project?.arrangements[player.active];
  el.nowStyle.textContent = arrangement?.name ?? "";
  el.transport.style.setProperty("--c", `var(--${player.active})`);
  el.playIcon.setAttribute("d", player.playing ? PAUSE_PATH : PLAY_PATH);
  el.play.setAttribute("aria-label", player.playing ? "Pause" : "Play");
  state.dirty = true;
}

function frame() {
  player.tick();
  if (state.project && !el.workspace.hidden && (player.playing || state.dirty)) {
    const position = player.position;
    const duration = player.duration || 1;
    const progress = position / duration;
    el.time.textContent = `${fmtTime(position)} / ${fmtTime(duration)}`;
    if (!state.seeking) el.seek.value = String(Math.round(progress * 1000));
    el.seek.style.setProperty("--progress", `${progress * 100}%`);
    const color = styleColor(player.active);
    const beat = timeToBeat(position, state.project.bpm);
    drawRoll(el.roll, state.project, position > 0 || player.playing ? beat : null, color);
    for (const card of el.versions.children) {
      const id = card.dataset.style;
      drawWave(card.querySelector(".wave"), state.peaks[id], progress, styleColor(id), id === player.active);
    }
    state.dirty = false;
  }
  requestAnimationFrame(frame);
}

// ---------------------------------------------------------------- events

el.rec.addEventListener("click", () => (recording ? stopRecording() : startRecording()));

el.upload.addEventListener("click", () => el.file.click());
el.file.addEventListener("change", async () => {
  const file = el.file.files[0];
  el.file.value = "";
  if (!file) return;
  showError(el.captureError, null);
  try {
    await handleTake(await decodeAudio(await file.arrayBuffer()));
  } catch (err) {
    backToCapture(err.message);
  }
});

el.demo.addEventListener("click", async () => {
  showError(el.captureError, null);
  el.demo.disabled = true;
  try {
    await handleTake(await decodeAudio(await api.fetchAudio("/api/demo/hum.wav")));
  } catch (err) {
    backToCapture(err.message);
  } finally {
    el.demo.disabled = false;
  }
});

function startOver(event) {
  event?.preventDefault();
  if (recording) return;
  player.pause();
  player.stopSolo();
  player.setBuffers({});
  player.active = null;
  state.project = null;
  state.hum = null;
  history.replaceState(null, "", location.pathname);
  showError(el.captureError, null);
  show("capture");
}
el.newIdea.addEventListener("click", startOver);
el.brand.addEventListener("click", startOver);

el.key.addEventListener("change", () => {
  const [tonic, mode] = el.key.value.split(":");
  const detected = state.project.detected_key;
  const same = Number(tonic) === detected.tonic && mode === detected.mode;
  applySettings({ ...currentSettings(), key: same ? null : { tonic: Number(tonic), mode } });
});

function setTempo(bpm) {
  const clamped = Math.min(240, Math.max(40, Math.round(bpm)));
  if (!Number.isFinite(clamped) || clamped === Math.round(state.project.bpm)) {
    el.bpm.value = Math.round(state.project.bpm);
    return;
  }
  applySettings({ ...currentSettings(), bpm: clamped });
}
el.bpm.addEventListener("change", () => setTempo(Number(el.bpm.value)));
el.tempoHalf.addEventListener("click", () => setTempo(state.project.bpm / 2));
el.tempoDouble.addEventListener("click", () => setTempo(state.project.bpm * 2));

metreButtons.forEach((button) =>
  button.addEventListener("click", () => {
    const bpb = Number(button.dataset.bpb);
    if (bpb !== state.project.beats_per_bar) applySettings({ ...currentSettings(), beats_per_bar: bpb });
  }),
);
el.reset.addEventListener("click", () => applySettings({ bpm: null, key: null, beats_per_bar: 4 }));

el.playHum.addEventListener("click", async () => {
  if (player.solo) return player.stopSolo();
  try {
    state.hum ??= await decodeAudio(await api.fetchAudio(`/api/projects/${state.project.id}/audio`));
  } catch (err) {
    return showError(el.workspaceError, err.message);
  }
  el.playHum.setAttribute("aria-pressed", "true");
  el.playHum.textContent = "Stop";
  player.playSolo(state.hum, () => {
    el.playHum.setAttribute("aria-pressed", "false");
    el.playHum.textContent = "Play my hum";
  });
});

el.roll.addEventListener("click", (event) => {
  const rect = el.roll.getBoundingClientRect();
  const beat = ((event.clientX - rect.left) / rect.width) * state.project.total_beats;
  player.seek(beatToTime(beat, state.project.bpm));
});

el.play.addEventListener("click", () => player.toggle());
el.loop.addEventListener("click", () => {
  player.loop = !player.loop;
  el.loop.setAttribute("aria-pressed", String(player.loop));
});
el.seek.addEventListener("input", () => {
  state.seeking = true;
  el.seek.style.setProperty("--progress", `${el.seek.value / 10}%`);
});
el.seek.addEventListener("change", () => {
  state.seeking = false;
  player.seek((el.seek.value / 1000) * player.duration);
});

player.addEventListener("change", syncPlayerUi);
window.addEventListener("resize", () => (state.dirty = true));

document.addEventListener("keydown", (event) => {
  if (el.workspace.hidden || event.metaKey || event.ctrlKey || event.altKey) return;
  const tag = event.target.tagName;
  if (tag === "INPUT" && event.target.type !== "range") return;
  if (tag === "SELECT" || tag === "TEXTAREA") return;
  const index = Number(event.key) - 1;
  if (index >= 0 && index < styleIds().length) {
    player.select(styleIds()[index]);
  } else if (event.code === "Space" && tag !== "BUTTON" && tag !== "A") {
    event.preventDefault();
    player.toggle();
  }
});

// ---------------------------------------------------------------- start

/** Open the project named in the URL (#<id>), so ideas can be bookmarked and shared. */
async function openFromHash() {
  const id = location.hash.slice(1);
  if (!/^[0-9a-f]{12}$/.test(id) || id === state.project?.id || recording) return;
  try {
    player.pause();
    state.hum = null;
    state.muted = {};
    beginWorking("Opening your idea…");
    await openProject(await api.getProject(id));
  } catch {
    history.replaceState(null, "", location.pathname);
    show("capture");
  }
}

show("capture");
requestAnimationFrame(frame);
window.addEventListener("hashchange", openFromHash);
openFromHash();
