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

VERSION = "2.20"
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


PROMPT_HELP = (
    "The prompt tells us how to treat it - there is nothing to choose. "
    "Plain text is used as typed. `{bright|dark}` picks one at random per track. "
    "Several complete versions separated by a line containing only `---` are "
    "used one per track, in order, looping."
)

STYLE_STRENGTH_INFO = (
    "1.0 is off, and fastest. Above 1.0 the render follows the wording of your Style and Lyrics harder, and takes about twice as long."
)

def labelled(title, info, factory, text_scale=2, control_scale=3):
    """Explanation on the left, control on the right, as one block - instead of a
    paragraph stacked on top of a slider, which pushes the value box away from
    its own track and makes a long page longer."""
    with gr.Row(equal_height=True):
        with gr.Column(scale=text_scale, min_width=0):
            gr.Markdown(f"**{title}**  \n{info}")
        with gr.Column(scale=control_scale, min_width=0):
            component = factory()
    return component


def hint(title, text):
    """A one-line explanation ABOVE a slider, so the slider keeps its value box
    tucked against its track instead of being pushed away by its own info text."""
    return f"**{title}** - {text}"


MOTION_INFO = (
    "Replaces repeated chords with chords that lead somewhere. The answer to 'it sounds flat'. 1 puts a dominant before each change."
)

HARMONY_STAFF_INFO = (
    "Adds a staff that spells every chord out as real notes, instead of only naming it above the melody."
)

KEY_CHANGES_INFO = (
    "How often the key moves. 0 never, 1-4 at section boundaries, 5 a repeating sequence."
)

MOD_TYPE_INFO = (
    "Which kind of key change. 'Parallel mode' moves no notes at all; 'Direct lift' raises only the final section."
)

MOVE_MELODY_INFO = (
    "Transpose the written notes along with the chords. Leave this on, or the melody stays behind in the old key."
)

GROUP_SIZE_INFO = (
    "Sequential type only: how many chords go by before the key steps again."
)

DARING_INFO = (
    "How adventurous the model is allowed to be while it writes the score, before any audio exists. 3 is what it ships with."
)

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
            return "Stopping - killing the render in flight. The batch halts right after."
        return "Stopping - the batch halts before the next render starts."

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
                    daring=None, abc_prefix_file=None):
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
    "Also write a .mid beside each track, to drag into GarageBand. Needs abc2midi: brew install abcmidi"
)


def run_folder(root, batch, stamp):
    """A folder for the whole run, so two batches cannot mix."""
    # A folder name may keep its spaces - "Dune Zimmer 20260916-1100" reads
    # better than "Dune_Zimmer...", and only the characters that would break a
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
    "daring":          r'^Harmonic daring:\s*(\d+)',
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
    for tag, field in (("Style", "style"), ("Lyrics", "lyrics")):
        m = re.search(rf'---\s*{tag}.*?---\s*\n(.*?)(?:\n\n---|\Z)', text, re.DOTALL)
        if m:
            info[field] = m.group(1).strip()
    return info


