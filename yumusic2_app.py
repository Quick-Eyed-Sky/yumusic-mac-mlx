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

New in 2.22:
  - Length: the model always writes a whole piece and the duration only cut
    the sound. The page now says, after every track, how long the score was
    and how much of it was heard - and a new choice decides what happens when
    the score is longer: stop the sound (as before), shorten the score to end
    at a section boundary, or play the whole score.
  - Harmonic daring shows what to expect, measured on 181 tracks, instead of
    the sampling numbers behind it. Style strength is renamed Style-prompt
    fidelity and moved to Advanced until it has been tested.
  - The top half of the page is built by yue_common, shared with Spectrum.

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
import score_length as SL

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


def generate(variant_label, style, lyrics, instrumental, duration_s, fit_label, meter_label,
             tempo, rhythm, daring, style_strength, planning_label, score_file,
             track_seed, vary_seed, refine_steps,
             num_tracks, batch_name, save_mp3, save_flac, save_midi):
    STOP.clear()
    log, files = [], []
    last_audio, prompt_text, score_html = None, "", ""
    score_md = C.SCORE_NOTE_IDLE
    bar = C.IDLE_PROGRESS
    warned = {"done": False}
    midi_warned = {"done": False}

    def out():
        return (last_audio, prompt_text, "\n".join(files),
                "\n".join(log[-300:]), score_html, bar, score_md)

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
    log.append(C.estimate_batch(n, duration_s, variant, style_strength,
                                C.fit_mode(fit_label)).replace("**", "") + "\n")
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
        fit_label = LIVE.now("fit_label", fit_label)
        fit = C.fit_mode(fit_label)
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
            "score longer than the duration": fit,
            "metre": meter_label,
            "tempo": int(tempo or 0),
            "rhythmic complexity": int(rhythm),
            "harmonic daring": int(daring),
            "style-prompt fidelity": round(float(style_strength), 2),
            "score planning": planning_label,
            "instrumental": bool(instrumental),
            "refinement steps": int(refine_steps or C.REFINE_DEFAULT),
            # The words, shortened: enough for the Log to say they were edited.
            "style prompt": C.short(PROMPT.text.get("style", "")),
            "lyrics": C.short(PROMPT.text.get("lyrics", "")),
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
                                steps=C.refine_arg(refine_steps), max_frames=max_frames,
                                daring=daring, abc_prefix_file=prefix_file, fit=fit)

        PROG.begin(f"track {i + 1}/{n}", max_frames)
        tick()
        log.append(f"=== Track {i + 1}/{n} - seed {seed} ===")
        yield out()

        score_info = None
        for line in STOP.run(cmd, C.REPO_DIR):
            data = SL.read_data_line(line)
            if data is not None:
                score_info = data          # the machine line stays out of the Log
                continue
            if line.startswith("[score-frames]"):
                PROG.frames = int(line.split()[1])
                continue
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
            f"Score fit: {fit}",
            f"Metre: {meter or 'model default'}"
            + (f", tempo {int(tempo)}" if tempo and int(tempo) > 0 else ""),
            f"Rhythmic complexity: {int(rhythm)}",
            f"Audio refinement steps: {int(refine_steps or C.REFINE_DEFAULT)}",
        ] + C.score_lines(score_info))

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

        score_md = SL.describe(score_info) or "*No score to measure for this track.*"
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


# The look of the page and every part of its top half live in yue_common, so
# YuMusic and Spectrum cannot drift apart.
CSS = C.UI_CSS
setting, explain, section = C.setting, C.explain, C.section

# The names must match generate()'s parameter names: that is what makes
# LIVE.seed(locals()) and LIVE.now() agree about what a setting is called.
# Number of tracks and Batch name are absent on purpose - they decide the shape
# of the run, not of a track.
LIVE = C.LiveSettings([
    "variant_label", "instrumental", "duration_s", "fit_label", "meter_label", "tempo",
    "rhythm", "daring", "style_strength", "planning_label", "score_file",
    "track_seed", "vary_seed", "refine_steps",
    "save_mp3", "save_flac", "save_midi",
])


