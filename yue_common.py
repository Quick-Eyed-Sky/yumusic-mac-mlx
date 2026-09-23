"""
yue_common.py -- everything the three YuMusic 2.0 apps share.

In 1.x each app carried its own copy of the prompt resolution, the subprocess
plumbing, the ffmpeg export and the Finder colouring, and they drifted apart.
Here they live once.
"""

import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import gradio as gr

# ---------------------------------------------------------------- identity --
#
# One place to change the version. Every app prints it at the top of its page,
# together with the model it drives, so a screenshot or a log always says
# exactly what produced it.

VERSION = "2.22"
MODEL_FAMILY = "YuE2-3B"
MODEL_REPO = "ahmadw/YuE2-3B-MLX"       # the Apple Silicon build of m-a-p/YuE2-3B
MODEL_UPSTREAM_URL = "https://github.com/multimodal-art-projection/YuE"


def app_header(app_name, tagline):
    return (f"## {app_name} {VERSION}\n"
            f"{tagline}\n\n"
            f"Model: **{MODEL_FAMILY}** - Apple Silicon build (`{MODEL_REPO}`) of "
            f"[YuE2]({MODEL_UPSTREAM_URL}), quantization chosen below.")


# ------------------------------------------------------------------ paths --

# Set YUE_REPO in the launcher to keep the weights somewhere else - an external
# drive, say. Nothing else in the apps knows where the model lives.
REPO_DIR = Path(os.environ.get("YUE_REPO",
                               Path.home() / "YuE" / "YuE2-3B-MLX")).expanduser()
V2_DIR = Path(__file__).resolve().parent
RUNNER = V2_DIR / "yue_runner.py"

MODEL_VARIANTS = {
    "8-bit (recommended - near bf16, about twice as fast)": "8bit",
    "bf16 (maximum quality, slowest)": "bf16",
    "4-bit (fastest and smallest, some drift)": "4bit",
}
DEFAULT_VARIANT = "8-bit (recommended - near bf16, about twice as fast)"


def variant_available(subdir):
    return (REPO_DIR / subdir).exists()


def available_variants():
    return [k for k in MODEL_VARIANTS if variant_available(MODEL_VARIANTS[k])]


def default_variant(labels):
    return DEFAULT_VARIANT if DEFAULT_VARIANT in labels else (labels or list(MODEL_VARIANTS))[0]


# --------------------------------------------------------------- vocabulary --

# The model calls this "--cot"; nobody outside the repo should have to know that.
SCORE_PLANNING = {
    "Melody + chords": "full",
    "Melody only": "melody",
    "None - straight to audio": "off",
}
DEFAULT_PLANNING = "Melody + chords"

# There used to be three radio buttons here asking which kind of prompt you had
# just typed. The prompt itself already says: braces mean "pick one of these",
# a --- line means "here is another whole version". Asking you to say it twice
# was a way of getting it wrong.

SEQUENTIAL_RE = re.compile(r"(?m)^\s*-{3,}\s*$")


def detect_prompt_mode(text):
    """Read the prompt and decide what kind it is. Sequential wins over dynamic,
    because a sequential prompt may well have braces inside each version."""
    text = text or ""
    if SEQUENTIAL_RE.search(text):
        return "Sequential"
    if _WILDCARD_RE.search(text):
        return "Dynamic"
    return "Fixed"


def prompt_mode_note(label, text):
    """One line under the box saying what was understood, so nothing is silent."""
    mode = detect_prompt_mode(text)
    if mode == "Sequential":
        n = len(split_sequential(text))
        extra = " Braces inside each version are still resolved." if _WILDCARD_RE.search(text or "") else ""
        return (f"**{label}: sequential** - {n} version{'s' if n != 1 else ''}, one per track, "
                f"looping when the batch is longer.{extra}")
    if mode == "Dynamic":
        n = len(_WILDCARD_RE.findall(text or ""))
        return (f"**{label}: dynamic** - {n} `{{a|b}}` choice{'s' if n != 1 else ''}, "
                f"drawn fresh for every track.")
    return f"**{label}: fixed** - used exactly as typed for every track."


# ------------------------------------------------------------------ texts --
#
# 2.22 rewrote every explanation on the page. The rule: say what the control
# does, what to set it to, and what it costs - in that order, in short
# sentences, and only what was measured. The long stories behind each number
# live in comments like this one and in the README, not on the page.

PROMPT_HELP = (
    "`{bright|dark}` picks one of the choices at random for each track. A line holding "
    "only `---` separates complete versions, used one per track, in turn."
)

STYLE_HOWTO = (
    "**Style prompt**  \n"
    "A list of musical facts, separated by commas - not sentences. In this order: "
    "genre, era, voice, instruments, rhythm, harmony, production, tempo, mood.\n\n"
    "*Dark synth-pop, restrained female alto, dry close vocal, analog polysynths, "
    "sequenced bass, sparse electronic drums, minor-key harmony, 105 BPM, nocturnal.*\n\n"
    "Words like *beautiful* or *amazing* give the model nothing to play.\n\n"
    + PROMPT_HELP
)

LYRICS_HOWTO = (
    "**Lyrics**  \n"
    "Only the words to be sung, with section tags. Anything else written here is sung "
    "too - musical directions belong in the Style prompt.\n\n"
    "Paste the words first, then click where a tag goes and press it:"
)

STYLE_STRENGTH_TITLE = "Style-prompt fidelity (CFG)"
STYLE_STRENGTH_INFO = (
    "1.0 = off: the model's own setting, and the fastest. Higher values make the sound "
    "follow the words of your Style and Lyrics more strictly, and the render takes about "
    "**twice as long**. It does not touch the score (melody and chords).  \n"
    "*Its effect has not been measured yet (168 of the 181 test tracks were made at 1.0). "
    "Compare a few tracks at 1.0 and 1.5 before using it on a batch.*"
)


DARING_TITLE = "Harmonic daring"
DARING_INFO = (
    "How adventurous the model is while it **writes the score** - melody and chords "
    "together, before any sound exists. This is the real harmony control, and its chords "
    "fit the melody because both are written at once.  \n"
    "3 is the model's own setting: plain loops. On 181 test tracks, **7 to 9** was the "
    "useful zone and 10 sometimes broke the score. The line under the slider says what "
    "each setting gave there - a trend, not a promise."
)
DARING_INFO_SPECTRUM = DARING_INFO + (
    "  \n*It acts here only. The harmony pass below has its own daring.*")

# ------------------------------------------------------------------ length --

SECONDS_PER_FRAME = 0.04            # the model counts 40 ms codec frames: 25 per second
MODEL_MAX_FRAMES = 9000             # the model's own ceiling: 6 minutes


def frames_from_duration(duration_s):
    """Target duration in seconds -> codec frames. One number, one control."""
    try:
        d = float(duration_s)
    except (TypeError, ValueError):
        return None
    if d <= 0:
        return None
    return max(1, min(MODEL_MAX_FRAMES, round(d / SECONDS_PER_FRAME)))


# How the score and the duration meet. Measured on 23 September 2026 on 181
# originals: the model writes a whole piece first (median 2:31, 1:26 to 4:51
# for 80% of them), whatever the duration, and the duration only cuts the
# sound. With lyrics the length follows the lyrics - 8 to 15 sung lines gave
# about two minutes, 48 or more gave five. See score_length.py.
FIT_CHOICES = {
    "Stop the sound at the duration (as before)": "cut",
    "Shorten the score to fit - ends at a section boundary": "trim",
    "Play the whole score - the duration is ignored (max 6 min)": "whole",
}
DEFAULT_FIT = "Stop the sound at the duration (as before)"
FIT_LABEL_BY_MODE = {v: k for k, v in FIT_CHOICES.items()}
TYPICAL_SCORE_SECONDS = 150

LENGTH_INFO = (
    "The model always writes a **whole piece** first - usually 2 to 3 minutes - and "
    "then plays it. The duration only says **where the sound stops**: at 30 s "
    "you hear the intro, and half of a typical score is never played.  \n"
    "With lyrics, the piece follows the lyrics: fewer lines, shorter piece. With "
    "`[instrumental]`, only the choice on the right can shorten it."
)

FIT_INFO_NOTE = (
    "*Shorten* and *whole* are new in 2.22. *Whole* keeps the same piece as *stop*, only "
    "longer. *Shorten* changes the score, so it is a different take - check a few endings "
    "by ear before a big batch."
)


def fit_mode(label):
    return FIT_CHOICES.get(label, "cut")


# ------------------------------------------------------------ harmonic daring --

DARING_DEFAULT = 3                  # == the model's shipped 0.70 / 0.90 / 30


def daring_to_sampling(daring):
    """0-10 -> (temperature, top_p, top_k). 3 reproduces the repo defaults exactly."""
    try:
        d = max(0.0, min(10.0, float(daring)))
    except (TypeError, ValueError):
        d = DARING_DEFAULT
    if d <= DARING_DEFAULT:
        f = d / DARING_DEFAULT
        return (0.60 + f * 0.10, 0.86 + f * 0.04, round(20 + f * 10))
    f = (d - DARING_DEFAULT) / (10 - DARING_DEFAULT)
    return (0.70 + f * 0.45, 0.90 + f * 0.08, round(30 + f * 70))


def daring_label(daring):
    """Three decimals, not two: this string is also what a person reads off a
    .txt and types back in. Printing 1.02 where the renderer receives 1.021
    makes the two runs quietly different, and the difference is a whole
    different piece of music."""
    t, p, k = daring_to_sampling(daring)
    tag = "repo default" if round(float(daring)) == DARING_DEFAULT else \
          ("safe" if float(daring) < DARING_DEFAULT else "adventurous")
    return f"temperature {t:.3f}, top_p {p:.3f}, top_k {k}  ({tag})"


