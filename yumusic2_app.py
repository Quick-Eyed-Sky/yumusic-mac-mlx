#!/usr/bin/env python3
"""
YuMusic 2 -- lyrics + style -> song (YuE2-3B, local, via MLX).

The straightforward generator: no mutation, no variants, one track at a time
or a hundred in a row. Everything the 1.x YuMusic did, plus the 2.0 additions:

  - Harmonic daring: opens up the score-planning stage, which ships locked at
    a conservative setting and is the reason the model keeps writing plain
    triads. This acts on the composition itself, before any audio exists.
  - Style strength (CFG): off by default in the model; raising it pushes the
    render harder towards the wording of your Style and Lyrics.
  - Live prompt editing: change Style or Lyrics while a batch is running and
    the next track picks it up. No need to stop and restart.
  - One length control instead of two.
  - Plain-language labels instead of the model's internal flag names.

New in 2.1:
  - The notated pitches move with the key. Until now only the chord LABELS were
    rewritten, so a transposed passage left the melody behind in the old key -
    which is exactly what made the voice fight the chords. Every transposed note
    is written with an explicit accidental, so it can never be misread.
  - Eight kinds of key change instead of one blind shift: parallel mode (which
    moves nothing and is always safe), common tone, diatonic pivot, direct lift
    of the final section, chromatic, enharmonic, sequential, Neapolitan.
  - Locrian no longer flattens the fifth of the home chord, which dissolved the
    key it was supposed to colour.
  - Slider explanations sit above their slider, so the value box stays next to
    the track.

Run:  python3 yumusic2_app.py        (or double-click launch_yumusic2.command)
Port: 7870 by default, override with YUMUSIC2_PORT.
"""

import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import gradio as gr

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import yue_common as C

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)
PORT = int(os.environ.get("YUMUSIC2_PORT", 7870))

STOP = C.StopController()
PROMPT = C.LivePrompt()
PROG = C.BatchProgress()
AWAKE = C.KeepAwake()


def render_score_svg(abc_path):
    """Sheet music preview, if abcm2ps is installed (brew install abcm2ps)."""
    if shutil.which("abcm2ps") is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "score"
        result = subprocess.run(["abcm2ps", "-g", "-O", str(base), str(abc_path)],
                                 capture_output=True, text=True)
        svg_path = base.parent / f"{base.name}001.svg"
        if result.returncode != 0 or not svg_path.exists():
            return None
        svg = svg_path.read_text(encoding="utf-8")
        start = svg.find("<svg")
        return f'<div style="overflow-x:auto">{svg[start:]}</div>' if start != -1 else None


