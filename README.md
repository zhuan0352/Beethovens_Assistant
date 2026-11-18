# Beethoven Assistant

Hum a melody idea, get it back as a produced track in four arrangements — piano,
guitar, synth and orchestral — and switch between them mid-playback to compare.

## Run it

```bash
./run.sh
```

Then open <http://localhost:8377>. The first run creates `.venv` and installs
the dependencies (Python 3.10+; no Node, ffmpeg or soundfonts needed).

Record with the big button, upload an audio file, or press **Try a demo hum**.
Recording needs a browser that allows microphone access on `localhost`.

Tests: `.venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest`

## Deploy (public URL)

`render.yaml` deploys the app to [Render](https://render.com)'s free plan:

1. Push this folder to a GitHub repository.
2. On Render: **New → Blueprint**, pick the repository, **Apply**.
3. A few minutes later it's live at `https://beethoven-assistant.onrender.com`
   (or similar). HTTPS is included, which browsers require for the microphone.

Free-plan caveats: the service sleeps after 15 minutes idle (the first visit
then takes ~30–60 s), and its disk is wiped on every redeploy or restart, so
shared project links (`#id`) don't last. The **Try a demo hum** button always
works. Env vars: `MAX_PROJECTS` (default 200, oldest pruned) and `DATA_DIR`.

## How it works

```
 browser                               server (FastAPI + NumPy)
 ───────                               ────────────────────────
 mic / file ─► 16 kHz mono WAV ──────► pitch tracking (YIN)
                                       note segmentation + tuning correction
                                       tempo estimation + quantisation
                                       key detection + chord selection
                                       four arrangers (piano / guitar / synth / orchestral)
 Web Audio renders each     ◄──────── note data (JSON) · MIDI export
 arrangement to a buffer
 synced playback + crossfade
```

The server turns audio into **notes**; the browser turns notes into **sound**.
Every version is pre-rendered, so switching is a crossfade between buffers that
are already playing in sync — it never loses your place.

### Pipeline (`backend/pipeline/`)

| Stage | File | What it does |
| --- | --- | --- |
| Pitch | `pitch.py` | YIN pitch tracker, vectorised with an FFT cross-correlation |
| Notes | `notes.py` | Voicing gate, splits on pitch change / silence / loudness dips, folds slides into the note they lead to, corrects for a singer who isn't at A440 |
| Rhythm | `rhythm.py` | Picks the tempo whose beat grid best explains the onsets, then snaps notes to a 16th-note grid |
| Harmony | `harmony.py` | Krumhansl key profiles; Viterbi search over diatonic chords, one or two per bar |
| Arrange | `arrange/*.py` | One module per style: same melody and chords, different instruments and patterns |
| Export | `midi.py` | Standard MIDI file writer |

`transcribe()` is the expensive audio step and runs once per take.
`arrange_project()` is cheap, so changing tempo, key or metre in the UI re-runs
only that.

### Audio engine (`frontend/js/engine/`)

All sound is synthesised in the browser — Karplus-Strong plucked strings,
oscillator-and-filter voices for piano, strings, winds and synths, noise-based
drums, a delay-network reverb, and a bus compressor. `render.js` renders an
arrangement offline; `player.js` plays the results in lockstep.

### API

| Method | Path | |
| --- | --- | --- |
| `POST` | `/api/projects` | Upload a take (`audio`: PCM WAV) → full project |
| `GET` | `/api/projects/{id}` | Fetch a project |
| `POST` | `/api/projects/{id}/rearrange` | `{bpm, key: {tonic, mode}, beats_per_bar}` — `null` means "use detected" |
| `GET` | `/api/projects/{id}/midi/{style}` | Download one version as `.mid` |
| `GET` | `/api/projects/{id}/audio` | The original take |
| `GET` | `/api/styles` | Available styles |
| `GET` | `/api/demo/hum.wav` | A synthetic hummed melody |

Interactive docs are at `/docs`. Projects are stored under `data/projects/`.

## Adding a style

1. Add `backend/pipeline/arrange/<style>.py` with `META` and `arrange(ctx) -> list[Track]`
   and register it in `arrange/__init__.py`.
2. If it uses a new `instrument` name, add a voice for it in
   `frontend/js/engine/instruments.js`.
3. Add a `--<style>` colour in `frontend/styles.css`.

## Known limits

- One voice at a time: it transcribes a single hummed, sung or whistled line.
- Very fast notes (16ths with slides between them) can be merged into their neighbours.
- The melody is assumed to start on a downbeat in 4/4 unless you switch to 3/4;
  if the tempo comes out at half or double speed, use the ½× / 2× buttons.
- Instruments are synthesised, not sampled. Export MIDI to use your own sounds in a DAW.