# What each setting of the daring did to 181 test tracks, measured on
# 23 September 2026 (see "HARMONY_PASS - diagnostic et refonte.md", 1.1):
# distinct chords over the whole score, share of tracks with a key change,
# and how often bars came out the wrong length. The prompts differ from row to
# row and some rows hold three tracks, so this is a trend, not an experiment -
# which is why every line says how many tracks it rests on.
DARING_EVIDENCE = {
    0: "Plainer than the model's own setting. *(not measured)*",
    1: "Plainer than the model's own setting. *(not measured)*",
    2: "A little plainer than the model's own setting. *(not measured)*",
    3: "The model's own setting: about **2 different chords** - a loop. *(3 tracks)*",
    4: "Probably **3 to 5 chords**, no key change. *(not measured)*",
    5: "About **5 different chords**, no key change, clean score. *(5 tracks)*",
    6: "About **6 chords**, no key change, clean score. *(101 tracks - the best-measured "
       "setting)*",
    7: "About **6 chords**, no key change seen, clean score. *(4 tracks)*",
    8: "About **8 chords**; 1 track in 20 changes key; now and then a few bars come out "
       "the wrong length. *(44 tracks)*",
    9: "About **10 chords**; 1 track in 4 changes key; no broken bars seen. *(only 4 "
       "tracks)*",
    10: "About **18 chords**; 2 tracks in 5 change key; **some scores fall apart** (up to "
        "70% of the bars the wrong length). *(20 tracks)*",
}


def daring_meaning(daring):
    """What to expect from this setting, from 181 measured tracks - instead of the
    temperature / top_p / top_k it maps to, which say nothing to a musician.
    The numbers themselves are still written into every .txt."""
    try:
        d = int(round(float(daring)))
    except (TypeError, ValueError):
        d = DARING_DEFAULT
    d = max(0, min(10, d))
    return f"**{d}** - {DARING_EVIDENCE[d]}"


# ------------------------------------------------------------------ prompts --

_WILDCARD_RE = re.compile(r"\{([^{}]+)\}")


def resolve_dynamic(text):
    def pick(match):
        options = [o.strip() for o in match.group(1).split("|") if o.strip()]
        return random.choice(options) if options else match.group(0)
    return _WILDCARD_RE.sub(pick, text)


def split_sequential(text):
    blocks = [b.strip() for b in SEQUENTIAL_RE.split(text) if b.strip()]
    return blocks or [text]


class LivePrompt:
    """Style and lyrics that a running batch re-reads at every track, so you can
    edit them mid-run and have the change land on the next track instead of
    having to stop and start over."""

    def __init__(self):
        self.text = {"style": "", "lyrics": ""}
        self.blocks = {}

    def edit(self, field, value):
        self.text[field] = value
        self.blocks.pop(field, None)      # a Sequential prompt must re-split

    def start(self, style, lyrics):
        self.text["style"] = style
        self.text["lyrics"] = lyrics
        self.blocks.clear()

    def resolve(self, field, track_index):
        """The mode is read off the text itself, every time - so editing a
        running batch's prompt into a sequential one just works."""
        text = self.text.get(field, "")
        mode = detect_prompt_mode(text)
        if mode == "Fixed":
            return text
        if mode == "Dynamic":
            return resolve_dynamic(text)
        if field not in self.blocks:
            self.blocks[field] = split_sequential(text)
        blocks = self.blocks[field]
        return resolve_dynamic(blocks[track_index % len(blocks)])


# -------------------------------------------------------------------- stop --

class StopController:
    """One batch at a time; Stop kills whatever render is in flight."""

    def __init__(self):
        self.event = threading.Event()
        self.process = {"proc": None}

    def clear(self):
        self.event.clear()

    def is_set(self):
        return self.event.is_set()

    def request(self):
        self.event.set()
        proc = self.process.get("proc")
        if proc is not None and proc.poll() is None:
            proc.terminate()
            return "Stopped. The render in progress was cancelled - nothing of it is saved."
        return "Stopped before the next render started."

    def run(self, cmd, cwd):
        """Run a render, yielding its output line by line."""
        self.returncode = None          # never inherit the previous render's result
        process = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, bufsize=1)
        self.process["proc"] = process
        try:
            for line in process.stdout:
                yield line.rstrip()
            process.wait()
            self.returncode = process.returncode
        finally:
            self.process["proc"] = None


def render_command(*, model_path, style, lyrics_file, out_path, seed, planning="full",
                    abc_file=None, cfg_scale=None, steps=None, max_frames=None,
                    daring=None, abc_prefix_file=None, plan_only=False,
                    cfg_negative_abc_file=None, fit="cut"):
    """Build the argv for one render, through our runner (see yue_runner.py)."""
    cmd = [sys.executable, str(RUNNER),
           "--repo", str(REPO_DIR),
           "--model", str(model_path),
           "--style", style,
           "--lyrics-file", str(lyrics_file),
           "--cot", planning,
           "--seed", str(int(seed)),
           "--out", str(out_path)]
    if abc_file is not None:
        cmd += ["--abc-file", str(abc_file)]
    elif abc_prefix_file is not None:
        cmd += ["--abc-prefix-file", str(abc_prefix_file)]
    if cfg_scale and float(cfg_scale) > 1.0:
        cmd += ["--cfg-scale", str(float(cfg_scale))]
    if steps:
        cmd += ["--steps", str(int(steps))]
    if max_frames:
        cmd += ["--max-semantic-tokens", str(int(max_frames))]
    if daring is not None and abc_file is None:
        t, p, k = daring_to_sampling(daring)
        cmd += ["--abc-temperature", f"{t:.3f}", "--abc-top-p", f"{p:.3f}",
                "--abc-top-k", str(k)]
    if plan_only:
        cmd += ["--plan-only"]
    if cfg_negative_abc_file is not None:
        cmd += ["--cfg-negative-abc-file", str(cfg_negative_abc_file)]
    if fit and fit != "cut" and not plan_only:
        cmd += ["--fit-score", fit]
    return cmd


# ---------------------------------------------------------------- exporting --

def convert_audio(wav_path, save_mp3, save_flac):
    """Optional MP3/FLAC alongside the wav. Returns (paths written, warning)."""
    saved, warnings_ = [], []
    if not (save_mp3 or save_flac):
        return saved, None
    if shutil.which("ffmpeg") is None:
        return saved, ("ffmpeg not found - install it (brew install ffmpeg) "
                       "to enable MP3/FLAC export.")
    wav_path = Path(wav_path)
    base = wav_path.with_suffix("")
    jobs = []
    if save_mp3:
        jobs.append((base.with_suffix(".mp3"),
                     ["-codec:a", "libmp3lame", "-q:a", "2"]))
    if save_flac:
        jobs.append((base.with_suffix(".flac"), []))
    for target, opts in jobs:
        r = subprocess.run(["ffmpeg", "-y", "-i", str(wav_path)] + opts + [str(target)],
                            capture_output=True, text=True)
        if target.exists():
            saved.append(target)
        else:
            warnings_.append(f"{target.suffix[1:].upper()} export failed: "
                             f"{r.stderr.strip()[-300:]}")
    return saved, ("\n".join(warnings_) if warnings_ else None)


BATCH_NAME_INFO = (
    "Your own name for this run, placed at the front of every filename."
)

MIDI_INFO = (
    "MIDI needs abc2midi, installed once with `brew install abcmidi`."
)