def sidecar_for(path):
    """The .txt that belongs to a track, given ANY of the track's files.

    Drop the wav, the mp3, the score, the MIDI or the .txt itself - they all
    share a stem, so any of them finds the settings. That is the whole point:
    you should not have to remember which file holds what."""
    path = Path(path)
    if path.suffix.lower() == ".txt" and path.exists():
        return path
    stem = path.name
    for suffix in (".latents.npy", ".wav", ".mp3", ".flac", ".abc", ".mid", ".npy"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    else:
        stem = path.stem
    candidate = path.with_name(stem + ".txt")
    return candidate if candidate.exists() else None


def restored_note(path, info):
    """One line saying what came back, and what could not."""
    name = Path(path).name
    if not info:
        return f"*`{name}` does not look like a track written by these apps.*"
    bits = []
    if "seed" in info:
        bits.append(f"seed **{info['seed']}**")
    if "duration_s" in info:
        bits.append(f"{info['duration_s']} s")
    if "daring" in info:
        bits.append(f"daring {info['daring']}")
    if "richness" in info:
        bits.append(f"richness {info['richness']}")
    harmony = " The harmony settings came back too." if "richness" in info else \
              " This is an original take, so the harmony controls were left alone."
    return (f"**Restored from `{name}`** - " + ", ".join(bits) + "." + harmony +
            "\n\nThe style prompt is put back **exactly as it was sent**, so the "
            "Instrumental box and the rhythm slider are left at zero - their wording is "
            "already inside the text, and adding it twice would change the prompt, and a "
            "changed prompt is a different piece. Raise the duration and press Generate: "
            "the composition stays the same, you simply get more of it.")


# ---------------------------------------------------------------- estimates --

# Rough, measured on an M4 Pro: render time as a multiple of the track length,
# plus the model reload every render costs (each render is its own process).
SPEED = {"bf16": 1.6, "8bit": 0.9, "4bit": 0.7}
LOAD_SECONDS = 45


def estimate_batch(renders, duration_s, variant_subdir, cfg_scale=None):
    """A plain-language 'is this an overnight job?' line."""
    renders = max(0, int(renders))
    if renders == 0:
        return "Nothing to render with these settings."
    try:
        dur = float(duration_s) or 60
    except (TypeError, ValueError):
        dur = 60
    per = dur * SPEED.get(variant_subdir, 1.3) + LOAD_SECONDS
    if cfg_scale and float(cfg_scale) > 1.0:
        per = per * 2 - LOAD_SECONDS
    total = renders * per
    hours, minutes = int(total // 3600), int((total % 3600) // 60)
    clock = f"{hours} h {minutes:02d}" if hours else f"{minutes} min"
    return (f"This run will render {renders} audio file"
            f"{'s' if renders != 1 else ''} - roughly {clock} "
            f"(plus the .abc and .txt beside each one).")


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
                    "still switch off. (A sleeping Mac drops the browser connection, which "
                    "is what stopped the run of 15 September at track 13.)")
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
# mean "there are no voices" - and on 16 September a Dune prompt proved the
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

# At the FRONT, not the end. JP found this on 16 September: his own
# "NO SINGER, NO LYRICS" typed at the top of a prompt worked where a sentence
# appended at the bottom had not. The model weighs the opening of a prompt more
# heavily, so a refusal belongs there.
NO_VOICE_HEAD = ("NO SINGER, NO LYRICS, NO VOCALS, NO CHOIR, NO HUMMING, "
                 "NO WORDLESS SINGING.")

INSTRUMENTAL_INFO = (
    "Writes `[instrumental]` as the lyrics and puts a refusal of voices at the **front** of "
    "your style prompt, which is where it works. Both are needed: `[instrumental]` only "
    "means there are no WORDS, and a style prompt that asks for a choir will still get you "
    "one."
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
            + ". A refusal will be added at the end, but the model weighs the whole prompt: "
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
    "Writes the opening lines of the score for the model, so it composes in this metre. "
    "Asking for it in words does not work; this does. Measured: 3/4 holds for a whole "
    "track, every bar. 7/8 obeys for a few bars and then the model writes its own M:4/4 "
    "and goes back to four - it has seen too little seven to stay in it. The tempo box "
    "beside it is honoured whatever the metre."
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
    "Adds wording to the end of your style prompt. This one really is just words - "
    "the model has no rhythm dial - but it is the lever that exists, and it works."
)


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
    "**The track seed is fixed at {seed}.** Every track in this run will be the SAME piece - "
    "a fixed seed reproduces one composition, it does not vary it. That is what you want when "
    "you are developing a track you liked. Set it back to **-1** for a batch of different "
    "pieces."
)