def generate(variant_label, style, lyrics, instrumental, duration_s, meter_label, tempo, rhythm,
             daring, style_strength, planning_label, score_file,
             track_seed, vary_seed, refine_steps,
             num_tracks, batch_name, save_mp3, save_flac, save_midi):
    STOP.clear()
    log, files = [], []
    last_audio, prompt_text, score_html = None, "", ""
    bar = C.IDLE_PROGRESS
    warned = {"done": False}
    midi_warned = {"done": False}

    def out():
        return (last_audio, prompt_text, "\n".join(files),
                "\n".join(log[-300:]), score_html, bar)

    def tick():
        nonlocal bar
        text, frac = PROG.report()
        bar = C.progress_html(text, frac, segments=PROG.total)

    variant = C.MODEL_VARIANTS[variant_label]
    model_path = C.REPO_DIR / variant
    def refuse(short, long=None):
        """Say no where it can be seen.

        Every one of these used to write a line into the Log and stop, and the
        Log is at the bottom of a long page. From the button it was
        indistinguishable from a button that does nothing. gr.Error puts a red
        box in the corner of the screen; the Log still gets the full story."""
        log.append(long or short)
        return gr.Error(short)

    if not C.RUNNER.is_file():
        bar = C.progress_html("Not started - the renderer is missing.", 0.0)
        yield out()
        raise refuse(f"Cannot find the renderer at {C.RUNNER}.")
    if not model_path.exists():
        bar = C.progress_html("Not started - that model variant is not installed.", 0.0)
        yield out()
        raise refuse(f"Model variant '{variant}' is not in {C.REPO_DIR}.")
    if not style.strip():
        bar = C.progress_html("Not started - the Style prompt is empty.", 0.0)
        yield out()
        raise refuse("The Style prompt is empty. It is the one thing the model cannot "
                     "do without.")
    # Lyrics are NOT required when Instrumental is ticked: three lines further
    # down they are replaced by [instrumental] whatever you wrote.
    if not instrumental and not lyrics.strip():
        bar = C.progress_html("Not started - no lyrics, and not marked instrumental.", 0.0)
        yield out()
        raise refuse("Lyrics are empty. Either write some, or tick "
                     "\u201cInstrumental - no voices at all\u201d, which replaces them.")
    if not C.safe_name(batch_name):
        bar = C.progress_html("Not started - this run needs a name.", 0.0)
        yield out()
        raise refuse("This run has no name. Name it in the box beside Generate, at the "
                     "top of the page.",
                     C.NO_NAME_MESSAGE.replace("**", "").replace("`", ""))


    n = max(1, min(100, int(num_tracks)))        # fixed once we start
    PROMPT.start(style, lyrics)
    LIVE.seed(locals())
    live_prev = {}

    note = AWAKE.start()
    if note:
        log.append(note + "\n")
    RUN_DIR = C.run_folder(OUTPUT_DIR, batch_name, time.strftime("%Y%m%d-%H%M"))
    C.remember_run(RUN_DIR)

    PROG.reset(n)
    tick()
    log.append(f"Saving to: {RUN_DIR}")
    log.append(C.estimate_batch(n, duration_s, variant, style_strength) + "\n")
    yield out()

    for i in range(n):
        if STOP.is_set():
            AWAKE.stop()
            bar = C.IDLE_PROGRESS
            log.append(f"Stopped after {i}/{n} track(s).")
            yield out(); return

        # Everything below is read HERE, at the top of this track, not at the
        # top of the run. Move a slider during track 3 and track 4 obeys it.
        variant_label = LIVE.now("variant_label", variant_label)
        variant = C.MODEL_VARIANTS.get(variant_label, variant)
        model_path = C.REPO_DIR / variant
        instrumental = LIVE.now("instrumental", instrumental)
        duration_s = LIVE.now("duration_s", duration_s)
        max_frames = C.frames_from_duration(duration_s)
        meter_label = LIVE.now("meter_label", meter_label)
        tempo = LIVE.now("tempo", tempo)
        rhythm = LIVE.now("rhythm", rhythm)
        daring = LIVE.now("daring", daring)
        style_strength = LIVE.now("style_strength", style_strength)
        planning_label = LIVE.now("planning_label", planning_label)
        planning = C.SCORE_PLANNING.get(planning_label, "full")
        score_file = LIVE.now("score_file", score_file)
        track_seed = LIVE.now("track_seed", track_seed)
        vary_seed = LIVE.now("vary_seed", vary_seed)
        refine_steps = LIVE.now("refine_steps", refine_steps)
        save_mp3 = LIVE.now("save_mp3", save_mp3)
        save_flac = LIVE.now("save_flac", save_flac)
        save_midi = LIVE.now("save_midi", save_midi)

        # Say out loud what moved. Three days later the Log is the only thing
        # that remembers why track 7 is twice as long as track 6.
        live_state = {
            "model": variant_label,
            "duration (s)": int(duration_s or 0),
            "metre": meter_label,
            "tempo": int(tempo or 0),
            "rhythmic complexity": int(rhythm),
            "harmonic daring": int(daring),
            "style strength": round(float(style_strength), 2),
            "score planning": planning_label,
            "instrumental": bool(instrumental),
        }
        moved = C.LiveSettings.changes(live_prev, live_state)
        if moved:
            log.append(moved)
        live_prev = live_state

        # The metre seed is written per track now, because the metre is live.
        meter = C.METERS.get(meter_label)
        prefix_text = C.abc_prefix_for(meter, tempo if tempo and int(tempo) > 0 else None)
        prefix_file = None
        if prefix_text and planning != "off":
            prefix_file = OUTPUT_DIR / f".tmp_meterseed_{int(time.time() * 1000)}.abc"
            prefix_file.write_text(prefix_text, encoding="utf-8")

        track_style = C.apply_instrumental(
            C.apply_rhythm(PROMPT.resolve("style", i), rhythm), instrumental)
        track_lyrics = "[instrumental]" if instrumental else PROMPT.resolve("lyrics", i)
        seed = (random.randint(0, 2**31 - 1) if int(track_seed) < 0
                else (int(track_seed) + i if vary_seed else int(track_seed)))
        prompt_text = (f"Track {i + 1}/{n} - seed {seed}\n\n"
                       f"Style:\n{track_style}\n\nLyrics:\n{track_lyrics}")

        stamp = time.strftime("%Y%m%d-%H%M%S")
        stem = C.stem_with_batch(batch_name,
                                  f"yumusic2_{stamp}_track{i + 1}of{n}_seed{seed}")
        wav = RUN_DIR / f"{stem}.wav"
        txt = RUN_DIR / f"{stem}.txt"
        lyr = RUN_DIR / f".tmp_lyrics_{stamp}_{i + 1}.txt"
        lyr.write_text(track_lyrics, encoding="utf-8")

        cmd = C.render_command(model_path=model_path, style=track_style, lyrics_file=lyr,
                                out_path=wav, seed=seed, planning=planning,
                                abc_file=score_file, cfg_scale=style_strength,
                                steps=refine_steps, max_frames=max_frames, daring=daring,
                                abc_prefix_file=prefix_file)

        PROG.begin(f"track {i + 1}/{n}", max_frames)
        tick()
        log.append(f"=== Track {i + 1}/{n} - seed {seed} ===")
        yield out()

        for line in STOP.run(cmd, C.REPO_DIR):
            log.append(line)
            PROG.note_line(line)
            tick()
            yield out()

        lyr.unlink(missing_ok=True)
        if prefix_file:
            prefix_file.unlink(missing_ok=True)
        if STOP.is_set():
            AWAKE.stop()
            bar = C.IDLE_PROGRESS
            log.append(f"Stopped during track {i + 1}/{n}.")
            yield out(); return
        if getattr(STOP, "returncode", 1) != 0 or not wav.exists():
            AWAKE.stop()
            log.append(f"Track {i + 1} failed - stopping the batch.")
            yield out(); return
        PROG.finish(); tick()

        t, p, k = C.daring_to_sampling(daring)
        C.write_sidecar(txt, title=f"YuMusic 2 - track {i + 1}/{n}", style=track_style,
                        lyrics=track_lyrics, lines=[
            f"Model variant: {variant}",
            f"Seed: {seed}",
            f"Score planning: {planning_label}",
            f"Harmonic daring: {daring} (temperature {t:.3f}, top_p {p:.3f}, top_k {k})",
            f"Style strength (CFG): {style_strength}",
            f"Target duration (s): {int(duration_s) if duration_s else '(model default)'}",
            f"Metre: {meter or 'model default'}"
            + (f", tempo {int(tempo)}" if tempo and int(tempo) > 0 else ""),
            f"Rhythmic complexity: {int(rhythm)}",
        ])

        abc = wav.with_suffix(".abc")
        extra, warn = C.convert_audio(wav, save_mp3, save_flac)
        if warn:
            log.append(warn)
        if save_midi:
            midi, midi_warn = C.convert_midi(abc)
            if midi_warn and not midi_warned["done"]:
                midi_warned["done"] = True
                log.append(midi_warn)
            if midi:
                extra = extra + [midi]

        C.label_group([wav, txt, abc] + extra, "green", warned, log)

        if abc.exists():
            svg = render_score_svg(abc)
            score_html = svg or (f"<p><em>Score saved as {abc.name}. Install abcm2ps "
                                 f"(brew install abcm2ps) to preview it here.</em></p>")
        else:
            score_html = "<p><em>No score for this track (score planning was off).</em></p>"

        last_audio = str(wav)
        files.append(str(wav))
        files.extend(str(p) for p in extra)
        suffix = f" (+ {', '.join(p.name for p in extra)})" if extra else ""
        log.append(f"Saved: {wav.name} (+ {txt.name}){suffix}\n")
        yield out()

    bar = C.progress_html(f"**Done** - {n} track(s) in "
                          f"{C._clock(time.time() - PROG.started)}.", 1.0,
                          done=True, segments=n)
    AWAKE.stop()
    log.append(f"Done - {n} track(s) in {RUN_DIR}")
    yield out()


