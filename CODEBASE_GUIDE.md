# Prism Codebase Guide

A walkthrough of how Prism works, written as the journey of your voice through
the code: from the moment the mic hands over samples to the moment Discord
hears the cleaned result.

---

## Part 1: The big picture

Prism is real-time audio middleware. Normally apps hear your mic directly.
With Prism, the audio takes a detour:

```
your mic → Prism cleans it up → fake "cable" mic → Discord / Zoom / OBS
```

The "fake mic" comes from VB-Audio Virtual Cable, a driver that creates two
connected Windows devices:

- **CABLE Input**: looks like a speaker. Prism plays its cleaned audio into it.
- **CABLE Output**: looks like a microphone. Other apps pick this as their mic.

Whatever goes into CABLE Input comes out of CABLE Output. So Prism just reads
from your real mic, cleans the sound, and "plays" it into CABLE Input. That's
the whole trick.

### How real-time audio works: callbacks and blocks

Real-time audio is not "read the whole recording, process it, play it."
Audio arrives continuously, so you process it in tiny chunks called **blocks**.

Prism uses `sounddevice`, a Python wrapper around **PortAudio** (a
cross-platform audio I/O library). PortAudio runs a **callback**: a function
it calls every time a new block of mic samples is ready. The numbers
(`prism/config.py`):

- `SAMPLERATE = 48000` — 48,000 samples per second
- `BLOCKSIZE = 480` — each block is 480 samples, exactly **10 milliseconds**

So 100 times a second, PortAudio hands Prism 10ms of mic audio and says
"fill in what should go to the cable." That per-block design is why latency
stays low: Prism never holds more than a few blocks of audio at any moment.

Why 48kHz and 480 specifically? RNNoise (the AI denoiser) is trained on 48kHz
audio and eats fixed 480-sample frames. One block == one RNNoise frame means
no re-buffering between the stream and the model.

**The one hard rule:** the callback runs on a real-time audio thread. If it's
ever slower than 10ms, you get dropouts (crackles, gaps). So the callback
stays trivial: no I/O, no big allocations. All real work lives in pipeline
**stages**.

---

## Part 2: The file relations map

```
                         app.py  (bootstrap: find devices, start engine, open UI)
                            │
            ┌───────────────┼────────────────┐
            ▼               ▼                ▼
     prism/audio.py    prism/ui.py     prism/config.py
     (AudioEngine,     (Tkinter window;  (every tunable number;
      the callback)     polls/sets the    imported by everyone)
            │            engine's flags)
            ▼
     prism/pipeline.py  (Pipeline class + build_default_pipeline)
            │
   ┌────────┼──────────────────────┬─────────────────┐
   ▼        ▼                      ▼                 ▼
highpass.py  rnnoise_denoise.py  deepfilternet.py  noise_gate.py
(scipy)      (ctypes→rnnoise.dll) (onnxruntime)     (numpy)

     prism/meters.py  (NoiseMeter, display only; fed by the callback in audio.py)
```

Arrows are imports. Audio only ever flows through
`audio.py → pipeline.py → the three dsp stages`. Everything else is wiring
or display.

---

## Part 3: Startup (before any audio exists)

`app.py` is a thin bootstrap. It does four things:

```python
cable_index = audio.find_device(config.CABLE_NAME, "output")  # "CABLE Input"
input_index = audio.pick_input_device()                       # a real mic, never CABLE
engine = audio.AudioEngine(cable_index)
engine.start(input_index)
run_ui(engine)
```

1. **Find the cable** by name match. Missing? Print install instructions and
   quit (and show a dialog box in the packaged .exe, which has no console).
2. **Pick a real mic**, explicitly excluding CABLE devices. Using CABLE Output
   as input would make Prism listen to its own output: a feedback loop.
3. **Start the engine** (opens the audio stream).
4. **Open the UI.**

`engine.start()` (`prism/audio.py`) builds the pipeline and opens a **duplex
stream**: one stream that reads from the mic and writes to the cable at the
same time:

```python
stream = sd.Stream(
    device=(input_index, output_index),
    samplerate=config.SAMPLERATE,   # 48000
    blocksize=config.BLOCKSIZE,     # 480 samples = 10 ms
    dtype=config.DTYPE,             # int16
    channels=(1, out_channels),     # mono in, stereo out (the cable)
    callback=callback,
)
```

And `build_default_pipeline()` (`prism/pipeline.py`) assembles the chain in
this exact order:

```python
stages = [HighPassFilter(...)]
if config.DENOISE_ENABLED:
    denoiser = build_denoiser(denoiser_choice)   # RNNoise or DeepFilterNet
    if denoiser is not None:
        stages.append(denoiser)
stages.append(NoiseGate(...))
return Pipeline(stages)
```

So the chain is: **high-pass → AI denoiser → noise gate**.

### The host API trap (Windows-specific)

Windows exposes every audio device through several driver layers (WASAPI, MME,
DirectSound). PortAudio can't open one duplex stream where the input is on one
layer and the output is on another. So the engine checks which layer your mic
came from and finds the cable's entry on that same layer before opening the
stream.

---

## Part 4: The life of one block (this happens 100x per second)

### Step 1: the callback receives a block (`prism/audio.py`)

PortAudio wakes the callback with `indata`: a (480, 1) array of int16 samples
fresh off your mic.

```python
def callback(indata, outdata, frames, time, status):
    self.in_db = _dbfs(indata[:, 0].astype(np.float32) / _INT16_SCALE)
    if self.enabled:
        processed = pipeline.process_int16(indata)
    else:
        processed = indata[:, 0]          # master toggle off: raw passthrough
    outdata[:] = processed[:, None]       # broadcast mono to both cable channels
```

It measures input loudness in **dBFS** (decibels relative to full scale, where
0 is the loudest a digital signal can be and silence is around -80), then
hands the block to the pipeline.

### Step 2: int16 becomes float (`prism/pipeline.py`)

The hardware speaks int16 (whole numbers from -32768 to 32767), but DSP math
wants floats in [-1.0, 1.0]. The pipeline owns that conversion so no stage has
to think about it:

```python
def process_int16(self, indata):
    x = indata[:, 0].astype(np.float32) / _INT16_SCALE   # int16 -> float [-1, 1]
    for stage in self.stages:
        x = stage.process(x)                              # the whole chain
    x = np.clip(x * _INT16_SCALE, -_INT16_SCALE, _INT16_MAX)
    return x.astype(np.int16)                             # float -> int16
```

**That `for` loop is the entire architecture.** Every stage is just an object
with `process(block) -> block`. Adding a future stage (Silero VAD, Demucs)
means appending one more object to the list.

### Step 3: high-pass filter (`prism/dsp/highpass.py`)

A 2nd-order Butterworth high-pass at 90 Hz. "High-pass" means frequencies
above the cutoff pass, everything below gets cut. That kills low rumble (desk
thumps, AC hum, traffic) without touching voices, since speech fundamentals
start around 85 Hz.

The subtle part: the filter is **stateful**. A filter has memory of past
samples, and the block boundary is an artificial cut in a continuous signal,
so the filter state is carried from block to block:

```python
out, self._zi = sosfilt(self.sos, block, zi=self._zi)
```

Without that, every block boundary would click. The state is also seeded with
the first sample so the filter doesn't "thump" at startup.

### Step 4: the AI denoiser (the heart)

Two interchangeable implementations, both following the same contract:
`IS_DENOISER = True`, `.enabled`, `.mix`, `.name`. The engine finds whichever
one is in the pipeline by that marker and doesn't care which it is.

#### Option A: RNNoise (`prism/dsp/rnnoise_denoise.py`) — the light default

RNNoise is a small recurrent neural network with the weights baked into a C
library. Prism binds straight to the DLL with **ctypes** (Python's built-in
way to call C functions), skipping the `pyrnnoise` package's broken Python
wrapper:

```python
lib = ctypes.CDLL(os.path.join(_library_dir(), "rnnoise.dll"))
...
self.speech_prob = float(_lib.rnnoise_process_frame(self._state, ptr, ptr))
```

