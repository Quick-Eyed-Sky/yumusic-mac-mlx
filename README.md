# YuMusic — lyrics and a style prompt into a song, on Apple Silicon

**A complete front-end for [YuE2](https://github.com/multimodal-art-projection/YuE) on Apple Silicon, written for people who make music rather than people who write code.**

A style description and lyrics in, a song out. Every control the model
actually accepts — style, lyrics, duration, metre, tempo, seed, and a
harmony the base model normally keeps too safe — is on one page, in plain
English, with the trade-off written **beside** each control instead of
buried in a wiki. Nothing leaves your Mac.

![The interface](docs/screenshot.png)

*One page, live while a batch runs.*

> This is an **unofficial** front-end. It is not made by or affiliated with
> the YuE / m-a-p team. It does not include the model.

**[Installation, step by step, assuming no Terminal experience →](INSTALL.md)**
· [Ce README en français →](README.fr.md)

---

## 🧪 Still experimental

Two controls on this page do not fully deliver what their name promises,
and burying that in a table further down would be dishonest.

**Harmonic daring** pushes the model's own score-planning sampling past the
range it was tuned for. That is the entire mechanism - there is no separate
"safe" version of a daring score underneath it. High settings can, and
sometimes do, produce a score that falls apart rather than one that is
merely surprising. Start low, and treat anything past the middle of the
slider as deliberately unstable, not as a bug report.

**Instrumental does not reliably silence the model.** Ticking it asks YuE2
for no voice at all, and it measurably reduces vocals - but the base model
does not obey the instruction the way a dedicated switch would. Some
renders still come back with singing. Treat a fully instrumental take as a
good outcome this render gave you, not a guarantee the setting makes.

---

## 🤔 Why this exists

YuE2 writes a full musical score — melody and chord symbols, in ABC notation
— before it renders a single sample of audio. That planning stage decides
the harmony, and it ships locked at a conservative setting. Nobody tells you
this either, and it is the reason the model keeps handing back plain,
diatonic triads however adventurous your style prompt reads: the harmony was
already decided, safely, before the words you wrote were ever set to sound.

So the first thing this front-end does is put that setting on the page as
**Harmonic daring** — a slider from the model's own cautious default up to
genuinely unstable — because the words "jazz harmony" or "chromatic" in a
style prompt cannot reach a decision the model makes before it reads them
as anything but style.

The rest of the app follows the same rule as everything else I build this
way: **if a setting does something, say what, in the sentence next to it.**

---

## 🎛️ What it does

**Make a song from a style prompt and lyrics.** English lyrics, section tags
(`[Verse]`, `[Chorus]` and five more) inserted at the cursor with one click,
and an **Instrumental** switch for no voice at all.

**Harmonic daring**, from the model's own safe default up to something that
will genuinely surprise you — see above.

**Restore a track completely.** Every render writes a `.txt` beside it
holding every setting used, and the score itself as a `.abc` file. Drop
**any file belonging to a track** — the `.txt`, the `.wav`, the score — and
every control on the page goes back to what made it. The seed is what
reproduces the composition: leave it alone and raise the duration, and you
get the same piece, longer.

**Batches that stay editable.** Up to 100 tracks, and **everything on the
page stays live while a batch runs** — change the style, the lyrics, the
duration, the metre, the daring, even the model variant, during track 3, and
track 4 obeys. Two things freeze once you press Generate, because they
decide the shape of the run rather than of a track: the **number of
tracks** and the **batch name**.

**Dynamic and sequential prompts**, in both the style and the lyrics:

🎲 **Dynamic.** One option drawn per render, fresh every time:

```
{slow|fast} {piano|guitar} piece, {warm|cold}
```

🔁 **Sequential.** Complete versions separated by a line of three or more
dashes, used one per track, in order, looping if you run past the end:

```
first idea
---
second idea
```

**A local model can draft a style prompt** from a rough idea — *a sad the
cure track, slow, with choir at the end* — through
[Ollama](https://ollama.com) running entirely on your own Mac. Its real job
is not polish but **translating a reference into musical facts**, which is
the one thing a preset library can never do: YuE2 does not know who The
Cure are, but it knows what *post-punk, melancholic male baritone, echoing
guitar, reverb-drenched production* means. Optional; the rest of the app
works without it.

**Sheet music of every track**, the ABC score YuE2 planned before it
rendered a single sample, viewable right on the page.

**A per-run folder**, named by you, holding the audio, the score, the
settings `.txt`, and optionally MP3, FLAC and MIDI copies alongside the
WAV.

**Two buttons for "where did it go?"** — the folder this run is writing
into, from the run bar, and the track you are listening to, selected in the
Finder.

---

## ⚠️ Two things worth knowing before your first render

**1. The model's own duration ceiling is six minutes.** Ask for more than
360 seconds and it simply stops at six.

**2. Instrumental means the lyrics are thrown away, not sent as a
suppression.** Tick it and whatever is in the Lyrics box is ignored in
favour of an explicit instrumental marker — you do not need to clear the
box first, and leaving text in it costs nothing.

---

## 🍏 Requirements

| | |
|---|---|
| **Mac** | Apple Silicon — M1 or later. Intel Macs cannot run this. |
| **Memory** | Not measured below 64 GB here; the model weights are small (4.2 GB for 8-bit), so 16 GB should be workable. |
| **Disk** | ~5 GB for the recommended 8-bit model; up to ~11 GB more if you add bf16 and 4-bit as well. |
| **macOS** | Sonoma (14) or later. |
| **Also needed** | The YuE2-3B-MLX model — [INSTALL.md](INSTALL.md) covers it. |
| **Optional** | [Ollama](https://ollama.com) (style-prompt drafting), ffmpeg is *not* needed — MP3/FLAC export is built in. |

---

## ⏱️ Measured by the model's own authors, on an M-series Mac

I have not run controlled timing here yet — these numbers are from the
[YuE2-3B-MLX](https://huggingface.co/ahmadw/YuE2-3B-MLX) model card itself,
quoted rather than guessed at:

| Variant | Decode speed | Size |
|---|---|---|
| 8-bit (recommended) | ~80 tokens/s with CFG | 4.2 GB |
| bf16 (reference quality) | ~70 tokens/s (~35 with CFG) | 7.0 GB |
| 4-bit (fastest, some drift) | fastest, some quality loss | 3.4 GB |

A three-minute song takes a few minutes end to end on Apple Silicon.

---

## 🎚️ The controls, in order

### The model

**Model variant.** 8-bit is the one to use — near bf16 quality, about twice
as fast to decode. bf16 is the reference and the slowest. 4-bit is fastest
and drifts furthest from the reference. **Only the variants you have
actually downloaded are listed** — the menu adapts to what is on disk.

### Words

**Style prompt.** A list of concrete musical facts separated by commas, not
sentences and not a pile of adjectives — roughly: genre, era or aesthetic,
vocal character, instruments, rhythmic character, harmonic language,
production, approximate tempo, mood. *"Beautiful, emotional, amazing" tells
the model nothing it can play; "restrained female alto, dry close vocal"
tells it exactly what to do.*

**Lyrics**, with section tags inserted at the cursor by seven buttons above
the box, so you can paste lyrics first and tag them afterwards.

**Instrumental** — see the warning above.

### Settings

**Target duration**, in seconds, capped at 360 by the model itself.

**Track seed**, `-1` for a fresh random seed per track, or a fixed number to
reproduce a piece — every track writes its own seed into its `.txt`.

**Walk the seed** — with a fixed seed, add 1 per extra track: same family,
real variation. Ignored while the seed is `-1`, which is already random.

**Metre and tempo** — a metre menu and a tempo number, `0` for "model
decides".

**Rhythmic complexity**, a slider from straightforward to intricate.

**Harmonic daring** — see *Why this exists* above.

**Style strength**, how hard the render is pushed toward the exact wording
of the style and lyrics.

### Advanced

**Score planning** — *melody + chords* is what harmonic daring acts on;
turning it off skips straight to audio and there is then no score to
inspect. **Start from an existing score** hands the app an `.abc` file to
render as-is, which is how a score you edited by hand gets back into the
model. **Audio refinement steps** — leave blank for the model's own default.

### The run

**Number of tracks**, up to 100, each with its own seed, all landing in one
folder named after the **batch name**. **Extra formats** — MP3, FLAC and
MIDI copies alongside the WAV and the `.abc` score, which are always
written.

### Results

The audio player, a button to reveal the playing track in the Finder, the
resolved prompt for the track in progress, the list of files this run has
saved, a running log, and the sheet music of the latest track.

---

## 🔧 Design notes

A few decisions that are deliberate, in case they look like oversights:

- **It calls the model repo's own pipeline rather than reimplementing
  it.** The one thing it adds is exposing the score-planning stage's
  sampling settings — temperature, top-p, top-k — which the model repo's own
  command line does not expose. That is what "Harmonic daring" actually
  turns.
- **Repetition penalty is deliberately not exposed.** ABC notation has to
  repeat bar lines, rests and note letters constantly; penalising repetition
  there would corrupt the notation rather than loosen the harmony.
- **Nothing is uploaded, ever.** No telemetry, no account, no network call
  except the one that downloads the model weights, once.

---

## 📜 Licences and attribution

**This front-end** is MIT — see [LICENSE](LICENSE). Do what you like with
it.

**The model is not included here, and its licence is not MIT.** The
YuE2-3B-MLX weights this app drives are downloaded separately by you, and
they are licensed **CC BY-NC 4.0 — non-commercial** by their original
authors, inherited from [m-a-p/YuE2-3B](https://huggingface.co/m-a-p/YuE2-3B)
and [m-a-p/YuE2-Vae](https://huggingface.co/m-a-p/YuE2-Vae). That is a real
restriction, not a formality: check
[the model card](https://huggingface.co/ahmadw/YuE2-3B-MLX) and the
[original YuE2 project](https://github.com/multimodal-art-projection/YuE)
before using anything you make with this commercially, rather than taking my
word for it — licences change, and this one is already stricter than most.

Nothing in this repository is generated audio, and no audio you make with it
passes through me or anyone else.

---

## 👋 Who made this

Jean-Pascal — **[Quick-Eyed Sky](https://www.youtube.com/@QuickEyedSky)** on
YouTube, [QES](https://huggingface.co/QES) on Hugging Face. Not a
programmer: this exists because the harmony was too safe and I wanted to
open the one setting that actually controls it.

If it saved you an afternoon, you can
[buy me a coffee](https://buymeacoffee.com/oFJ5CiY7n). Entirely optional,
and the project stays exactly as free either way.

---

## 🙏 Thanks

To the [YuE2 / m-a-p team](https://github.com/multimodal-art-projection/YuE)
for the model, and to
[ahmadw](https://huggingface.co/ahmadw/YuE2-3B-MLX) for the native MLX port
that makes it run this way on a Mac at all.