def run_folder(root, batch, stamp):
    """A folder for the whole run, so two batches cannot mix."""
    # A folder name may keep its spaces - "Night Glass 20260916-1100" reads
    # better than "Night_Glass...", and only the characters that would break a
    # path need to go. Filenames inside it still use the strict safe_name().
    name = re.sub(r'[/\\:\x00]+', " ", (batch or "").strip()).strip() or "spectrum"
    folder = Path(root) / f"{name[:60]} {stamp}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def track_folder(run_dir, index, total):
    """A folder per track. Asking for wav + mp3 + flac + abc + mid + txt on a
    track with five harmonisations is thirty-six files; loose in one folder that
    is unreadable, and there is nothing to be gained by it."""
    folder = Path(run_dir) / f"Track {index} of {total}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def safe_name(text):
    """A filename fragment that cannot break a path."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (text or "").strip())
    return cleaned.strip("_.")[:60]


def stem_with_batch(batch, stem):
    name = safe_name(batch)
    return f"{name}_{stem}" if name else stem


def convert_midi(abc_path):
    """Turn a score into a .mid, so it can be opened in GarageBand or any DAW.
    Returns (path or None, warning or None)."""
    abc_path = Path(abc_path)
    if not abc_path.exists():
        return None, None
    if shutil.which("abc2midi") is None:
        return None, ("abc2midi not found - install it once with 'brew install abcmidi' "
                      "to get a .mid file you can drag into GarageBand.")
    target = abc_path.with_suffix(".mid")
    subprocess.run(["abc2midi", str(abc_path), "-o", str(target)],
                    capture_output=True, text=True)
    return (target, None) if target.exists() else (None, "MIDI conversion failed.")


# ------------------------------------------------------------ Finder colours --

FINDER_LABEL = {"green": 6, "purple": 5}     # verified defaults: 5 purple, 6 green


def set_finder_label(path, colour):
    try:
        r = subprocess.run(
            ["osascript", "-e",
             f'tell application "Finder" to set label index of '
             f'(POSIX file "{path}" as alias) to {FINDER_LABEL[colour]}'],
            capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def label_group(paths, colour, warned, log_lines):
    """Colour a whole family of files at once. Never fails a render."""
    existing = [Path(p) for p in paths if Path(p).exists()]
    if not existing:
        return
    if not any(set_finder_label(p, colour) for p in existing) and not warned["done"]:
        warned["done"] = True
        log_lines.append(
            "Note: Finder colours could not be set - macOS asks once for permission to let "
            "Terminal control Finder (look for a prompt, or System Settings > Privacy & "
            "Security > Automation). Everything is saved correctly either way.")


# ----------------------------------------------------------------- sidecars --

def write_sidecar(path, *, title, lines, style, lyrics):
    body = [title, f"Generated: {time.strftime('%Y%m%d-%H%M%S')}"] + list(lines)
    body += ["", "--- Style (as sent to the model) ---", style,
             "", "--- Lyrics (as sent to the model) ---", lyrics]
    Path(path).write_text("\n".join(body), encoding="utf-8")


# Every line these apps write into a .txt, and how to read it back. Adding a
# setting means adding one line here and one entry in the app's target list -
# nothing else.
SIDECAR_FIELDS = {
    "model_variant":   r'^Model variant:\s*(\S+)',
    "seed":            r'^Seed:\s*(-?\d+)',
    "planning":        r'^Score planning:\s*(.+?)\s*$',
    # Spectrum 2.21 wrote "Score-writing daring", and restoring one of its tracks
    # silently left the daring alone. Both spellings are read.
    "daring":          r'^(?:Harmonic|Score-writing) daring:\s*(\d+)',
    "fit":             r'^Score fit:\s*(\w+)',
    "steps":           r'^Audio refinement steps:\s*(\d+)',
    # Spectrum's richness floor may have raised the daring to get its score.
    # That daring, not the one on the slider, is what wrote this track.
    "floor_daring":    r'richness floor: \d+ distinct chords at daring (\d+)',
    "style_strength":  r'^Style strength.*?:\s*([\d.]+)',
    "duration_s":      r'^Target duration \(s\):\s*(\d+)',
    "meter":           r'^Metre:\s*([^,\n]+)',
    "tempo":           r'^Metre:.*?tempo\s*(\d+)',
    "rhythm":          r'^Rhythmic complexity:\s*(\d+)',
    "batch_name":      r'^Batch name:\s*(.+?)\s*$',
    # the harmony half, present only on a mutation's .txt
    "richness":        r'^Harmony:\s*richness\s*(\d+)',
    "key_changes":     r'^Harmony:.*?key changes\s*(\d+)',
    "character":       r'^Harmony:.*?,\s*(smooth|bold)\s+keys',
    "return_home":     r'^Harmony:.*?return home\s*(\w+)',
    "harmony_seed":    r'^Harmony:.*?harmony seed\s*(-?\d+)',
    "modes":           r'^Modal plan:\s*(.+?)\s*$',
    "mod_type":        r'^Key-change type:\s*(.+?),\s*melody moved',
    "move_melody":     r'melody moved:\s*(\w+)',
    "motion":          r'^Harmonic motion:\s*(\d+)',
    "harmony_staff":   r'chords (?:played|written) as notes:\s*(\w+)',
    "subs":            r'^Substitutions:\s*(.+?)\s*$',
}


def read_sidecar(text):
    """Everything an app wrote about a track, read back out of its .txt.

    This is the other half of write_sidecar(): what makes a track you liked
    reproducible instead of merely documented."""
    info = {}
    for key, pat in SIDECAR_FIELDS.items():
        m = re.search(pat, text, re.MULTILINE)
        if m:
            info[key] = m.group(1).strip()
    if "floor_daring" in info:
        info["daring"] = info["floor_daring"]
    for tag, field in (("Style", "style"), ("Lyrics", "lyrics")):
        m = re.search(rf'---\s*{tag}.*?---\s*\n(.*?)(?:\n\n---|\Z)', text, re.DOTALL)
        if m:
            info[field] = m.group(1).strip()
    return info


# Where tracks are written, in this folder and in the published layout. A file
# dropped on the page is a COPY in Gradio's own temporary folder - the browser
# never tells the server where the original was - so its .txt is not beside
# it. It is found here instead, by name: every filename carries a timestamp
# and a seed, so the name alone identifies the track.
SEARCH_ROOTS = [V2_DIR / "outputs", V2_DIR / "YuMusic2" / "outputs",
                V2_DIR / "Spectrum2" / "spectrum_outputs",
                V2_DIR / "HarmonyMutator2" / "mutations"]


def _track_stem(name):
    for suffix in (".latents.npy", ".wav", ".mp3", ".flac", ".abc", ".mid", ".npy", ".txt"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return Path(name).stem


def sidecar_for(path, roots=None):
    """The .txt that belongs to a track, given ANY of the track's files.

    Drop the wav, the mp3, the score, the MIDI or the .txt itself - they all
    share a stem, so any of them finds the settings. Until 2.22 only the .txt
    worked: the others were looked up beside Gradio's temporary copy, where
    nothing else ever is, and the page said "No settings file found"."""
    path = Path(path)
    if path.suffix.lower() == ".txt" and path.exists():
        return path
    stem = _track_stem(path.name)
    candidate = path.with_name(stem + ".txt")
    if candidate.exists():
        return candidate
    for root in list(roots or []) + SEARCH_ROOTS:
        root = Path(root)
        if not root.is_dir():
            continue
        for hit in root.rglob(stem + ".txt"):
            return hit
    return None


def stop_now(stop, progress, awake):
    """What the Stop button does. Returns (message, progress bar).

    Stop cancels the running batch outright, so the batch never reaches its
    own last lines: the bar stayed frozen on "Render 3 of 5", and - worse -
    the anti-sleep it had started kept the Mac awake until the next batch
    ended. Both are handled here, where the cancel happens."""
    message = stop.request()
    awake.stop()
    done, total = progress.done, progress.total
    text = (f"**Stopped.** {done} of {total} render{'s' if total != 1 else ''} finished."
            if total else "**Stopped.**")
    return message, progress_html(text, 0.0)


def restored_note(path, info):
    """What came back, and the one thing worth knowing about using it."""
    name = Path(path).name
    if not info:
        return f"*`{name}` does not look like a track written by these apps.*"
    bits = []
    if "seed" in info:
        bits.append(f"seed **{info['seed']}**")
    if "duration_s" in info:
        bits.append(f"{info['duration_s']} s")
    if "daring" in info:
        bits.append(f"daring {info['daring']}" + (" (the one the richness floor chose)"
                                                  if "floor_daring" in info else ""))
    if "richness" in info:
        bits.append(f"richness {info['richness']}")
    harmony = " The harmony settings came back too." if "richness" in info else ""
    if info.get("fit") == "trim":
        more = ("This track was made from a **shortened** score. To hear the whole piece "
                "it was cut from, set *When the score is longer* to **Play the whole score**.")
    else:
        more = ("To hear **more of the same piece**, set *When the score is longer* to "
                "**Play the whole score** and press Generate - same seed, same piece, "
                "nothing cut off.")
    return (f"**Restored from `{name}`** - " + ", ".join(bits) + "." + harmony + "  \n"
            + more + "  \n*The style comes back exactly as it was sent, so Instrumental and "
            "Rhythmic complexity are left at zero: their words are already in it.*")


# ---------------------------------------------------------------- estimates --

# Rough, measured on an M4 Pro: render time as a multiple of the track length,
# plus the model reload every render costs (each render is its own process).
SPEED = {"bf16": 1.6, "8bit": 0.9, "4bit": 0.7}
LOAD_SECONDS = 45


def render_seconds(duration_s, variant_subdir, cfg_scale=None):
    """Roughly how long ONE render takes. Guidance above 1.0 runs the model
    twice per token, so it roughly doubles."""
    try:
        dur = float(duration_s) or 60
    except (TypeError, ValueError):
        dur = 60
    per = dur * SPEED.get(variant_subdir, 1.3) + LOAD_SECONDS
    if cfg_scale and float(cfg_scale) > 1.0:
        per = per * 2 - LOAD_SECONDS
    return per


def _clock_words(total):
    hours, minutes = int(total // 3600), int((total % 3600) // 60)
    return f"{hours} h {minutes:02d}" if hours else f"{minutes} min"


def _fit_duration(duration_s, fit):
    """The length a render will really have, as far as it can be known before
    the score is written. 'whole' is priced at a typical score."""
    if fit == "whole":
        return TYPICAL_SCORE_SECONDS
    return duration_s


def _fit_words(fit):
    return ("  *Priced at a typical 2:30 score - the real length is known only once each "
            "score is written.*" if fit == "whole" else "")


def estimate_batch(renders, duration_s, variant_subdir, cfg_scale=None, fit="cut"):
    """A plain-language 'is this an overnight job?' line."""
    renders = max(0, int(renders))
    if renders == 0:
        return "Nothing to render with these settings."
    total = renders * render_seconds(_fit_duration(duration_s, fit), variant_subdir, cfg_scale)
    return (f"**{renders} track{'s' if renders != 1 else ''}** - roughly "
            f"**{_clock_words(total)}**." + _fit_words(fit))


def estimate_run(originals, versions_each, duration_s, variant_subdir,
                  cfg_original=None, cfg_harmony=None, fit="cut"):
    """The same sentence, with the two stages priced apart.

    From 2.21 the harmony pass has its own guidance, so a run can be quick in
    part 1 and twice as slow in part 2. Pricing the whole run at one cfg was
    out by up to a factor of two on exactly the runs that take longest - which
    is when knowing matters."""
    originals = max(0, int(originals))
    versions_each = max(0, int(versions_each))
    renders = originals * (1 + versions_each)
    if renders == 0:
        return "Nothing to render with these settings."
    duration_s = _fit_duration(duration_s, fit)
    a = originals * render_seconds(duration_s, variant_subdir, cfg_original)
    b = originals * versions_each * render_seconds(duration_s, variant_subdir, cfg_harmony)
    line = (f"**{renders} audio file{'s' if renders != 1 else ''}** - roughly "
            f"**{_clock_words(a + b)}**." + _fit_words(fit))
    if versions_each and cfg_harmony and float(cfg_harmony) > 1.0:
        line += (f"  Of that, {_clock_words(b)} is the harmony pass, doubled by its "
                 f"score adherence of {float(cfg_harmony):g}.")
    return line


# =========================================================================== #
#  2.7 additions                                                              #
# =========================================================================== #

# ------------------------------------------------------------- staying awake --
#
# Found the hard way on the night of 15 September: a twenty-track batch stopped
# at thirteen, mid-track, with the .wav written and the .txt never reached. The
# app had not crashed - it was still running in the morning. The Mac had gone to
# sleep, which dropped the browser's connection, which made Gradio cancel the
# event, which killed the loop exactly where it stood.
#
# So an overnight batch has to hold the machine awake itself. `caffeinate -i -m`
# prevents idle sleep and disk sleep while it runs, and nothing else: the screen
# is still free to switch off, and the moment the batch ends the machine is
# allowed to sleep again.

class KeepAwake:
    """Hold off sleep for the length of a batch, and not one second longer."""

    def __init__(self):
        self.proc = None

    def start(self):
        if self.proc is not None:
            return None
        try:
            self.proc = subprocess.Popen(["caffeinate", "-i", "-m"],
                                          stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL)
            return ("This Mac will be kept awake until the batch finishes - the screen may "
                    "still switch off. (A sleeping Mac drops the browser connection, and "
                    "that stops a batch.)")
        except Exception:
            self.proc = None
            return ("Note: could not hold off sleep. If this is an overnight batch, set "
                    "System Settings > Lock Screen > Turn display off... to Never first.")

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None


# ------------------------------------------------------------------ progress --
#
# A hundred-track batch is an overnight job, and "it is running" is not enough
# information to decide whether to wait up. This turns the log into two
# sentences and a bar: where we are, how long it has taken, when it ends.

class BatchProgress:
    """Counts renders, and interpolates inside the current one from the token
    counts the renderer prints, so the bar moves during a render and not only
    between them."""

    def __init__(self):
        self.reset(0)

    def reset(self, total):
        self.total = max(0, int(total))
        self.done = 0
        self.started = time.time()
        self.within = 0.0
        self.label = ""
        self.frames = None

    def begin(self, label, frames=None):
        self.label = label
        self.frames = frames or None
        self.within = 0.0

    def note_line(self, line):
        """Read the renderer's own output for a sense of how far into this
        render we are. The three stages are roughly 15% / 55% / 30% of the time."""
        if not self.frames:
            return
        m = re.search(r"\[semantic\]\s+(\d+)\s+tokens", line)
        if m:
            self.within = 0.15 + 0.55 * min(1.0, int(m.group(1)) / float(self.frames))
            return
        m = re.search(r"\[nar\] step (\d+)/(\d+)", line)
        if m:
            a, b = int(m.group(1)), max(1, int(m.group(2)))
            self.within = 0.70 + 0.30 * (a / b)
            return
        if line.startswith("[vae]"):
            self.within = 0.98

    def finish(self):
        self.done += 1
        self.within = 0.0
        self.label = ""

    # ------------------------------------------------------------------ views
    @property
    def fraction(self):
        if not self.total:
            return 0.0
        return min(1.0, (self.done + self.within) / self.total)

    def report(self):
        if not self.total:
            return "", 0.0
        elapsed = time.time() - self.started
        frac = self.fraction
        # "Render 8 of 20" is the honest count - a track with three variations
        # is four renders, and calling all four "track 8" was a small lie that
        # made the estimate look wrong. The label says which track and which
        # variation, which is the thing you actually wanted to know.
        parts = [f"**Render {min(self.done + 1, self.total)} of {self.total}**"]
        if self.label:
            parts.append(self.label)
        parts.append(f"{_clock(elapsed)} elapsed")
        if frac > 0.01 and self.done > 0:
            remaining = elapsed / frac - elapsed
            end = time.localtime(time.time() + remaining)
            parts.append(f"about {_clock(remaining)} left")
            parts.append(f"finishing around **{time.strftime('%H:%M', end)}**")
        else:
            parts.append("estimating...")
        return "  -  ".join(parts), frac


def _clock(seconds):
    seconds = max(0, int(seconds))
    h, m = seconds // 3600, (seconds % 3600) // 60
    if h:
        return f"{h} h {m:02d}"
    if m:
        return f"{m} min"
    return f"{seconds} s"


def progress_html(text, fraction, done=False, segments=0):
    """The bar itself. Inline styles only, so no stylesheet can lose it.

    `segments` draws one tick per render, so a run of 5 tracks x 4 versions
    reads as twenty boxes filling up rather than one anonymous sliding bar."""
    pct = max(0.0, min(1.0, float(fraction))) * 100
    colour = "#22c55e" if done else "#f97316"
    # The text is written with Markdown bold, but this is HTML: until 2.22 the
    # bar said **Render 3 of 20** with the asterisks showing.
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text or "")
    ticks = ""
    if segments and 1 < int(segments) <= 60:
        step = 100.0 / int(segments)
        ticks = (f'<div style="position:absolute;inset:0;'
                 f'background:repeating-linear-gradient(to right,'
                 f'transparent 0,transparent calc({step}% - 1px),'
                 f'rgba(0,0,0,.28) calc({step}% - 1px),rgba(0,0,0,.28) {step}%)">'
                 f'</div>')
    return (
        f'<div style="font-size:0.95em;margin-bottom:6px">{text or "Idle."}</div>'
        f'<div style="position:relative;background:#e5e7eb;border-radius:999px;'
        f'height:14px;overflow:hidden">'
        f'<div style="width:{pct:.1f}%;height:100%;background:{colour};'
        f'transition:width .4s"></div>{ticks}</div>'
    )


IDLE_PROGRESS = progress_html("Not running.", 0.0)


# ------------------------------------------------------------- instrumental --
#
# "[instrumental]" in the lyrics means "there are no words to sing". It does NOT
# mean "there are no voices" - and on 16 September a film-score prompt proved the
# difference: the lyrics said [instrumental], while the style prompt asked for
# "breathy female vocal fragments", "ghostly wordless choir harmonics",
# "female voices as breath, whispers, cries", "enormous choral masses" and
# "female choir". The model did exactly as it was told. A wordless choir IS
# instrumental music; it just happens to be sung.
#
# So this checkbox does the only thing that actually works: it writes the lyrics
# for you AND appends a refusal to the style, then tells you when your own style
# prompt is asking for the very thing you are trying to remove.

VOICE_WORDS = ("vocal", "voice", "voices", "choir", "choral", "sung", "singing",
               "singer", "soprano", "tenor", "whisper", "whispers", "chant",
               "vocals", "humming", "wordless")

# At the FRONT, not the end. Found on 16 September: a
# "NO SINGER, NO LYRICS" typed at the top of a prompt worked where a sentence
# appended at the bottom had not. The model weighs the opening of a prompt more
# heavily, so a refusal belongs there.
NO_VOICE_HEAD = ("NO SINGER, NO LYRICS, NO VOCALS, NO CHOIR, NO HUMMING, "
                 "NO WORDLESS SINGING.")

INSTRUMENTAL_INFO = (
    "Sends `[instrumental]` as the lyrics **and** puts *NO SINGER, NO VOCALS, NO CHOIR...* "
    "at the start of the style, where it works best. Both are needed: a style that asks "
    "for a choir still gets a choir. Not 100% reliable."
)


def voice_requests(style):
    """The words in a style prompt that ask for singing. Returned so the page can
    show them rather than silently fighting the person who wrote them."""
    text = (style or "").lower()
    found = []
    for w in VOICE_WORDS:
        if re.search(rf"\b{re.escape(w)}\b", text) and w not in found:
            found.append(w)
    return found


def instrumental_note(style, on):
    if not on:
        return ""
    asked = voice_requests(style)
    if not asked:
        return ("**Instrumental** - `[instrumental]` will be sent as the lyrics, and a "
                "refusal of voices added to your style.")
    return ("**Careful - your style prompt asks for voices.** It contains: "
            + ", ".join(f"`{w}`" for w in asked[:8])
            + ". The refusal is added at the start, but the model weighs the whole prompt: "
              "the surest fix is to take those words out yourself.")


def apply_instrumental(style, on):
    """Idempotent on purpose. A sidecar records the style AS SENT, so it already
    carries this line; adding it a second time would change the prompt, and a
    changed prompt is a different piece of music. That is exactly the bug of
    16 September - a restored track came back as something else entirely."""
    if not on or NO_VOICE_HEAD in (style or ""):
        return style
    return f"{NO_VOICE_HEAD}\n\n{style.lstrip()}"


# --------------------------------------------------------------------- meter --
#
# YuE2 decides its own metre while it writes the score, and it overwhelmingly
# decides on 4/4. But the score is written as text, one token at a time, which
# means its first line can be written FOR it: give it "M:7/8" and it carries on
# in seven. That is what --abc-prefix does (see yue_runner.py). The prompt alone
# does not do this - we tried, and the model writes 4/4 whatever you ask it in
# words.

METERS = {
    "As the model likes (usually 4/4)": None,
    "4/4 - common time": "4/4",
    "3/4 - waltz  (tested: holds)": "3/4",
    "6/8 - compound, lilting": "6/8",
    "12/8 - slow shuffle": "12/8",
    "5/4 - five  (expect drift to 4/4)": "5/4",
    "7/8 - seven  (tested: drifts to 4/4)": "7/8",
    "9/8 - nine  (expect drift to 4/4)": "9/8",
}
DEFAULT_METER = "As the model likes (usually 4/4)"

METER_INFO = (
    "Writes the first line of the score for the model, which is the only way to get a "
    "metre - asking in words does not work. **3/4** holds for a whole track. **7/8** holds "
    "a few bars, then drifts back to 4/4; expect the same from 5/4 and 9/8.  \n"
    "Tempo: 0 lets the model choose. Any other number is followed, whatever the metre."
)

# Rhythmic complexity is a different thing from metre: 'Close to the Edit' is in
# plain 4/4 and never repeats a bar. There is no numeric control for that in the
# model, so this one is honest about what it is - wording appended to your style
# prompt, which is the only lever that exists for it.
RHYTHM_WORDS = {
    0: None,
    1: "with a steady, unchanging groove",
    2: "with light syncopation, the backbeat occasionally displaced",
    3: "with a rhythm section that varies every few bars, syncopated accents, "
       "fills that never repeat",
    4: "with a constantly reconfigured beat, stuttering edits, accents moved bar by bar, "
       "no two measures drummed the same way",
    5: "with the pulse continuously cut up and reassembled, hocketed percussion, "
       "abrupt metric displacement, breakbeat edits, the downbeat repeatedly hidden",
}
RHYTHM_INFO = (
    "Adds one sentence about rhythm to the end of your style prompt. Only words - the "
    "model has no rhythm control - but it is the one lever there is. 0 adds nothing."
)


def rhythm_note(level):
    """The exact sentence the slider adds, so nothing is added blind."""
    words = RHYTHM_WORDS.get(int(level or 0))
    if not words:
        return "*Nothing added.*"
    return f"*Adds:* \u201c{words[0].upper()}{words[1:]}.\u201d"


def apply_rhythm(style, level):
    """Idempotent, for the same reason as apply_instrumental()."""
    words = RHYTHM_WORDS.get(int(level or 0))
    if not words:
        return style
    sentence = f"{words[0].upper()}{words[1:]}."
    if sentence in (style or ""):
        return style
    return f"{style.rstrip().rstrip('.')}. {sentence}"


def abc_prefix_for(meter, tempo=None):
    """The opening lines of a score, handed to the model so it continues in the
    metre we want. Returns None when there is nothing to impose."""
    if not meter and not tempo:
        return None
    lines = ["X:1", "T:"]
    lines.append(f"M:{meter}" if meter else "M:4/4")
    lines.append("L:1/16")
    if tempo:
        lines.append(f"Q:1/4={int(tempo)}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------- simple mode --

SEED_WARNING = (
    "**The seed is fixed at {seed}: every track in this run will be the same piece.** "
    "Right for developing one track; for a batch of different pieces, set it back to **-1**."
)

SEED_WARNING_VARY = (
    "**The seed starts at {seed} and adds 1 per track**: {n} different pieces, but a "
    "different set from what -1 would give. Use -1 unless you are re-running a series."
)

SEED_TITLE = "Seed"
SEED_INFO = (
    "**-1** = a new piece for every track. **A number** = that exact piece again: each "
    "track's seed is written in its .txt.  \n"
    "With a fixed seed and several tracks, *add 1* gives neighbouring pieces instead of "
    "copies of one."
)


def seed_note(seed, vary, tracks):
    """Shown beside Generate, not buried in a log. A fixed seed silently giving
    a hundred copies of one piece is the kind of mistake that costs a night."""
    try:
        seed = int(seed)
    except (TypeError, ValueError):
        return ""
    if seed < 0:
        return ""
    n = max(1, int(tracks or 1))
    if vary and n > 1:
        return SEED_WARNING_VARY.format(seed=seed, n=n)
    if n > 1:
        return SEED_WARNING.format(seed=seed)
    return (f"**The seed is fixed at {seed}** - this reproduces that exact piece. To hear "
            f"more of it, choose *Play the whole score* under Length.")


PAUSE_INFO = (
    "Seconds of rest between tracks, only if you want the fans to stop for a while. Not "
    "needed for safety: over a 75-render batch, a Mac mini M4 Pro kept the same speed all "
    "night."
)


NO_NAME_MESSAGE = (
    "**This run has no name.** Type one in the yellow box and press Generate again. The "
    "name starts every filename and names the run's folder - without it you get one more "
    "folder called `spectrum`."
)


BATCH_NAME_HEADER = (
    "Starts every filename and names the run's folder - so it can be found in a month."
)



# =============================================================================
#                                                             the shared UI kit
# =============================================================================
#
# Everything below decides how a page READS, and it lives here so the three
# apps cannot drift apart. It arrived in 2.19, when the YuMusic page was
# rebuilt as a table - explanation on the left, control on the right - and it
# turned out to be worth having everywhere.
#
# Nothing here renders anything by itself. `setting()` and `section()` build
# layout, the rest are strings and small helpers.

UI_CSS = """
.resizable textarea { resize: vertical !important; min-height: 9em; }