One call per frame: noisy samples in, cleaned samples out, in place. The
return value is a bonus: the model's guess of "is someone speaking right now"
(0 to 1), which the noise meter uses for free.

RNNoise demands exactly 480-sample frames, and Prism's block size **is** 480,
so normally it's one block in, one frame through, zero added latency. The
stage still has a FIFO (first-in-first-out buffer) so it survives odd block
sizes:

```python
self._pending = np.concatenate((self._pending, block))   # accumulate input
while self._pending.size >= FRAME_SIZE:                  # full frame ready?
    frame, self._pending = self._pending[:480], self._pending[480:]
    self._ready = np.concatenate((self._ready, self._denoise_frame(frame)))
out, self._ready = self._ready[:block.size], self._ready[block.size:]
```

The `mix` knob (the UI strength slider) is a dry/wet blend: mixing the
original signal ("dry") with the processed one ("wet"):

```python
return self.mix * wet + (1.0 - self.mix) * frame
```

Measured: ~1.3 ms per 10 ms block during speech, ~0.55 ms on silence.

#### Option B: DeepFilterNet3 (`prism/dsp/deepfilternet.py`) — stronger, heavier

A much bigger model run through **ONNX Runtime**, a CPU inference engine. The
entire signal chain (FFT analysis, the neural net, resynthesis) lives inside
the exported model graph, so the Python side just feeds one 512-sample frame
and carries the model's 12 recurrent state tensors between calls:

```python
feeds = {_INPUT: frame}
feeds.update(self._states)                 # the model's memory
out = self._sess.run(self._out_names, feeds)
enhanced = out[0]
# stash the returned states for the next frame
```

Two costs versus RNNoise:

1. It wants 512-sample frames while blocks are 480, so the FIFO actually
   buffers here (one frame of delay).
2. The model itself answers ~3 frames late (~32ms of algorithmic latency).

That latency creates a trap for the `mix` slider: blending the live dry signal
with a 32ms-late wet signal would cause comb filtering (a hollow, robotic
phasing sound). So the stage runs the dry signal through a matching 3-frame
delay line so both arrive in sync:

```python
self._dry = np.concatenate((self._dry[FRAME_SIZE:], frame))  # delay line
return self.mix * enhanced + (1.0 - self.mix) * dry
```

Measured: ~5.7 ms per 10.7 ms frame. Real-time, but ~5x RNNoise's CPU.

#### Fallback chain

If onnxruntime or the 13MB model file is missing, the DFN constructor raises
and `build_denoiser()` prints why and falls back to RNNoise. Missing RNNoise
too? It degrades to plain high-pass + gate. Nothing ever hard-crashes the
audio path.

### Step 5: noise gate (`prism/dsp/noise_gate.py`)

Last stage: mute the gaps between words. It measures each block's **RMS**
(root mean square, the standard "average loudness" of a chunk of audio) and
compares against the threshold:

```python
rms = float(np.sqrt(np.mean(block * block)))
if rms >= self.threshold:
    self._hold_left = self.hold_ms        # speech: keep gate open
else:
    self._hold_left = max(0.0, self._hold_left - self.block_ms)
target = 1.0 if self._hold_left > 0.0 else 0.0
```

Three details that make it sound good instead of choppy:

- **It runs after the denoiser, on purpose.** On the cleaned signal the noise
  floor is tiny, so the threshold can sit way down at -45 dBFS and still only
  catch true silence. A gate on the raw mic had to sit above the room noise
  (-25 dBFS) and clipped quiet consonants. (This deliberately reverses the
  original PRD's gate-first order.)
- **Hold time** (200ms): after speech stops, the gate stays open a beat so
  word endings and breath gaps aren't chopped.
- **Ramped gain**: the gate never snaps open or shut. It ramps the volume
  across the block (5ms attack, 150ms release), because an instant gain jump
  is an audible click:

```python
ramp = np.linspace(start_gain, end_gain, block.size, dtype=np.float32)
return block * ramp
```

### Step 6: back to int16, out to the cable

Back in `process_int16`, the float block is clipped to [-1, 1] (so loud peaks
distort gracefully instead of wrapping around into garbage) and converted to
int16. The callback writes it to `outdata`, duplicating mono into both stereo
channels. PortAudio delivers that to **CABLE Input**, the VB-Cable driver
pipes it to **CABLE Output**, and the apps that selected CABLE Output as their
mic hear your cleaned voice.

### One block's life, in one line

```
mic (int16, 480 samples, 10ms)
 → callback (audio.py)
  → /32768 to float
   → HighPassFilter (90Hz, kills rumble)
    → RNNoise or DFN3 (AI removes noise, mix = strength slider)
     → NoiseGate (-45dB, mutes gaps, ramped)
      → clip, ×32768 to int16
       → CABLE Input → CABLE Output → Discord
```

---

## Part 5: How the UI controls a live audio thread (no locks)

Two worlds execute at once: the **audio thread** (PortAudio fires the callback
every 10ms) and the **main thread** (the Tkinter UI). The AudioEngine is the
bridge.

### The concurrency problem

The user drags the strength slider while the callback is mid-block. Normally
you'd reach for a lock (a mutex), but you never want a lock in an audio
callback: if the UI thread holds it when the callback fires, the callback
blocks, misses its 10ms deadline, and you hear a glitch.

Prism's answer: don't lock at all. All the controls are **plain Python floats
and bools** (`enabled`, `denoiser_mix`). In CPython, the GIL (global
interpreter lock) makes a single read or write of a variable atomic, meaning
it can't be seen half-written. The UI writes `engine.enabled = False`, the
callback reads it next block; worst case the change lands 10ms late, which is
imperceptible for a toggle.

Same trick in reverse for the meters: the callback writes `in_db`, `out_db`,
`gate_open` every block, and the UI polls them on a 50ms timer (~20 fps).
They're display-only, so being a frame stale doesn't matter.

### Restart instead of mutate

Switching mics or swapping the denoiser doesn't try to surgically modify a
running stream. It stops the stream and rebuilds the whole pipeline from
scratch: fresh state, no leftover filter memory from the old device. Brief and
deliberate, not a hot path.

### The UI window (`prism/ui.py`)

A stopgap Tkinter panel until the Tauri UI in Phase 5. Everything in it reads
or writes engine flags:

```python
def on_toggle():
    engine.enabled = enabled_var.get()           # master on/off

def on_strength(value):
    engine.denoiser_mix = float(value) / 100.0   # slider 0-100 -> mix 0.0-1.0
```

Details worth knowing:

- **The model picker tells the truth.** After `engine.set_denoiser(...)` it
  reads back `engine.denoiser_name` to see what *actually* loaded. If
  DeepFilterNet fell back to RNNoise, the dropdown snaps back and the status
  line says why.
- **Errors get reported, not raised.** Mic and model switches are wrapped in
  try/except, because a device can vanish mid-session and a UI crash would
  kill the audio too.
- Some Windows host APIs truncate device names to 31 chars, so matching the
  current mic to a dropdown row is done by name prefix, not equality.

---

## Part 6: The noise meter (display only, never touches audio)

While each block passes through, the callback also feeds the `NoiseMeter`
(`prism/meters.py`):

```python
self._meter.update(self.in_db, self.out_db, speech_prob)
```

Two smoothed readings for the UI:

- **`noise_floor_db`**: how loud your room is. Learned only from frames where
  RNNoise's `speech_prob` says nobody's talking, so your voice never inflates
  it. With no VAD available (DeepFilterNet has none, or denoising is off), it
  holds its last estimate rather than guess.
- **`reduction_db`**: input loudness minus output loudness, the "look how much
  junk we're stripping" number.

Both use one-pole smoothing (each block nudges the reading a small step toward
the new value) so they read steady instead of flickering.

---

## Part 7: Tests, scripts, and everything else

### Tests (`tests/test_pipeline.py`) — proof without a microphone

No audio devices needed: they synthesize signals with numpy, stream them
through the pipeline block by block exactly like the callback would, and
assert on the RMS of what comes out. Each check targets one claim the code
makes:

- **High-pass works**: a 40 Hz rumble sine must come out under 30% of its
  input level; a 300 Hz "speech" tone must survive nearly intact.
- **Gate works**: a tiny 0.001-amplitude tone must come out as near silence.
- **Gate hold works**: open the gate with speech, feed a 50ms gap (shorter
  than the 200ms hold), assert the internal gain stayed up; then feed 2
  seconds of silence and assert it closed.
- **RNNoise works**: white noise in, must come out at less than half the level.
- **The block contract holds**: feed blocks of size 480, 1024, and 160 and
  assert every call returns exactly as many samples as it got. This protects
  the FIFO logic in both denoisers, the easiest thing in the repo to silently
  break.
- **Disabled means untouched**: a disabled denoiser must return the input
  bit-for-bit, not "almost the same".
- **The meter is honest**: speech frames must not move the room-noise reading,
  and with no VAD it must hold rather than guess.

Every AI check skips cleanly if its dependency is missing, matching the
runtime's graceful degradation.

Run them: `./venv/Scripts/python.exe -m tests.test_pipeline`

### Model fetch (`scripts/fetch_deepfilternet.py`)

The DFN3 model is a 13 MB binary, not committed to git. The script downloads a
tar from the grazder/DeepFilterNet repo, extracts just the `.onnx` file into
`models/deepfilternet3/`, and exits early if it's already there. Standard
library only, so it runs before onnxruntime is installed.

### The non-Python stuff

- **roadmap.md**: single source of truth for phase statuses. The website
  renders it directly; statuses get updated there, nowhere else.
- **docs/**: the product site, served straight by GitHub Pages with no build
  step. `index.html` is the landing page, `roadmap/` renders roadmap.md live,
  `releases/` pairs GitHub releases with devlog write-ups in `docs/devlog/`,
  and one shared `app.js` figures out which page it's on by which elements
  exist.
- **Packaging**: Windows `.exe` builds via PyInstaller. That's why the code
  has `sys.frozen` checks: frozen builds bundle `rnnoise.dll` at the app root
  instead of inside the pyrnnoise wheel, and startup errors show a dialog box
  since there's no console.
- **requirements.txt / venv**: numpy, scipy, sounddevice are the hard deps.
  pyrnnoise and onnxruntime are optional; everything degrades gracefully
  without them.

---

## Part 8: The complete inventory

| Piece | Job |
|---|---|
| app.py | boot: find devices, start engine, open UI |
| prism/config.py | every tunable number, with the reasoning in comments |
| prism/audio.py | device discovery + AudioEngine + the realtime callback |
| prism/pipeline.py | the stage chain and denoiser selection/fallback |
| prism/dsp/highpass.py | Butterworth high-pass, stateful across blocks |
| prism/dsp/noise_gate.py | RMS gate with attack/release/hold |
| prism/dsp/rnnoise_denoise.py | RNNoise via ctypes, light default |
| prism/dsp/deepfilternet.py | DFN3 via onnxruntime, stronger but heavier |
| prism/meters.py | display-only room-noise and reduction readings |
| prism/ui.py | Tkinter control panel, polls and pokes the engine |
| tests/test_pipeline.py | offline proof of every DSP claim |
| scripts/fetch_deepfilternet.py | one-time model download |
| docs/ + roadmap.md | website and roadmap |

## Part 9: The interview answers (the four ideas to remember)

If you only keep four things from this guide:

1. **The stage pattern.** `Pipeline` is a list of objects with
   `process(block) -> block`. The whole architecture is one `for` loop, and
   every future feature (VAD, voice isolation) is just another stage appended
   to the list.
2. **Lock-free real-time safety.** UI and audio threads share plain
   floats/bools, atomic under the GIL. No locks anywhere near the callback,
   so the 10ms deadline is never blocked.
3. **Gate after denoiser.** A deliberate reversal of the original plan, with
   measured reasoning: gating the cleaned signal lets the threshold sit at
   -45 dBFS and pass soft consonants that a raw-mic gate clipped.
4. **Latency alignment in DeepFilterNet.** The FIFO rechunks 480-sample blocks
   to 512-sample frames, and a 3-frame dry delay line keeps the strength
   slider's dry/wet blend phase-aligned, avoiding comb filtering.