SEED_WARNING_VARY = (
    "**The track seed starts at {seed}** and steps up by one for each track, so you get "
    "{n} different pieces - but a different set from the one -1 would have given you. "
    "Set it to **-1** unless you are deliberately re-running a series."
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
    return (f"**The track seed is fixed at {seed}** - this will reproduce that exact piece. "
            f"Raise the duration and it stays the same composition, only longer.")


PAUSE_INFO = (
    "A rest between tracks, so the machine is quiet for a while and you can use it. "
    "**It is not needed to protect anything, and here is the measurement:** across your own "
    "75-render Dune batch the second half took 326 seconds per render and the first half "
    "took 326 seconds - no slowdown at all. Two other batches actually got faster. A Mac "
    "mini M4 Pro holds its speed all night, and macOS has never recorded a thermal warning "
    "on this machine.\n\nThe one thermal emergency in the logs happened while the Mac was "
    "trying to sleep, with the fans held low - which is exactly what the anti-sleep added in "
    "2.11 now prevents. So leave this at 0 unless you want the noise to stop for a while."
)


NO_NAME_MESSAGE = (
    "**This run has no name.** Give it one in the yellow box above and press Generate "
    "again.\n\nIt takes two seconds now and saves an hour later: the name goes at the "
    "front of every filename and names the folder the whole run is saved in. Without it "
    "you get a folder called `spectrum` among all the other folders called `spectrum`."
)


BATCH_NAME_HEADER = (
    "Your own name for this run. It goes at the front of every filename and names the "
    "folder the run is saved in - so a night's work can still be found in a month."
)

SIMPLE_MODE = "Simple - how many, and how daring"
ADVANCED_MODE = "Advanced - I will set every control myself"
HARMONY_MODES = [SIMPLE_MODE, ADVANCED_MODE]

MUTATION_COUNT_INFO = (
    "How many different harmonisations of each track to render. They come out ordered, "
    "mildest first, so they explain each other - and at 8 or 10 you are exploring a whole "
    "stretch of the ladder in one run."
)

SIMPLE_DARING_INFO = (
    "How far the boldest of them goes. Every step swaps in a different harmonic device "
    "rather than piling on more notes - nothing on this dial is dissonant on purpose."
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
.resizable textarea { resize: vertical !important; min-height: 12em; }

/* The run bar is the only orange thing on the page, so the eye finds it first. */
.runbar { border: 2px solid #f97316; border-radius: 10px; padding: 14px; }
/* ...and Gradio nests the same div twice, so it was drawing that orange line
   twice, one inside the other, with the padding doubled. */
.runbar .runbar { border: none !important; padding: 0 !important; }

/* Air between the name and the two buttons: they were touching, and a box
   pressed against a button reads as one control rather than two. */
.runbar > div > .row { gap: 22px !important; }
.runbar button { margin-bottom: 10px; }
.runbar .namebar { margin-right: 4px; }

/* Gradio renders a one-line Textbox as a <textarea>, so both have to be named
   or the batch name quietly stays the same size as everything else. */
.namebar textarea, .namebar input { font-size: 1.3em !important; padding: 12px 14px !important; }
.namebar label > span { font-size: 1.05em !important; }

/* Every setting is one row of a table: what it does on the left, the control
   that does it on the right. The rule between rows is what keeps a long
   column readable - without it the page is one undifferentiated wall. */
.setting { border-bottom: 1px solid rgba(128,128,128,0.22); padding: 12px 0 14px 0; }
.setting p { margin: 0.2em 0; }

/* Section headings need air above them and none below: a heading belongs to
   what follows it, not to what it interrupts. */
.section { margin-top: 30px !important; margin-bottom: 0 !important; }
.section h3 { font-size: 1.3em !important; }
.section-note p { opacity: 0.8; margin-top: 0.2em !important; }

/* On a narrow window Gradio does NOT stack these columns - it squeezes them,
   and the explanation becomes a 220-pixel ribbon of eight short lines. Below
   820 pixels the table is worth giving up: explanation above, control under.
   Measured, not assumed: at 1000px it stays side by side, at 760px it stacks
   with no horizontal scroll. */
@media (max-width: 820px) {
  .setting { flex-wrap: wrap !important; }
  .setting > * { flex: 1 1 100% !important; min-width: 100% !important; }
}

/* The way back up. It flashes the run bar on arrival, because landing at the
   top of a long page without knowing what moved is its own small confusion. */
.totop { margin-top: 18px; }
.runbar.flash { box-shadow: 0 0 0 5px rgba(249, 115, 22, 0.45); transition: box-shadow .2s; }

/* The lyric tag buttons. Monospace because they insert literal text, and a
   deep indigo because a row of seven grey buttons is a row of seven grey
   buttons - the colour is what makes them read as one tool. */
.tagbar { gap: 6px !important; margin-bottom: 4px; }
.tagbar button {
    font-family: ui-monospace, Menlo, monospace;
    background: #312e81 !important;
    border: 1px solid #4f46e5 !important;
    color: #e0e7ff !important;
}
.tagbar button:hover { background: #4338ca !important; }

/* Each big part of the page gets a thin coloured frame, the way the run bar
   has its orange one. The colour is only there to say "this is one part" -
   the eye finds a boundary faster than it reads a heading. */
.part {
    border: 1px solid rgba(128,128,128,0.35);
    border-radius: 10px;
    padding: 4px 16px 14px 16px;
    margin-top: 24px !important;
    background: transparent !important;
}
/* Gradio renders a Group as a div inside a div, BOTH carrying the class - so
   the frame was being drawn twice, one inside the other, with the padding
   doubled. Measured in the browser, not guessed. */
.part .part {
    border: none !important;
    padding: 0 !important;
    margin-top: 0 !important;
    border-radius: 0 !important;
}
.part .section { margin-top: 14px !important; }
.part-blue   { border-color: rgba(59,130,246,0.55); }
.part-violet { border-color: rgba(139,92,246,0.55); }
.part-green  { border-color: rgba(34,197,94,0.50); }
.part-amber  { border-color: rgba(245,158,11,0.55); }
.part-pink   { border-color: rgba(236,72,153,0.55); }
.part-cyan   { border-color: rgba(6,182,212,0.55); }
.part-slate  { border-color: rgba(100,116,139,0.55); }
"""


def setting(title, info, factory, text_scale=2, control_scale=3):
    """One row of the settings table: explanation on the left, control on the
    right. Returns the control, so wiring elsewhere is unchanged."""
    with gr.Row(equal_height=True, elem_classes=["setting"]):
        with gr.Column(scale=text_scale, min_width=0):
            gr.Markdown(f"**{title}**  \n{info}")
        with gr.Column(scale=control_scale, min_width=0):
            component = factory()
    return component


def explain(title, info, text_scale=2, control_scale=3):
    """The same row, opened by hand, for the settings that need TWO controls on
    the right - metre and tempo, the file formats. Returns (row, column): fill
    the column with `with col:` and close the row with row.__exit__(None, None,
    None)."""
    row = gr.Row(equal_height=True, elem_classes=["setting"])
    row.__enter__()
    with gr.Column(scale=text_scale, min_width=0):
        gr.Markdown(f"**{title}**  \n{info}")
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


TO_TOP_LABEL = "\N{UPWARDS BLACK ARROW}️  That is everything - take me back up to Generate"

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
    Note which way round they are: if BOTH fail the box simply does not change,
    which you can see. The failure that mattered was the opposite one - the tag
    appearing on screen while Gradio never heard about it, and the render going
    out untagged.
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
    """The row of tag buttons that write into the textarea with this elem_id."""
    gr.Markdown("*Click where you want a tag, then press its button. Paste the lyrics "
                "first and tag them afterwards - that is what these are for.*")
    with gr.Row(elem_classes=["tagbar"]):
        for tag in LYRIC_TAGS:
            gr.Button(f"[{tag}]", size="sm", variant="secondary",
                      min_width=90).click(fn=None, inputs=None, outputs=None,
                                          js=insert_tag_js(tag, elem_id))


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
                             inputs=component)

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
    "Two things are frozen once you press Generate, because they decide the shape of the "
    "run rather than of a track: the **number of tracks** and the **batch name**."
)

LIVE_NOTE = (
    "**Everything on this page stays live while a batch runs.** Change the Style, the "
    "Lyrics, the duration, the metre, the daring - even the model - during track 3, and "
    "**track 4 obeys**. The track in flight is never disturbed, nothing has to be "
    "stopped, and the Log writes down what moved and when."
)

STYLE_HOWTO = (
    "**How to write a style prompt.** A list of concrete musical facts, separated by "
    "commas - not sentences, and not a pile of adjectives. Roughly in this order: "
    "**genre**, era or aesthetic, **vocal character**, **instruments**, rhythmic "
    "character, harmonic language, production, approximate tempo, mood.\n\n"
    "> Dark synth-pop, restrained female alto, dry close vocal, analog polysynths, "
    "sequenced bass, sparse electronic drums, minor-key harmony, slow 4/4 pulse around "
    "105 BPM, cool nocturnal production, gradual accumulation of layers.\n\n"
    '*"Beautiful, emotional, amazing" tells the model nothing it can play. "Restrained '
    'female alto, dry close vocal" tells it exactly what to do. And keep musical '
    "direction here: the Lyrics box is for words that get sung, so an instruction "
    "written in it will be sung out loud.*"
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
    "Type the idea however it comes - *a sad the cure track, slow, with choir at the "
    "end* - and a model on **your own machine** turns it into the list of musical facts "
    "above. Nothing leaves the Mac.\n\n"
    "Its real use is **references**: YuE2 does not know who The Cure are, but it knows "
    "what *post-punk, melancholic male baritone, echoing electric guitar, "
    "reverb-drenched production* means. That translation is the whole point.\n\n"
    "*Measured here: `gemma2:9b` got it right three times out of three in about 1.7 "
    "seconds. Anything under 7B did not know the reference and invented a different "
    "band - that is knowledge, not wording, so no amount of instruction fixes it. Draft "
    "BEFORE you press Generate: during a render the two models compete for the same "
    "memory.*"
)

DRAFT_MISSING = (
    "**This is the one optional thing on the page, and it is not installed.**\n\n"
    "It needs [Ollama](https://ollama.com) running locally. Everything else works "
    "perfectly without it - this only drafts a starting point you would otherwise type "
    "yourself.\n\n"
    "To have it: install Ollama, then `ollama pull gemma2:9b` once."
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
        f"**Read it before you use it.** {model} took {time.time() - started:.1f} s, and "
        f"it invented the parts you did not specify - the tempo, the bass, the drum "
        f"character. That is the job, but they are its guesses, not yours. Edit the box "
        f"above freely; nothing is sent until you press Generate.")