/* ------------------------------------------------------------- the run bar
   The only orange thing on the page, so the eye finds it first. It is a
   Column, not a Group: a Group is drawn as a div inside a div, both carrying
   the class, which is how 2.21 ended up with two orange frames and two yellow
   ones, one inside the other. */
.runbar {
    border: 3px solid #f97316 !important;
    border-radius: 12px !important;
    padding: 14px 18px 12px 18px !important;
    background: rgba(249, 115, 22, 0.05) !important;
    gap: 12px !important;
}
.runrow { gap: 22px !important; align-items: stretch !important; }
.namebar {
    border: 2px solid #eab308 !important;
    border-radius: 10px !important;
    background: rgba(234, 179, 8, 0.08) !important;
}
.namebar textarea, .namebar input { font-size: 1.3em !important; padding: 10px 12px !important; }
.namebar label > span, .namebar [data-testid="block-info"] { font-size: 1.05em !important; font-weight: 600 !important; }
/* The Harmony Mutator still builds its run bar from Groups, which Gradio
   nests twice: the inner copy is stripped so each frame is drawn once. */
.runbar .runbar, .namebar .namebar {
    border: none !important; padding: 0 !important; margin: 0 !important;
    background: transparent !important; box-shadow: none !important;
}
.gorow { gap: 12px !important; flex-wrap: nowrap !important; }
.gobtn { min-height: 100px !important; font-size: 1.25em !important; font-weight: 700 !important; border-radius: 10px !important; }
.runhint p { margin: 4px 2px 0 2px !important; font-size: 0.93em; opacity: 0.85; }
.runfoot { align-items: center !important; gap: 16px !important; }
.runfoot p { margin: 0 !important; }