# The look of the page, the settings table, the lyric tag buttons and the style
# drafter all live in yue_common now, so the three apps cannot drift apart.
CSS = C.UI_CSS
setting, explain, section = C.setting, C.explain, C.section

# The names must match generate()'s parameter names: that is what makes
# LIVE.seed(locals()) and LIVE.now() agree about what a setting is called.
# Number of tracks and Batch name are absent on purpose - they decide the shape
# of the run, not of a track.
LIVE = C.LiveSettings([
    "variant_label", "instrumental", "duration_s", "meter_label", "tempo",
    "rhythm", "daring", "style_strength", "planning_label", "score_file",
    "track_seed", "vary_seed", "refine_steps",
    "save_mp3", "save_flac", "save_midi",
])


# Gradio keeps its own copy of every file it serves to the browser, in a
# temporary folder, and never tidies up on its own: three days of batches left
# 384 wav copies and 6.8 GB behind. delete_cache says "every hour, throw away
# copies older than six" - six because a player still on screen points at its
# cached copy, and deleting that from under it would 404 a track you were about
# to replay. Restarting the app clears the whole cache outright, which is
# Gradio's own documented behaviour.
with gr.Blocks(title="YuMusic 2", delete_cache=(3600, 21600)) as demo:
    gr.Markdown(C.app_header(
        "YuMusic",
        f"Lyrics + style into a song, locally, with the score-planning stage opened up. "
        f"Tracks are saved to `{OUTPUT_DIR}`."))

    # ------------------------------------------------------------- the run bar
    #
    # Two columns on purpose: the name of the run on the left, because that is
    # what gets forgotten, and the two buttons on the right where the hand
    # already is. The progress bar spans both - it belongs to the whole run,
    # not to either column.
    with gr.Group(elem_classes=["runbar"]):
        progress_bar = gr.HTML(C.IDLE_PROGRESS)
        with gr.Row(equal_height=True):
            with gr.Column(scale=3, min_width=0):
                with gr.Group(elem_classes=["namebar"]):
                    batch_name = gr.Textbox(
                        value="", label="\N{PUSHPIN} Batch name",
                        placeholder="e.g. glass-piano-night-2")
                gr.Markdown(f"*{C.BATCH_NAME_HEADER}*")
            with gr.Column(scale=2, min_width=0):
                generate_btn = gr.Button("Generate", variant="primary")
                stop_btn = gr.Button("Stop", variant="stop")
                estimate = gr.Markdown(C.estimate_batch(1, 120, "8bit", 1.0))
        with gr.Row():
            open_run_btn = gr.Button(C.OPEN_RUN_LABEL, size="sm", variant="secondary")
        name_warning = gr.Markdown("**Name this run before you start.**")
        seed_warning = gr.Markdown("")
        # Markdown, not a Textbox: an empty Textbox draws an empty grey box that
        # looks like a control you have failed to fill in. Empty Markdown draws
        # nothing at all, which is the honest rendering of "no status yet".
        status_out = gr.Markdown("")

    with gr.Accordion("Start again from a track you already made", open=False):
        gr.Markdown(
            "Drop **any file belonging to a track** - the `.txt`, the `.wav`, the `.mp3`, "
            "the score - and every control goes back to what made it. Change what you want "
            "and press Generate.\n\n"
            "*The seed is what reproduces the piece: leave it alone and raise the duration, "
            "and you get the same composition, longer.*")
        restore_file = gr.File(label="Drop a track (any of its files)",
                                file_types=[".txt", ".wav", ".mp3", ".flac", ".abc", ".mid"])
        restore_note = gr.Markdown("")

    # ---------------------------------------------------------------- the model
    section("\N{BRAIN} The model", colour="blue")
    _labels = C.available_variants()
    variant_label = setting(
        "Model variant",
        "8-bit is the one to use: near bf16 quality and about twice as fast. bf16 is the "
        "reference and the slowest. 4-bit is fastest and drifts. Only the variants actually "
        "downloaded are listed.",
        lambda: gr.Dropdown(choices=_labels or list(C.MODEL_VARIANTS),
                            value=C.default_variant(_labels),
                            label="Model variant", show_label=False))

    # ----------------------------------------------------------------- the words
    section("\N{MEMO} Words", C.PROMPT_HELP, colour="violet")

    gr.Markdown(
        "**Everything on this page stays live while a batch runs.** Change the Style, the "
        "Lyrics, the duration, the metre, the daring - even the model - during track 3, and "
        "**track 4 obeys**. The track in flight is never disturbed, nothing has to be "
        "stopped, and the Log writes down what moved and when.\n\n"
        "*That is what a ten-track run is for: three short ones to hear where it is going, "
        "then lengthen the duration and rework the words as you learn what the piece wants. "
        "Two things are frozen once you press Generate, because they decide the shape of the "
        "run rather than of a track: the **number of tracks** and the **batch name**.*")

    gr.Markdown(
        "**How to write a style prompt.** A list of concrete musical facts, separated by "
        "commas - not sentences, and not a pile of adjectives. Roughly in this order: "
        "**genre**, era or aesthetic, **vocal character**, **instruments**, rhythmic "
        "character, harmonic language, production, approximate tempo, mood.\n\n"
        "> Dark synth-pop, restrained female alto, dry close vocal, analog polysynths, "
        "sequenced bass, sparse electronic drums, minor-key harmony, slow 4/4 pulse around "
        "105 BPM, cool nocturnal production, gradual accumulation of layers.\n\n"
        "*\"Beautiful, emotional, amazing\" tells the model nothing it can play. "
        "\"Restrained female alto, dry close vocal\" tells it exactly what to do. And keep "
        "musical direction here: the Lyrics box is for words that get sung, so an "
        "instruction written in it will be sung out loud.*")

    _choices = C.draft_choices()
    with gr.Accordion("\N{SPARKLES} Or let a local model draft one for you "
                      "(optional)", open=False):
        if _choices:
            gr.Markdown(
                "Type the idea however it comes - *a sad the cure track, slow, with choir "
                "at the end* - and a model on **your own machine** turns it into the list "
                "of musical facts above. Nothing leaves the Mac.\n\n"
                "Its real use is **references**: YuE2 does not know who The Cure are, but "
                "it knows what *post-punk, melancholic male baritone, echoing electric "
                "guitar, reverb-drenched production* means. That translation is the whole "
                "point.\n\n"
                "*Measured here: `gemma2:9b` got it right three times out of three in "
                "about 1.7 seconds. Anything under 7B did not know the reference and "
                "invented a different band - that is knowledge, not wording, so no amount "
                "of instruction fixes it. Draft BEFORE you press Generate: during a render "
                "the two models compete for the same memory.*")
            with gr.Row(equal_height=True):
                idea = gr.Textbox(label="Rough idea", lines=2, scale=3,
                                  placeholder="a sad the cure track, slow, with choir at the end")
                with gr.Column(scale=1, min_width=0):
                    draft_model = gr.Dropdown(_choices, value=_choices[0] if _choices else None,
                                              label="Local model")
                    draft_btn = gr.Button("Write the style prompt", variant="secondary")
            draft_note = gr.Markdown("")
        else:
            gr.Markdown(
                "**This is the one optional thing on the page, and it is not installed.**\n\n"
                "It needs [Ollama](https://ollama.com) running locally. Everything else in "
                "YuMusic works perfectly without it - this only drafts a starting point you "
                "would otherwise type yourself.\n\n"
                "To have it: install Ollama, then `ollama pull gemma2:9b` once.")
            idea = gr.Textbox(visible=False)
            draft_model = gr.Dropdown(visible=False)
            draft_btn = gr.Button(visible=False)
            draft_note = gr.Markdown(visible=False)

    style = gr.Textbox(label="Style prompt", lines=7, elem_classes=["resizable"],
                       placeholder="English, {indie pop|dream pop}, bright acoustic guitar, female vocals",
                       info="Editable while a batch runs - the change lands on the next track.")
    style_note = gr.Markdown(C.prompt_mode_note("Style", ""))

    C.tag_buttons("yum_lyrics")

    lyrics = gr.Textbox(label="Lyrics", lines=7, elem_id="yum_lyrics",
                        elem_classes=["resizable"],
                        placeholder="[Verse]\n...\n[Chorus]\n...",
                        info="Editable while a batch runs - the change lands on the next track.")
    lyrics_note = gr.Markdown(C.prompt_mode_note("Lyrics", ""))

    instrumental = setting(
        "Instrumental", C.INSTRUMENTAL_INFO,
        lambda: gr.Checkbox(value=False, label="No voices at all"))

    # -------------------------------------------------------------- the settings
    section("\N{WRENCH} Settings", colour="green")

    duration_s = setting(
        "Target duration",
        "In seconds, and the only length control there is. The model's own ceiling is six "
        "minutes - 360 seconds - and asking for more simply stops at six.",
        lambda: gr.Number(value=120, precision=0, label="Seconds", show_label=False))

    track_seed = setting(
        "Track seed",
        "-1 gives every track a new random seed. Type a number instead to reproduce a track "
        "you liked - every track writes its own seed into its .txt file.",
        lambda: gr.Number(value=-1, precision=0, label="Track seed", show_label=False))

    vary_seed = setting(
        "Walk the seed",
        "With a fixed seed, add 1 for each extra track: same family, real variation. "
        "Ignored while the seed is -1, which is already random every time.",
        lambda: gr.Checkbox(value=True, label="Add 1 to the seed for each extra track"))

    _row, _col = explain("Metre and tempo", C.METER_INFO)
    with _col:
        meter_label = gr.Dropdown(list(C.METERS), value=C.DEFAULT_METER, label="Metre")
        tempo = gr.Number(value=0, precision=0, label="Tempo (0 = model decides)")
    _row.__exit__(None, None, None)

    rhythm = setting(
        "Rhythmic complexity", C.RHYTHM_INFO,
        lambda: gr.Slider(0, 5, value=0, step=1, label="Rhythmic complexity",
                          show_label=False))

    _row, _col = explain("Harmonic daring", C.DARING_INFO)
    with _col:
        daring = gr.Slider(0, 10, value=C.DARING_DEFAULT, step=1,
                           label="Harmonic daring", show_label=False)
        daring_readout = gr.Markdown(f"`{C.daring_label(C.DARING_DEFAULT)}`")
    _row.__exit__(None, None, None)

    style_strength = setting(
        "Style strength", C.STYLE_STRENGTH_INFO,
        lambda: gr.Slider(1.0, 3.0, value=1.0, step=0.1, label="Style strength",
                          show_label=False))

    # -------------------------------------------------------------------- advanced
    with gr.Accordion("\N{TEST TUBE} Advanced - open only when everything else is settled",
                      open=False):
        planning_label = setting(
            "Score planning",
            "The model writes a score before it renders any audio. **Melody + chords** is "
            "what harmonic work needs, and what Harmonic daring acts on. Turning it off "
            "goes straight to sound, and there is then no score to look at or export.",
            lambda: gr.Radio(list(C.SCORE_PLANNING), value=C.DEFAULT_PLANNING,
                             label="Score planning", show_label=False))
        score_file = setting(
            "Start from an existing score",
            "Hand it an `.abc` file and it renders that score instead of writing a new one. "
            "This is how a score you have edited by hand gets back into the model.",
            lambda: gr.File(label="Score (.abc)", file_types=[".abc", ".txt"],
                            show_label=False))
        refine_steps = setting(
            "Audio refinement steps",
            "Leave blank for the model's own default. More steps means a longer render for "
            "a difference you may not hear.",
            lambda: gr.Number(value=None, precision=0, label="Steps", show_label=False))

    # ------------------------------------------------------------------- the run
    section("\N{PACKAGE} The run", colour="amber")

    num_tracks = setting(
        "Number of tracks",
        "How many renders this run makes, each with its own seed. They all land in one "
        "folder named after the batch name above.",
        lambda: gr.Slider(1, 100, value=1, step=1, label="Number of tracks",
                          show_label=False))

    _row, _col = explain(
        "Extra formats",
        "The .wav, the .abc score and the .txt settings are always written. These are "
        "copies beside them.  \n" + C.MIDI_INFO)
    with _col:
        save_mp3 = gr.Checkbox(label="Also save MP3")
        save_flac = gr.Checkbox(label="Also save FLAC")
        save_midi = gr.Checkbox(label="Also save MIDI (opens in GarageBand)")
    _row.__exit__(None, None, None)

    gr.Button("\N{UPWARDS BLACK ARROW}\uFE0F  That is everything - take me back up to Generate",
              variant="secondary", elem_classes=["totop"]).click(
        fn=None, inputs=None, outputs=None,
        js="""() => {
  const bar = document.querySelector('.runbar');
  if (bar) {
    bar.scrollIntoView({ behavior: 'smooth', block: 'start' });
    bar.classList.add('flash');
    setTimeout(() => bar.classList.remove('flash'), 1600);
  } else {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
}""")

    # --------------------------------------------------------------- the results
    section("\N{SPEAKER WITH THREE SOUND WAVES} Results", colour="slate")
    audio_out = gr.Audio(label="Latest track", type="filepath")
    reveal_btn = gr.Button(C.REVEAL_LABEL, size="sm", variant="secondary")
    where_note = gr.Markdown("")
    with gr.Row():
        prompt_out = gr.Textbox(label="Resolved prompt (track in progress)", lines=9,
                                 elem_classes=["resizable"], scale=1)
        files_out = gr.Textbox(label="Files saved this run", lines=9, scale=1)
    log_out = gr.Textbox(label="Log", lines=16, max_lines=40)
    with gr.Accordion("Sheet music of the latest track", open=False):
        score_out = gr.HTML()

    C.close_sections()

    # ------------------------------------------------------------------ wiring
    RESTORE = [variant_label, style, lyrics, instrumental, duration_s, meter_label, tempo,
               rhythm, daring, style_strength, planning_label, track_seed, batch_name,
               vary_seed]

    def restore(f):
        blank = [gr.update()] * len(RESTORE)
        if not f:
            return (*blank, "")
        path = f if isinstance(f, str) else getattr(f, "name", None)
        side = C.sidecar_for(path) if path else None
        if side is None:
            return (*blank, f"*No settings file found beside `{Path(path).name}`.*")
        info = C.read_sidecar(side.read_text(encoding="utf-8"))
        if not info:
            return (*blank, C.restored_note(side, info))
        labels_by_dir = {v: k for k, v in C.MODEL_VARIANTS.items()}
        meter_value = next((k for k, v in C.METERS.items()
                            if (v or "model default") == info.get("meter", "")), None)

        def num(key, cast=int):
            try:
                return gr.update(value=cast(info[key]))
            except (KeyError, ValueError, TypeError):
                return gr.update()

        PROMPT.edit("style", info.get("style", ""))
        PROMPT.edit("lyrics", info.get("lyrics", ""))
        return (
            gr.update(value=labels_by_dir[info["model_variant"]])
            if info.get("model_variant") in labels_by_dir else gr.update(),
            gr.update(value=info["style"]) if "style" in info else gr.update(),
            gr.update(value=info.get("lyrics", "")) if "lyrics" in info else gr.update(),
            # Left OFF on purpose - see the note in yue_common.apply_instrumental().
            gr.update(value=False),
            num("duration_s"), gr.update(value=meter_value) if meter_value else gr.update(),
            num("tempo"), gr.update(value=0), num("daring"), num("style_strength", float),
            gr.update(value=info["planning"]) if info.get("planning") in C.SCORE_PLANNING
            else gr.update(),
            num("seed"),
            gr.update(value=info.get("batch_name", "")) if "batch_name" in info else gr.update(),
            gr.update(value=False),
            C.restored_note(side, info),
        )

    restore_file.change(restore, inputs=restore_file, outputs=RESTORE + [restore_note])

    batch_name.change(
        lambda v: "" if C.safe_name(v) else "**Name this run before you start.**",
        inputs=batch_name, outputs=name_warning)

    for _c in (track_seed, vary_seed, num_tracks):
        _c.change(C.seed_note, inputs=[track_seed, vary_seed, num_tracks],
                  outputs=seed_warning)

    # See LIVE, at the top of this file, for why these two lists must agree.
    LIVE.bind({
        "variant_label": variant_label, "instrumental": instrumental,
        "duration_s": duration_s, "meter_label": meter_label, "tempo": tempo,
        "rhythm": rhythm, "daring": daring, "style_strength": style_strength,
        "planning_label": planning_label, "score_file": score_file,
        "track_seed": track_seed, "vary_seed": vary_seed, "refine_steps": refine_steps,
        "save_mp3": save_mp3, "save_flac": save_flac, "save_midi": save_midi,
    })

    open_run_btn.click(lambda: C.open_run_folder(OUTPUT_DIR), outputs=status_out)
    reveal_btn.click(C.reveal_track, inputs=audio_out, outputs=where_note)

    draft_btn.click(C.draft_style, inputs=[idea, draft_model], outputs=[style, draft_note])

    daring.change(lambda d: f"`{C.daring_label(d)}`", inputs=daring, outputs=daring_readout)
    style.change(lambda v: (PROMPT.edit("style", v), C.prompt_mode_note("Style", v))[1],
                 inputs=style, outputs=style_note)
    lyrics.change(lambda v: (PROMPT.edit("lyrics", v), C.prompt_mode_note("Lyrics", v))[1],
                  inputs=lyrics, outputs=lyrics_note)

    def refresh_estimate(n, dur, variant_label, strength):
        return C.estimate_batch(n, dur, C.MODEL_VARIANTS.get(variant_label, "8bit"), strength)

    for ctrl in (num_tracks, duration_s, variant_label, style_strength):
        ctrl.change(refresh_estimate, inputs=[num_tracks, duration_s, variant_label, style_strength],
                    outputs=estimate)

    run = generate_btn.click(
        generate,
        inputs=[variant_label, style, lyrics, instrumental, duration_s, meter_label, tempo, rhythm,
                daring, style_strength, planning_label, score_file,
                track_seed, vary_seed, refine_steps,
                num_tracks, batch_name, save_mp3, save_flac, save_midi],
        outputs=[audio_out, prompt_out, files_out, log_out, score_out, progress_bar])
    stop_btn.click(STOP.request, inputs=None, outputs=status_out, cancels=[run])

if __name__ == "__main__":
    note = C.sweep_gradio_cache()
    if note:
        print(note)
    demo.launch(server_port=PORT, css=CSS)