# Gradio keeps its own copy of every file it serves to the browser, in a
# temporary folder, and never tidies up on its own: three days of batches left
# 384 wav copies and 6.8 GB behind. delete_cache says "every hour, throw away
# copies older than six" - six because a player still on screen points at its
# cached copy. Restarting the app clears the whole cache outright.
with gr.Blocks(title="YuMusic 2", delete_cache=(3600, 21600)) as demo:
    gr.Markdown(C.app_header(
        "YuMusic",
        f"Style + lyrics into a song, on this Mac. Tracks are saved in `{OUTPUT_DIR}`."))

    R = C.run_bar()
    progress_bar, batch_name = R["progress_bar"], R["batch_name"]
    generate_btn, stop_btn, estimate = R["generate_btn"], R["stop_btn"], R["estimate"]
    status_out = R["status_out"]

    restore_file, restore_note = C.restore_box()

    variant_label = C.model_section()

    WD = C.words_section("yum_lyrics")
    style, lyrics, instrumental = WD["style"], WD["lyrics"], WD["instrumental"]

    S = C.settings_section()
    duration_s, fit_label = S["duration_s"], S["fit_label"]
    track_seed, vary_seed = S["track_seed"], S["vary_seed"]
    meter_label, tempo, rhythm, daring = S["meter_label"], S["tempo"], S["rhythm"], S["daring"]

    A = C.advanced_block()
    style_strength, planning_label = A["style_strength"], A["planning_label"]
    score_file, refine_steps = A["score_file"], A["refine_steps"]

    # ------------------------------------------------------------------- the run
    section("\N{PACKAGE} The run", colour="amber")

    num_tracks = setting(
        "Number of tracks",
        "How many pieces this run makes, each with its own seed. They all go into one "
        "folder named after the batch.",
        lambda: gr.Slider(1, 100, value=1, step=1, label="Number of tracks",
                          show_label=False))

    _row, _col = explain("Extra formats", C.FORMATS_INFO)
    with _col:
        save_mp3 = gr.Checkbox(label="Also save MP3")
        save_flac = gr.Checkbox(label="Also save FLAC")
        save_midi = gr.Checkbox(label="Also save MIDI (opens in GarageBand)")
    _row.__exit__(None, None, None)

    C.to_top_button()

    # --------------------------------------------------------------- the results
    section("\N{SPEAKER WITH THREE SOUND WAVES} Results", colour="slate")
    # interactive=False: a player, not a drop zone. In 2.21 the empty player said
    # "drop audio here", which is an invitation to do the wrong thing.
    audio_out = gr.Audio(label="Latest track", type="filepath", interactive=False,
                         elem_classes=["player"])
    score_note = gr.Markdown(C.SCORE_NOTE_IDLE, elem_classes=["scorenote"])
    with gr.Row(equal_height=True, elem_classes=["runfoot"]):
        reveal_btn = gr.Button(C.REVEAL_LABEL, scale=0, min_width=0,
                               elem_classes=["finderbtn"])
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
    C.wire_words_and_settings({**R, **WD, **S}, PROMPT, num_tracks)

    RESTORE = [variant_label, style, lyrics, instrumental, duration_s, fit_label,
               meter_label, tempo, rhythm, daring, style_strength, planning_label,
               track_seed, batch_name, vary_seed, refine_steps]

    def restore(f):
        blank = [gr.update()] * len(RESTORE)
        if not f:
            return (*blank, "")
        path = f if isinstance(f, str) else getattr(f, "name", None)
        side = C.sidecar_for(path, [OUTPUT_DIR]) if path else None
        if side is None:
            return (*blank, f"*No settings file found for `{Path(path).name}` - not beside it, and not in the output folders. Drop its `.txt` instead.*")
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
            num("duration_s"),
            # A track from before 2.22 has no "Score fit" line: it was cut.
            gr.update(value=C.FIT_LABEL_BY_MODE.get(info.get("fit", "cut"), C.DEFAULT_FIT)),
            gr.update(value=meter_value) if meter_value else gr.update(),
            num("tempo"), gr.update(value=0), num("daring"), num("style_strength", float),
            gr.update(value=info["planning"]) if info.get("planning") in C.SCORE_PLANNING
            else gr.update(),
            num("seed"),
            gr.update(value=info.get("batch_name", "")) if "batch_name" in info else gr.update(),
            gr.update(value=False),
            gr.update(value=int(info.get("steps", C.REFINE_DEFAULT))),
            C.restored_note(side, info),
        )

    restore_file.change(restore, inputs=restore_file, outputs=RESTORE + [restore_note],
                        show_progress="hidden")

    # See LIVE, at the top of this file, for why these two lists must agree.
    LIVE.bind({
        "variant_label": variant_label, "instrumental": instrumental,
        "duration_s": duration_s, "fit_label": fit_label, "meter_label": meter_label,
        "tempo": tempo, "rhythm": rhythm, "daring": daring, "style_strength": style_strength,
        "planning_label": planning_label, "score_file": score_file,
        "track_seed": track_seed, "vary_seed": vary_seed, "refine_steps": refine_steps,
        "save_mp3": save_mp3, "save_flac": save_flac, "save_midi": save_midi,
    })

    R["open_run_btn"].click(lambda: C.open_run_folder(OUTPUT_DIR), outputs=status_out)
    reveal_btn.click(C.reveal_track, inputs=audio_out, outputs=where_note)

    def refresh_estimate(n, dur, variant_label, strength, fit_label):
        return C.estimate_batch(n, dur, C.MODEL_VARIANTS.get(variant_label, "8bit"), strength,
                                C.fit_mode(fit_label))

    est_inputs = [num_tracks, duration_s, variant_label, style_strength, fit_label]
    for ctrl in est_inputs:
        ctrl.change(refresh_estimate, inputs=est_inputs, outputs=estimate, **C.QUIET)

    run = generate_btn.click(
        generate,
        inputs=[variant_label, style, lyrics, instrumental, duration_s, fit_label, meter_label,
                tempo, rhythm, daring, style_strength, planning_label, score_file,
                track_seed, vary_seed, refine_steps,
                num_tracks, batch_name, save_mp3, save_flac, save_midi],
        outputs=[audio_out, prompt_out, files_out, log_out, score_out, progress_bar,
                 score_note])
    stop_btn.click(lambda: C.stop_now(STOP, PROG, AWAKE), inputs=None,
                   outputs=[status_out, progress_bar], cancels=[run])

if __name__ == "__main__":
    note = C.sweep_gradio_cache()
    if note:
        print(note)
    demo.launch(server_port=PORT, css=CSS)