/* The two Finder buttons. In 2.21 they were small grey bars stretched across
   the whole page, which read as a divider rather than as something to press. */
.finderbtn {
    background: #1e3a8a !important;
    border: 1px solid #3b82f6 !important;
    color: #eff6ff !important;
    font-weight: 600 !important;
    border-radius: 8px !important;
    padding: 8px 16px !important;
    white-space: nowrap;
    width: auto !important;
    flex: 0 0 auto !important;
    max-width: 100% !important;
}
.finderbtn:hover { background: #1d4ed8 !important; }

/* ------------------------------------------------------ start again from ...
   Teal, framed, and at the top: the second way into the page after Generate,
   and in 2.21 it looked like one more grey line. */
.restore {
    border: 2px solid #14b8a6 !important;
    border-radius: 12px !important;
    background: rgba(20, 184, 166, 0.07) !important;
    margin-top: 14px !important;
}
.restore > button, .restore > .label-wrap, .restore button.label-wrap {
    font-size: 1.08em !important; font-weight: 600 !important;
}

/* ---------------------------------------------------------- the big parts
   Each part of the page has a coloured frame. 2px and nearly opaque since
   2.22: at 1px and half-transparent they disappeared on a dark screen. */
.part {
    border: 2px solid rgba(128, 128, 128, 0.6) !important;
    border-radius: 12px !important;
    padding: 0 !important;
    margin-top: 26px !important;
    background: transparent !important;
    overflow: hidden !important;
}
/* Gradio renders a Group as a div inside a div, BOTH carrying the class, so
   the inner one is stripped back to nothing. Measured in the browser. */
.part .part {
    border: none !important;
    padding: 0 !important;
    margin-top: 0 !important;
    border-radius: 0 !important;
}
.part .section { margin-top: 0 !important; }
.part .block.section { padding: 14px 16px 2px 16px !important; }
.part-blue   { border-color: rgba(59, 130, 246, 0.9) !important; }
.part-violet { border-color: rgba(139, 92, 246, 0.9) !important; }
.part-green  { border-color: rgba(34, 197, 94, 0.85) !important; }
.part-amber  { border-color: rgba(245, 158, 11, 0.9) !important; }
.part-pink   { border-color: rgba(236, 72, 153, 0.9) !important; }
.part-cyan   { border-color: rgba(6, 182, 212, 0.9) !important; }
.part-slate  { border-color: rgba(100, 116, 139, 0.9) !important; }
.part .part-blue, .part .part-violet, .part .part-green, .part .part-amber,
.part .part-pink, .part .part-cyan, .part .part-slate { border: none !important; }

.section { margin-top: 30px !important; margin-bottom: 0 !important; }
.section h3 { font-size: 1.3em !important; }
.section-note p { opacity: 0.8; margin-top: 0.2em !important; }

/* ---------------------------------------------------- the settings table
   What it does on the left, the control on the right. The left column gets
   real padding: in 2.21 its text touched the edge of the grey band. */
.setting { border-bottom: 1px solid rgba(128, 128, 128, 0.22); padding: 10px 0 12px 0; }
.setting p { margin: 0.25em 0; }
.explain { padding: 6px 22px 6px 20px !important; line-height: 1.5; }
.explain p { margin: 0.35em 0 !important; }
.readout p { margin: 6px 4px 0 4px !important; font-size: 0.95em; }
.small p { font-size: 0.9em; opacity: 0.85; margin: 4px 4px 0 4px !important; }
.block.readout, .block.small { padding: 4px 12px 8px 12px !important; }

/* The three facts, between Words and Settings. */
.block.goodtoknow {
    border: none !important;
    border-left: 5px solid #eab308 !important;
    background: rgba(234, 179, 8, 0.08) !important;
    border-radius: 10px !important;
    padding: 6px 20px 10px 18px !important;
    margin-top: 26px !important;
}
/* Gradio puts elem_classes on the block AND on the text inside it. */
.prose.goodtoknow { border: none !important; background: transparent !important;
                    padding: 0 !important; margin: 0 !important; }
.goodtoknow h4 { margin: 6px 0 4px 0 !important; }
.goodtoknow li { margin: 4px 0 !important; }

/* Advanced: its own frame, dashed and grey, outside Settings - so it reads as
   "optional, and apart", not as one more row of the table. */
.advanced {
    border: 2px dashed rgba(148, 163, 184, 0.85) !important;
    border-radius: 12px !important;
    background: rgba(148, 163, 184, 0.06) !important;
    margin-top: 18px !important;
}
.advanced > button, .advanced > .label-wrap, .advanced button.label-wrap {
    font-weight: 600 !important;
}

/* The lyric tag buttons: two columns, under the explanation, beside the box
   they write into. Monospace because they type literal text. */
.tagbar {
    display: grid !important;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 6px !important;
    margin-top: 8px;
}
.tagbar button {
    font-family: ui-monospace, Menlo, monospace;
    background: #312e81 !important;
    border: 1px solid #4f46e5 !important;
    color: #e0e7ff !important;
    min-width: 0 !important;
}
.tagbar button:hover { background: #4338ca !important; }

/* Inside a framed part, Gradio draws a player with a 3px white border. */
.player { border: 1px solid rgba(128, 128, 128, 0.35) !important; }
.block.scorenote { padding: 8px 16px 4px 16px !important; }

.totop { margin-top: 18px; }
.runbar.flash { box-shadow: 0 0 0 5px rgba(249, 115, 22, 0.45); transition: box-shadow .2s; }

/* On a narrow window Gradio does NOT stack columns - it squeezes them, and an
   explanation becomes a ribbon of one-word lines. Below 820 pixels the table
   is given up: explanation above, control under. */
@media (max-width: 820px) {
  .setting, .runrow { flex-wrap: wrap !important; }
  .setting > *, .runrow > * { flex: 1 1 100% !important; min-width: 100% !important; }
  .explain { padding: 4px 8px !important; }
  .gobtn { min-height: 64px !important; }
}
"""


def setting(title, info, factory, text_scale=2, control_scale=3):
    """One row of the settings table: explanation on the left, control on the
    right. Returns the control, so wiring elsewhere is unchanged."""
    with gr.Row(equal_height=True, elem_classes=["setting"]):
        with gr.Column(scale=text_scale, min_width=0, elem_classes=["explain"]):
            gr.Markdown(f"**{title}**  \n{info}")
        with gr.Column(scale=control_scale, min_width=0):
            component = factory()
    return component


def explain(title, info, text_scale=2, control_scale=3, equal_height=True):
    """The same row, opened by hand, for the settings that need TWO controls on
    the right. Returns (row, column): fill the column with `with col:` and
    close the row with row.__exit__(None, None, None)."""
    row = gr.Row(equal_height=equal_height, elem_classes=["setting"])
    row.__enter__()
    with gr.Column(scale=text_scale, min_width=0, elem_classes=["explain"]):
        gr.Markdown(f"**{title}**  \n{info}" if title else info)
    col = gr.Column(scale=control_scale, min_width=0)
    return row, col


_OPEN_PARTS = []


def section(text, note="", colour="slate"):
    """Open a framed part of the page, and close the previous one.

    It opens a Group by hand rather than as a `with` block, so the call sites
    keep their indentation - everything between one section() and the next
    belongs to the frame. Call close_sections() once at the end of the layout.
    """
    close_sections()
    group = gr.Group(elem_classes=["part", f"part-{colour}"])
    group.__enter__()
    _OPEN_PARTS.append(group)
    gr.Markdown(f"### {text}", elem_classes=["section"])
    if note:
        gr.Markdown(f"*{note}*", elem_classes=["section-note"])


def close_sections():
    while _OPEN_PARTS:
        _OPEN_PARTS.pop().__exit__(None, None, None)


TO_TOP_LABEL = "\N{UPWARDS BLACK ARROW}\uFE0F  Back up to Generate"

TO_TOP_JS = """() => {
  const bar = document.querySelector('.runbar');
  if (bar) {
    bar.scrollIntoView({ behavior: 'smooth', block: 'start' });
    bar.classList.add('flash');
    setTimeout(() => bar.classList.remove('flash'), 1600);
  } else {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
}"""


# ------------------------------------------------------------- lyric tags --

# The seven tags YuE2 actually recognises, in the order a song uses them.
# Taken from the model's own guidance - inventing a tag it has never seen gets
# you the word sung out loud.
LYRIC_TAGS = ["Intro", "Verse", "Pre-Chorus", "Chorus", "Bridge",
              "Instrumental", "Outro"]


def insert_tag_js(tag, elem_id):
    """Insert a section tag AT THE CURSOR, in the browser.

    This has to run client-side: Gradio hands Python the whole text and never
    the caret position, so the server has no way of knowing where "here" is.

    It TYPES the tag rather than assigning it. `execCommand('insertText')` is
    the browser's own editing path - the same one a real keystroke or a paste
    takes - so the `input` event it raises is a trusted event rather than one
    we fabricate, and the insertion lands on the undo stack: Command-Z takes
    the tag back out. Assigning `.value` directly, which is what this did
    first, wiped the undo history of the whole box.

    The assignment is kept as a fallback for the day execCommand goes away.
    """
    return """() => {
  const ta = document.querySelector('#__ID__ textarea');
  if (!ta) { return; }
  const s = ta.selectionStart, e = ta.selectionEnd;
  const before = ta.value.slice(0, s), after = ta.value.slice(e);
  const nlBefore = (before.length > 0 && !before.endsWith('\\n')) ? '\\n' : '';
  const nlAfter  = (after.length  > 0 && !after.startsWith('\\n')) ? '\\n' : '';
  const ins = nlBefore + '[__TAG__]' + nlAfter;
  ta.focus();
  ta.setSelectionRange(s, e);
  let typed = false;
  try { typed = document.execCommand('insertText', false, ins); } catch (err) { typed = false; }
  if (!typed || ta.value.slice(s, s + ins.length) !== ins) {
    ta.value = before + ins + after;
    const pos = s + ins.length;
    ta.setSelectionRange(pos, pos);
    ta.dispatchEvent(new Event('input', { bubbles: true }));
  }
}""".replace("__TAG__", tag).replace("__ID__", elem_id)


def tag_buttons(elem_id):
    """The tag buttons, two to a row, that write into the textarea with this
    elem_id. The explanation is the caller's (see LYRICS_HOWTO)."""
    with gr.Column(elem_classes=["tagbar"]):
        for tag in LYRIC_TAGS:
            gr.Button(f"[{tag}]", size="sm", variant="secondary",
                      min_width=0).click(fn=None, inputs=None, outputs=None,
                                          js=insert_tag_js(tag, elem_id))


# ------------------------------------------------------ the shared page parts
#
# 2.22: the top half of YuMusic and of Spectrum is built HERE, once. Until
# 2.21 each app carried its own copy of every paragraph, and they had already
# drifted - YuMusic's daring text still talked about "Part 1" and a harmony
# pass it does not have.

MODEL_INFO = (
    "**8-bit**: near bf16 quality, about twice as fast - use this one. **bf16**: the "
    "reference, slowest. **4-bit**: fastest, a little less precise. Only the variants "
    "you have downloaded are listed."
)

PLANNING_INFO = (
    "Keep **Melody + chords**: it is the score that Harmonic daring acts on{extra}. "
    "*Melody only* writes no chords. *None* goes straight to sound - no score to see or "
    "export, and the daring does nothing."
)

SCORE_FILE_INFO = (
    "Renders this `.abc` instead of writing a new score - for a score you edited by hand. "
    "Harmonic daring does nothing then: the score already exists."
)

REFINE_DEFAULT = 32                 # the model's own ode_steps, all three variants
REFINE_INFO = (
    "How many passes turn the planned sound into audio. **32 is the model's own "
    "setting** - leave it there. 16 is faster and may sound rougher; 64 takes about a "
    "third longer (estimated) for a difference you are unlikely to hear."
)

FORMATS_INFO = (
    "The .wav, the .abc score and the .txt of settings are always saved. These add copies "
    "beside them. " + MIDI_INFO
)

RESTORE_INTRO = (
    "Drop **any file of a track you made** - the .wav, .mp3, .txt, the score, the MIDI - "
    "and every setting on this page goes back to what made it{extra}. Change what you want, "
    "then press Generate."
)


def run_bar():
    """The run bar: progress, the batch name, Generate and Stop, the estimate,
    and the button to the run's folder. Returns its components by name."""
    W = {}
    with gr.Column(elem_classes=["runbar"]):
        W["progress_bar"] = gr.HTML(IDLE_PROGRESS)
        with gr.Row(equal_height=False, elem_classes=["runrow"]):
            with gr.Column(scale=3, min_width=280):
                W["batch_name"] = gr.Textbox(
                    value="", label="\N{PUSHPIN} Batch name", lines=1, max_lines=1,
                    placeholder="e.g. glass-piano-night-2", elem_classes=["namebar"])
                gr.Markdown(f"*{BATCH_NAME_HEADER}*", elem_classes=["runhint"])
            with gr.Column(scale=2, min_width=280):
                with gr.Row(elem_classes=["gorow"]):
                    W["generate_btn"] = gr.Button("▶︎  Generate", variant="primary",
                                                  scale=3, min_width=150,
                                                  elem_classes=["gobtn"])
                    W["stop_btn"] = gr.Button("■︎  Stop", variant="stop", scale=1,
                                              min_width=100, elem_classes=["gobtn"])
                W["estimate"] = gr.Markdown(estimate_batch(1, 120, "8bit", 1.0),
                                            elem_classes=["runhint"])
        with gr.Row(equal_height=True, elem_classes=["runfoot"]):
            W["open_run_btn"] = gr.Button(OPEN_RUN_LABEL, scale=0, min_width=0,
                                          elem_classes=["finderbtn"])
            with gr.Column(scale=1, min_width=0):
                W["name_warning"] = gr.Markdown("**Name this run before you start.**")
                W["seed_warning"] = gr.Markdown("")
                # Markdown, not a Textbox: an empty Textbox draws an empty grey box
                # that looks like a control you have failed to fill in.
                W["status_out"] = gr.Markdown("")
    return W


def restore_box(spectrum=False):
    """The "start again from a track" drop zone. Returns (file, note)."""
    with gr.Accordion("\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS}  Start "
                      "again from a track you already made", open=False,
                      elem_classes=["restore"]):
        gr.Markdown(RESTORE_INTRO.format(
            extra=" - the harmony pass too, if you drop a harmonised version"
            if spectrum else ""))
        types = [".txt", ".wav", ".mp3", ".flac", ".abc", ".mid"] + ([".npy"] if spectrum else [])
        restore_file = gr.File(label="Drop a track here (any of its files)", file_types=types,
                               height=130)
        restore_note = gr.Markdown("")
    return restore_file, restore_note


def model_section():
    section("\N{BRAIN} The model", colour="blue")
    labels = available_variants()
    return setting(
        "Model variant", MODEL_INFO,
        lambda: gr.Dropdown(choices=labels or list(MODEL_VARIANTS),
                            value=default_variant(labels),
                            label="Model variant", show_label=False))


def words_section(lyrics_id):
    """Style (with the optional drafter) and Lyrics (with the tag buttons), each
    as a two-column row, then Instrumental. Returns the components by name."""
    section("\N{MEMO} Words", colour="violet")
    W = {}
    with gr.Row(equal_height=False, elem_classes=["setting"]):
        with gr.Column(scale=2, min_width=0, elem_classes=["explain"]):
            gr.Markdown(STYLE_HOWTO)
        with gr.Column(scale=3, min_width=0):
            W["style"] = gr.Textbox(
                label="Style prompt", show_label=False, lines=7, elem_classes=["resizable"],
                placeholder="Style prompt - for example:  Dream pop, {female alto|male "
                            "tenor}, shimmering guitars, slow 4/4, 80 BPM, wistful")
            W["style_note"] = gr.Markdown(prompt_mode_note("Style", ""), elem_classes=["small"])
            choices = draft_choices()
            with gr.Accordion("\N{SPARKLES} Draft it from a rough idea (optional, local "
                              "model)", open=False):
                if choices:
                    gr.Markdown(DRAFT_INTRO)
                    with gr.Row(equal_height=True):
                        W["idea"] = gr.Textbox(label="Rough idea", lines=2, scale=3,
                                               placeholder="a sad the cure track, slow, "
                                                           "with choir at the end")
                        with gr.Column(scale=2, min_width=160):
                            W["draft_model"] = gr.Dropdown(choices, value=choices[0],
                                                           label="Local model")
                            W["draft_btn"] = gr.Button("Write the style prompt",
                                                       variant="secondary")
                    W["draft_note"] = gr.Markdown("")
                else:
                    gr.Markdown(DRAFT_MISSING)
                    W["idea"] = gr.Textbox(visible=False)
                    W["draft_model"] = gr.Dropdown(visible=False)
                    W["draft_btn"] = gr.Button(visible=False)
                    W["draft_note"] = gr.Markdown(visible=False)
    with gr.Row(equal_height=False, elem_classes=["setting"]):
        with gr.Column(scale=2, min_width=0, elem_classes=["explain"]):
            gr.Markdown(LYRICS_HOWTO)
            tag_buttons(lyrics_id)
        with gr.Column(scale=3, min_width=0):
            W["lyrics"] = gr.Textbox(label="Lyrics", show_label=False, lines=11,
                                     elem_id=lyrics_id, elem_classes=["resizable"],
                                     placeholder="Lyrics - for example:\n[Verse]\n...\n"
                                                 "[Chorus]\n...")
            W["lyrics_note"] = gr.Markdown(prompt_mode_note("Lyrics", ""),
                                           elem_classes=["small"])
    row, col = explain("Instrumental", INSTRUMENTAL_INFO)
    with col:
        W["instrumental"] = gr.Checkbox(value=False, label="No voices at all")
        W["instrumental_note"] = gr.Markdown("", elem_classes=["small"])
    row.__exit__(None, None, None)
    return W


def settings_section(spectrum=False):
    """Good to know, then Settings: length, seed, metre, rhythm, daring."""
    close_sections()
    gr.Markdown(good_to_know(spectrum), elem_classes=["goodtoknow"])
    section("\N{WRENCH} Settings", colour="green")
    W = {}
    row, col = explain("Length", LENGTH_INFO)
    with col:
        W["duration_s"] = gr.Number(value=120, precision=0,
                                    label="Target duration, in seconds (max 360)")
        W["fit_label"] = gr.Radio(list(FIT_CHOICES), value=DEFAULT_FIT,
                                  label="When the score is longer than the duration")
        gr.Markdown(FIT_INFO_NOTE, elem_classes=["small"])
    row.__exit__(None, None, None)

    row, col = explain(SEED_TITLE, SEED_INFO)
    with col:
        W["track_seed"] = gr.Number(value=-1, precision=0, label="Seed (-1 = random)")
        W["vary_seed"] = gr.Checkbox(value=True,
                                     label="Add 1 to a fixed seed for each extra track")
    row.__exit__(None, None, None)

    row, col = explain("Metre and tempo", METER_INFO)
    with col:
        W["meter_label"] = gr.Dropdown(list(METERS), value=DEFAULT_METER, label="Metre")
        W["tempo"] = gr.Number(value=0, precision=0, label="Tempo in BPM (0 = the model decides)")
    row.__exit__(None, None, None)

    row, col = explain("Rhythmic complexity", RHYTHM_INFO)
    with col:
        W["rhythm"] = gr.Slider(0, 5, value=0, step=1, label="Rhythmic complexity",
                                show_label=False)
        W["rhythm_note"] = gr.Markdown(rhythm_note(0), elem_classes=["readout"])
    row.__exit__(None, None, None)

    row, col = explain(DARING_TITLE, DARING_INFO_SPECTRUM if spectrum else DARING_INFO)
    with col:
        W["daring"] = gr.Slider(0, 10, value=DARING_DEFAULT, step=1, label=DARING_TITLE,
                                show_label=False)
        W["daring_readout"] = gr.Markdown(daring_meaning(DARING_DEFAULT),
                                          elem_classes=["readout"])
    row.__exit__(None, None, None)
    return W


def advanced_block(spectrum=False):
    """Advanced, in its own dashed frame after Settings."""
    close_sections()
    W = {}
    with gr.Accordion("\N{TEST TUBE}  Advanced - leave alone until everything else is "
                      "settled", open=False, elem_classes=["advanced"]):
        W["style_strength"] = setting(
            STYLE_STRENGTH_TITLE, STYLE_STRENGTH_INFO,
            lambda: gr.Slider(1.0, 3.0, value=1.0, step=0.1, label=STYLE_STRENGTH_TITLE,
                              show_label=False))
        W["planning_label"] = setting(
            "Score planning",
            PLANNING_INFO.format(extra=", and what the harmony pass needs" if spectrum else ""),
            lambda: gr.Radio(list(SCORE_PLANNING), value=DEFAULT_PLANNING,
                             label="Score planning", show_label=False))
        W["score_file"] = setting(
            "Start from an existing score", SCORE_FILE_INFO,
            lambda: gr.File(label="Score (.abc)", file_types=[".abc", ".txt"],
                            show_label=False, height=110))
        W["refine_steps"] = setting(
            "Audio refinement steps", REFINE_INFO,
            lambda: gr.Slider(8, 96, value=REFINE_DEFAULT, step=4,
                              label="Audio refinement steps", show_label=False))
    return W


def to_top_button():
    gr.Button(TO_TOP_LABEL, variant="secondary", elem_classes=["totop"]).click(
        fn=None, inputs=None, outputs=None, js=TO_TOP_JS)


def refine_arg(steps):
    """The --steps to pass: None at the model's own 32, so a track made at the
    default is rendered exactly as before 2.22."""
    try:
        n = int(steps)
    except (TypeError, ValueError):
        return None
    return None if n == REFINE_DEFAULT or n <= 0 else n


def wire_words_and_settings(W, PROMPT, num_tracks):
    """Every small readout on the shared parts. W is the merged dict of
    run_bar(), words_section() and settings_section()."""
    W["daring"].change(daring_meaning, inputs=W["daring"], outputs=W["daring_readout"], **QUIET)
    W["rhythm"].change(rhythm_note, inputs=W["rhythm"], outputs=W["rhythm_note"], **QUIET)
    W["style"].change(lambda v: (PROMPT.edit("style", v), prompt_mode_note("Style", v))[1],
                      inputs=W["style"], outputs=W["style_note"], **QUIET)
    W["lyrics"].change(lambda v: (PROMPT.edit("lyrics", v), prompt_mode_note("Lyrics", v))[1],
                       inputs=W["lyrics"], outputs=W["lyrics_note"], **QUIET)
    for c in (W["style"], W["instrumental"]):
        c.change(instrumental_note, inputs=[W["style"], W["instrumental"]],
                 outputs=W["instrumental_note"], **QUIET)
    W["draft_btn"].click(draft_style, inputs=[W["idea"], W["draft_model"]],
                         outputs=[W["style"], W["draft_note"]])
    W["batch_name"].change(
        lambda v: "" if safe_name(v) else "**Name this run before you start.**",
        inputs=W["batch_name"], outputs=W["name_warning"], **QUIET)
    for c in (W["track_seed"], W["vary_seed"], num_tracks):
        c.change(seed_note, inputs=[W["track_seed"], W["vary_seed"], num_tracks],
                 outputs=W["seed_warning"], **QUIET)


def score_lines(info):
    """What the renderer measured, as lines for the .txt beside the track."""
    if not info or not info.get("bars"):
        return []
    import score_length as SL
    line = (f"Score: {info['bars']} bars, about {SL.clock(info['seconds'])} at "
            f"{info.get('bpm', '?')} BPM")
    if info.get("original_bars"):
        line += (f" (shortened from {info['original_bars']} bars, "
                 f"{SL.clock(info['original_seconds'])})")
    if info.get("audio_seconds") and info["bars"]:
        pct = min(100, round(100 * info["heard_bars"] / info["bars"]))
        line += f"; heard {SL.clock(info['audio_seconds'])} = {pct}% of it"
    return [line]


SCORE_NOTE_IDLE = "*After each track: how long its score is, and how much of it you heard.*"


# ----------------------------------------------------- Gradio's own rubbish --

def sweep_gradio_cache(max_age_hours=6):
    """Delete Gradio's orphaned temporary copies.

    Gradio keeps its own copy of every file it serves to the browser. The
    `delete_cache` argument on Blocks looks like the whole answer and is not:
    it only knows about the files THAT process created, so anything left by a
    previous run - or by a process that was killed rather than shut down -
    stays for ever. Measured here: three days of batches left 6.8 GB behind,
    and a clean restart with delete_cache set removed none of it.

    So each app sweeps the folder itself when it starts. Six hours by default,
    because a player still open in another app points at its cached copy.
    """
    folder = os.environ.get("GRADIO_TEMP_DIR")
    folder = Path(folder) if folder else Path(tempfile.gettempdir()) / "gradio"
    # Never sweep anything that is not literally Gradio's own folder.
    if folder.name != "gradio" or not folder.is_dir():
        return ""
    cutoff = time.time() - max_age_hours * 3600
    freed, count = 0, 0
    for item in folder.rglob("*"):
        try:
            if item.is_symlink() or not item.is_file():
                continue
            if item.stat().st_mtime < cutoff:
                size = item.stat().st_size
                item.unlink()
                freed += size
                count += 1
        except OSError:
            continue
    for item in sorted(folder.rglob("*"), key=lambda q: len(q.parts), reverse=True):
        try:
            if item.is_dir() and not any(item.iterdir()):
                item.rmdir()
        except OSError:
            continue
    if not count:
        return ""
    return (f"Cleared {count} stale Gradio cache file(s), {freed / 1e9:.1f} GB, from "
            f"{folder}. They are copies of files you already have - Gradio makes one "
            f"of everything it shows in the browser and does not tidy up after itself.")


# ------------------------------------------------------- where did it go? --
#
# The question asked after every batch, and the one the interface answered
# worst: the path was printed once in the Log, at the bottom of a long page,
# among two hundred other lines.
#
# Two buttons, because they answer two different questions. In the run bar,
# "where did this RUN go" - the folder, opened. Beside the player, "where is
# the thing I am listening to" - that exact file, selected in its folder.

LAST_RUN = {"dir": None}


def remember_run(path):
    """Called once per run, as soon as the folder exists."""
    LAST_RUN["dir"] = str(path)


def _open_in_finder(path, select=False):
    """`open -R` reveals and selects; `open` on a folder opens it."""
    try:
        args = ["open", "-R", str(path)] if select else ["open", str(path)]
        subprocess.run(args, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def open_run_folder(fallback):
    """The run in progress, or the last one, or the app's output root."""
    target = LAST_RUN["dir"] or str(fallback)
    if not Path(target).exists():
        return f"*Nothing has been rendered yet. `{target}` does not exist.*"
    ok, why = _open_in_finder(target)
    if not ok:
        return f"*Could not open `{target}` - {why}*"
    name = Path(target).name
    return (f"Opened **{name}** in the Finder."
            if LAST_RUN["dir"] else
            f"No run yet this session - opened the output folder, **{name}**.")


def reveal_track(path):
    """Select the file that is playing, inside whatever folder it landed in."""
    if not path:
        return "*Nothing is playing yet.*"
    path = path if isinstance(path, str) else getattr(path, "name", None)
    if not path or not Path(path).exists():
        return "*That file is no longer where it was.*"
    ok, why = _open_in_finder(path, select=True)
    if not ok:
        return f"*Could not reveal it - {why}*"
    return f"Selected **{Path(path).name}** in the Finder."


OPEN_RUN_LABEL = "\N{OPEN FILE FOLDER} Open this run's folder"
REVEAL_LABEL = "\N{OPEN FILE FOLDER} Show the track playing, in the Finder"


# ----------------------------------------------------------- live settings --
#
# Gradio hands a function its inputs ONCE, at the moment the button is pressed.
# For a run of ten tracks that is the wrong moment: by track three you have
# heard three, and you know you want the next one longer, or bolder, or in 3/4.
#
# So every control also writes its value here as you move it, and each track
# reads from here instead of from the arguments the run was launched with.

# Small page updates - a readout, a warning, the estimate, a live setting -
# skip the queue and show no spinner. Queued, they could wait behind a running
# batch and sat there with a spinning "0.0s" beside them (seen on 23 September).
QUIET = {"show_progress": "hidden", "queue": False}


def short(text, n=40):
    """A prompt on one line, cut to n characters - for the Log."""
    one = " ".join((text or "").split())
    return f"'{one[:n]}...'" if len(one) > n else f"'{one}'"


class LiveSettings:
    """One per app. The names must match the generate() parameter names: that
    is what makes seed(locals()) and now() agree about what a setting is
    called."""

    def __init__(self, names):
        self.names = list(names)
        self.values = {}

    def seed(self, values):
        """Press-time values become the starting point, so a control you never
        touch behaves exactly as it always did."""
        self.values = {k: v for k, v in values.items() if k in self.names}

    def now(self, name, fallback=None):
        return self.values.get(name, fallback)

    def bind(self, mapping):
        """Wire every live control to keep this current. Called once, at build
        time."""
        for name, component in mapping.items():
            component.change(lambda v, k=name: self.values.__setitem__(k, v),
                             inputs=component, **QUIET)

    @staticmethod
    def changes(previous, current):
        """A human sentence naming what moved since the last track, or ''.
        Three days later the Log is the only thing that remembers why track 7
        is twice as long as track 6."""
        if not previous:
            return ""
        moved = [f"{k} {previous[k]} -> {v}" for k, v in current.items()
                 if previous.get(k) != v]
        return ("Changed since the last track: " + "; ".join(moved)) if moved else ""


FROZEN_NOTE = (
    "Only the **number of tracks** and the **batch name** are fixed when you press Generate."
)

LIVE_NOTE = (
    "**Everything on this page stays live during a batch.** Change a setting during track "
    "3 and track 4 uses it; the track being made is never disturbed."
)


def good_to_know(spectrum=False):
    """The three facts that change how the page is used, once, above Settings.
    Written on 23 September 2026, the morning the first one was discovered."""
    live = LIVE_NOTE + " " + FROZEN_NOTE
    if spectrum:
        live += " The harmony pass is live too."
    return (
        "#### \N{ELECTRIC LIGHT BULB} Three things worth knowing\n"
        "1. **The duration does not make the music shorter.** The model writes a whole "
        "piece first - usually 2 to 3 minutes - and the duration only says where the sound "
        "stops. At 30 s you hear the intro. *Length*, below, lets you choose.\n"
        "2. **Harmonic daring is the harmony control.** It acts while the score is "
        "written, melody and chords together, so its chords fit the melody." + (" The "
        "harmony pass has its own, separate daring." if spectrum else "")
        + "\n3. " + live
    )


# -------------------------------------------------------- the style drafter --
#
# Optional, and off the critical path: a local model turns "a sad the cure
# track, slow, with choir at the end" into a style prompt YuE2 can act on.
#
# It is NOT a polisher. Its real job is translating a REFERENCE into musical
# facts - a name YuE2 has no idea what to do with becomes "post-punk, early
# 1980s, melancholic male baritone, echoing electric guitar, reverb-drenched
# production". A preset library could never do that.
#
# Measured on 17 September 2026, same system prompt and examples, three runs
# each, on the models already installed:
#
#   gemma2:9b     1.7s   post-punk, baritone, echoing guitar, choral outro
#                        - right every time, and never lost the choir     BEST
#   mistral:7b    1.0s   gothic rock, baritone - right, but terse, and one
#                        run in three dropped the choir
#   qwen3.5:4b    1.1s   synth-pop / dream pop, "breathy male tenor" - plausible
#                        and wrong, and the choir became a texture not an ending
#   qwen3.5:0.8b  1.0s   alternative rock / chamber pop / post-punk, female
#                        soprano then male lead - it simply does not know what
#                        The Cure sounds like                          UNUSABLE
#
# That last line is the finding. The failure of a small model here is not
# formatting, which you can prompt your way out of - it is KNOWLEDGE, which you
# cannot. Anything under about 7B is guessing.

OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")

DRAFT_SYSTEM = """You turn a rough idea into a style prompt for the YuE2 music model.

Output ONE line. Comma separated. English. Nothing else - no preamble, no
quotes, no explanation, no line breaks.

The line lists concrete musical facts in this order: genre, era or aesthetic,
vocal character, lead instrument, other instruments, rhythmic character,
harmonic language, production character, approximate tempo, mood.

Rules:
- Never name a band, artist or song. Translate the reference into the musical
  facts that make it sound like that.
- Never use words about pictures, light, colour, film or video.
- Never use empty praise: beautiful, emotional, amazing, masterpiece.
- Keep anything the user asked for explicitly, including structure.
- Under 60 words."""

DRAFT_SHOTS = [
    ("something like daft punk but sad",
     "French house, late-1990s filtered disco aesthetic, vocoded male vocal, "
     "sidechained analog synth bass, filtered string samples, muted four-on-the-floor "
     "drums, minor-key loops, warm saturated production, 115 BPM, melancholy"),
    ("fast angry guitars, girl singing, 90s",
     "Alternative rock, mid-1990s aesthetic, strained female alto, distorted "
     "double-tracked electric guitars, overdriven bass, driving live drums, "
     "power-chord harmony with minor thirds, dry close production, 160 BPM, furious"),
]

# Best first. Only the ones actually installed are offered.
DRAFT_PREFERRED = ["gemma2:9b", "mistral:7b", "qwen3.5:4b", "gemma2", "mistral"]

DRAFT_INTRO = (
    "Type a rough idea, even with a band name - *a sad Cure track, slow, choir at the "
    "end*. A model on **this Mac** (Ollama) turns it into musical facts YuE2 understands. "
    "Nothing leaves the Mac.  \n"
    "*Use gemma2:9b - smaller models do not know the references and invent another band. "
    "Draft before Generate: during a render both models share the memory.*"
)

DRAFT_MISSING = (
    "**Optional, and not installed.** This needs [Ollama](https://ollama.com) running on "
    "the Mac: install it, then run `ollama pull gemma2:9b` once. Everything else works "
    "without it."
)


def ollama_models():
    """What is installed, or an empty list if Ollama is not there at all."""
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=2) as r:
            return [m["name"] for m in json.load(r).get("models", [])]
    except Exception:
        return []


def draft_choices():
    installed = ollama_models()
    return [m for m in DRAFT_PREFERRED if m in installed] or installed


def draft_style(idea, model):
    """A rough idea in, one line of style prompt out."""
    idea = (idea or "").strip()
    if not idea:
        return gr.update(), "*Type a rough idea first - a band, a mood, anything.*"
    if not model:
        return gr.update(), "*No local model chosen.*"

    messages = [{"role": "system", "content": DRAFT_SYSTEM}]
    for a, b in DRAFT_SHOTS:
        messages += [{"role": "user", "content": a}, {"role": "assistant", "content": b}]
    messages.append({"role": "user", "content": idea})

    body = json.dumps({
        "model": model, "messages": messages, "stream": False, "think": False,
        "options": {"temperature": 0.3, "num_predict": 200},
    }).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            out = json.load(r)["message"]["content"]
    except urllib.error.URLError as exc:
        return gr.update(), (f"*Could not reach Ollama at `{OLLAMA}`. It is optional - "
                             f"everything else works without it. ({exc.reason})*")
    except Exception as exc:
        return gr.update(), f"*The model answered something unusable: {exc}*"

    # One line, whatever it did. A stray blank line would read as a sequential
    # prompt further down the page, which would be a surprising way to lose an
    # afternoon.
    line = " ".join(out.split()).strip().strip('"')
    if not line:
        return gr.update(), "*The model returned nothing. Try again, or another model.*"
    return gr.update(value=line), (
        f"**Read it before you use it** ({model}, {time.time() - started:.1f} s). What you "
        f"did not specify - tempo, bass, drums - is its guess, not yours. Edit freely.")
