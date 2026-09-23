#!/usr/bin/env python3
"""
yue_runner.py -- the renderer behind all three YuMusic 2.0 apps.

A thin wrapper around the model repo's own generate.py. It does everything
`python generate.py ...` does, plus the one thing generate.py's command line
cannot do: it exposes the sampling settings of the SCORE PLANNING stage.

Why that matters
----------------------------------------------------------------------------
YuE2 writes an ABC score (melody + chord symbols) before it renders a single
sample of audio, and that planning stage ships locked at temperature 0.70 /
top_p 0.90 / top_k 30. That is a deliberately safe setting, and it is the
reason the model keeps handing back plain diatonic triads: it is playing it
safe at the exact moment the harmony is decided.

Those numbers live on `pipe.abc_sampling`, an ordinary Python attribute, so
we can raise them without modifying the model repo. That is what
--abc-temperature / --abc-top-p / --abc-top-k do, and what the apps call
"Harmonic daring".

Note that repetition penalty is deliberately NOT exposed: ABC is a text
notation that has to repeat bar lines, rests and note letters constantly, so
penalising repetition there corrupts the notation rather than the harmony.

Everything else -- model, tokenizer, sampling, VAE -- is the repo's own code,
imported rather than copied, so this stays in sync with it.

Supplying --abc-file skips the planning stage entirely (your score is used
as-is), so the daring settings have no effect in that case. That is expected:
mutation renders re-use a score that already exists.

--abc-prefix is the third thing, added in 2.7
----------------------------------------------------------------------------
Between "write the whole score yourself" and "here is the whole score" there is
a third option the repo's command line does not offer: write the FIRST LINES of
the score, and let the model continue from them.

That matters because the score is generated as text, token by token, from an
empty start. Hand it "M:7/8" as the beginning of that text and it composes in
seven -- which no amount of asking in the style prompt will do, because the
style prompt is conditioning and the metre line is the thing itself.

Only the generation call differs; everything downstream is the repo's own
pipeline, reached through the same objects.

--plan-only and --cfg-negative-abc-file are the fourth and fifth, added in 2.21
----------------------------------------------------------------------------
--plan-only writes the ABC score and stops. The planning stage is seconds; the
audio behind it is minutes. Separating them lets a caller look at a score,
decide it is a four-chord loop, and ask for another one before paying for the
render. Rendering that accepted score afterwards with --abc-file gives the same
audio it would have given inline, because generate_tokens() re-keys from the
seed at the start of every stage.

--cfg-negative-abc-file points --cfg-scale at the SCORE. The repo's guidance
contrasts (style + lyrics + score) against (bare instruction + the same score),
so the score cancels and raising cfg sharpens only the style prompt. Handing
this option the ORIGINAL score, while --abc-file holds the mutated one, leaves
exactly the mutation in the difference - and that is what gets amplified. See
generate_with_contrast() below.

--fit-score is the sixth, added in 2.22
----------------------------------------------------------------------------
The model always writes a WHOLE piece before it plays a note - on 181 test
originals, typically 2 to 3 minutes of score - and --max-semantic-tokens only
decides where the sound is cut off. The median track played 45% of its score.

  cut    as before: the score is whatever the model wrote, the audio stops at
         the duration. Byte-for-byte the old behaviour, same code path.
  whole  the audio is given as many frames as the score needs (up to the
         model's six minutes). The score is untouched, so this is the SAME
         take as "cut" with the same seed - only longer.
  trim   the score is cut at the section boundary nearest the duration (or at
         the nearest line of music if no section ends near it) BEFORE the audio
         is made, so the model plays a complete shorter piece. The score
         changed, so this is a different take from "cut", even from bar 1.

Every render also prints one "[score-data] {...}" line: bars, seconds, how
much was heard. The apps read it to show what the duration really did.
"""

import argparse
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, required=True,
                        help="the YuE2-3B-MLX repo directory (where generate.py lives)")
    parser.add_argument("--model", type=Path, required=True, help="model variant directory")
    parser.add_argument("--style", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--lyrics")
    group.add_argument("--lyrics-file", type=Path)
    parser.add_argument("--cot", choices=["off", "melody", "full"], default="full")
    parser.add_argument("--abc-file", type=Path)
    parser.add_argument("--abc-prefix-file", type=Path,
                        help="seed the score's opening lines (metre, tempo) and let the "
                             "model continue from them")
    parser.add_argument("--seed", type=int, default=831001)
    parser.add_argument("--cfg-scale", type=float)
    parser.add_argument("--steps", type=int)
    parser.add_argument("--max-semantic-tokens", type=int)
    parser.add_argument("--abc-temperature", type=float,
                        help="score planning temperature (repo default 0.70)")
    parser.add_argument("--abc-top-p", type=float, help="score planning top_p (repo default 0.90)")
    parser.add_argument("--abc-top-k", type=int, help="score planning top_k (repo default 30)")
    parser.add_argument("--plan-only", action="store_true",
                        help="write the ABC score and stop. Seconds instead of minutes, "
                             "so a bad score can be thrown away before it costs a render.")
    parser.add_argument("--cfg-negative-abc-file", type=Path,
                        help="point --cfg-scale at the SCORE instead of at the style "
                             "prompt, by contrasting against this score (normally the "
                             "un-mutated original). See generate_with_contrast().")
    parser.add_argument("--fit-score", choices=["cut", "trim", "whole"], default="cut",
                        help="what to do when the score is longer than the duration: "
                             "cut the audio (default, as before), trim the score to fit, "
                             "or render the whole score")
    parser.add_argument("--out", type=Path, default=Path("yue2.wav"))
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    if not (repo / "generate.py").is_file():
        print(f"[error] no generate.py in {repo}", file=sys.stderr, flush=True)
        return 2
    sys.path.insert(0, str(repo))

    import numpy as np
    import mlx.core as mx
    from generate import Yue2Pipeline, Sampling, write_wav, SAMPLE_RATE

    # score_length.py sits beside this file; plain Python, nothing heavy.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import score_length as SL

    lyrics = args.lyrics if args.lyrics is not None else args.lyrics_file.read_text()
    abc = args.abc_file.read_text() if args.abc_file else None
    neg_abc = (args.cfg_negative_abc_file.read_text()
               if args.cfg_negative_abc_file else None)
    log = lambda msg: print(msg, file=sys.stderr, flush=True)

    pipe = Yue2Pipeline(args.model, log=log)

    # --- the whole point of this wrapper -------------------------------------
    if abc is None and any(v is not None for v in
                            (args.abc_temperature, args.abc_top_p, args.abc_top_k)):
        current = pipe.abc_sampling.__dict__
        overrides = {}
        if args.abc_temperature is not None:
            overrides["temperature"] = args.abc_temperature
        if args.abc_top_p is not None:
            overrides["top_p"] = args.abc_top_p
        if args.abc_top_k is not None:
            overrides["top_k"] = max(1, int(args.abc_top_k))
        pipe.abc_sampling = Sampling(**{**current, **overrides})
        log(f"[plan] harmonic daring: temperature {pipe.abc_sampling.temperature:.2f}, "
            f"top_p {pipe.abc_sampling.top_p:.2f}, top_k {pipe.abc_sampling.top_k} "
            f"(repo default 0.70 / 0.90 / 30)")
    elif abc is not None:
        log("[plan] score supplied -- planning stage skipped, daring settings unused")

    semantic = None
    if args.max_semantic_tokens:
        semantic = Sampling(**{**pipe.semantic_sampling.__dict__,
                                "max_tokens": args.max_semantic_tokens,
                                "min_tokens": min(pipe.semantic_sampling.min_tokens,
                                                  args.max_semantic_tokens)})

    start = time.perf_counter()
    latents_path = args.out.with_suffix(".latents.npy")
    on_latents = lambda z: np.save(latents_path, np.array(z))

    seed_text = (args.abc_prefix_file.read_text()
                 if args.abc_prefix_file and abc is None and args.cot != "off" else None)

    # --- write the score and stop --------------------------------------------
    # The planning stage is seconds; the audio behind it is minutes. Splitting
    # them lets a caller look at the score, decide it is a four-chord loop, and
    # ask for another one -- instead of paying for twelve minutes of audio first.
    if args.plan_only:
        if args.cot == "off":
            print("[error] --plan-only needs score planning on (--cot melody or full)",
                  file=sys.stderr, flush=True)
            return 2
        target = args.out.with_suffix(".abc")
        if abc is not None:
            target.write_text(abc)
            log(f"[plan] score supplied -- copied to {target}, nothing to plan")
            return 0
        from generate import token_prefix, generate_tokens
        tok = pipe.tokenizer
        seed_ids = list(tok.encode(seed_text)) if seed_text else []
        if seed_ids:
            log(f"[plan] score seeded with {len(seed_ids)} tokens")
        head = token_prefix(tok, args.style, lyrics, args.cot)
        ids, truncated = generate_tokens(pipe.model, head + seed_ids, pipe.abc_sampling,
                                          args.seed, "abc", on_token=pipe._progress("abc"))
        if truncated:
            log("[plan] ABC hit max_tokens")
        planned = tok.decode(seed_ids + list(ids))
        target.write_text(planned)
        report_score(planned, 0, "plan", None, None, log, SL)
        log(f"[done] plan only -- {target} in {time.perf_counter() - start:.0f}s")
        return 0

    # --- fit the score to the duration, when asked --------------------------
    fit = args.fit_score
    if fit != "cut" and neg_abc is not None:
        log("[score] the harmony pass renders its score at the length it is given -- "
            "fit ignored")
        fit = "cut"
    if fit != "cut" and args.cot == "off":
        log("[score] score planning is off, so there is no score to fit -- the "
            "duration cuts the audio, as before")
        fit = "cut"
    if fit != "cut":
        audio, info, frames, cut = generate_fitted(pipe, args, lyrics, abc, seed_text, fit,
                                                   semantic, on_latents, log, SL, Sampling)
        write_wav(args.out, audio)
        args.out.with_suffix(".abc").write_text(info["abc"])
        report_score(info["abc"], audio.shape[0] / SAMPLE_RATE, fit, cut, frames, log, SL)
        log(f"[done] {args.out} {audio.shape[0] / SAMPLE_RATE:.1f}s "
            f"in {time.perf_counter() - start:.0f}s")
        return 0

    if seed_text:
        audio, info = generate_from_prefix(repo, pipe, args, lyrics, seed_text,
                                            semantic, on_latents, log)
    elif neg_abc is not None and abc is not None:
        audio, info = generate_with_contrast(pipe, args, lyrics, abc, neg_abc,
                                              semantic, on_latents, log)
    else:
        if args.abc_prefix_file and abc is None and args.cot == "off":
            log("[plan] score planning is off -- the metre seed has nothing to seed")
        audio, info = pipe(args.style, lyrics, cot=args.cot, seed=args.seed, abc=abc,
                           cfg_scale=args.cfg_scale, semantic_sampling=semantic,
                           steps=args.steps, on_latents=on_latents)
    write_wav(args.out, audio)
    if info["abc"] is not None:
        args.out.with_suffix(".abc").write_text(info["abc"])
        report_score(info["abc"], audio.shape[0] / SAMPLE_RATE, "cut", None,
                     args.max_semantic_tokens, log, SL)
    log(f"[done] {args.out} {audio.shape[0] / SAMPLE_RATE:.1f}s "
        f"in {time.perf_counter() - start:.0f}s")
    return 0


def report_score(abc_text, audio_seconds, fit, cut, frames, log, SL):
    """Two lines in the Log: one for a person, one for the app. Never fails a
    render - a score this cannot read is simply not reported."""
    try:
        info = SL.summary(abc_text, audio_seconds, fit, cut, frames)
        if not info["bars"]:
            return
        human = SL.describe(info).replace("**", "").replace("  \n", " ")
        log(f"[score] {human}")
        log(SL.data_line(info))
    except Exception as exc:                      # a report is not worth a render
        log(f"[score] could not measure the score ({exc})")


def generate_fitted(pipe, args, lyrics, abc, seed_text, fit, semantic, on_latents, log,
                    SL, Sampling):
    """Write the score (or take the one supplied), fit it, then render it.

    The planning half is the repo's own, called the way Yue2Pipeline.__call__
    calls it, so the score is the one "cut" would have written with this seed.
    In "whole" the model's own token ids are kept as they are, so the render
    is the same take as "cut", only longer. In "trim" the shortened text is
    encoded again - the score changed, so the take changes anyway."""
    from generate import token_prefix, generate_tokens

    tok = pipe.tokenizer
    if abc is not None:
        abc_text, abc_ids = abc, list(tok.encode(abc))
    else:
        seed_ids = list(tok.encode(seed_text)) if seed_text else []
        if seed_ids:
            log(f"[plan] score seeded with: {seed_text.strip().splitlines()[-1]!r} "
                f"({len(seed_ids)} tokens) -- the model continues from there")
        head = token_prefix(tok, args.style, lyrics, args.cot)
        rest, truncated = generate_tokens(pipe.model, head + seed_ids, pipe.abc_sampling,
                                           args.seed, "abc", on_token=pipe._progress("abc"))
        if truncated:
            log("[plan] ABC hit max_tokens")
        abc_ids = seed_ids + list(rest)
        abc_text = tok.decode(abc_ids)

    asked = (semantic or pipe.semantic_sampling).max_tokens
    target_s = asked * SL.SECONDS_PER_FRAME
    measured = SL.measure(abc_text)
    cut = None
    if fit == "whole":
        frames = SL.frames_for(measured["seconds"]) if measured["bars"] else asked
        log(f"[score] whole score: {measured['bars']} bars, about "
            f"{SL.clock(measured['seconds'])} -- rendering {SL.clock(frames * SL.SECONDS_PER_FRAME)}"
            + (" (the model's six-minute ceiling)" if frames >= SL.MODEL_MAX_FRAMES else ""))
    else:                                           # trim
        new_text, cut = SL.trim(abc_text, target_s)
        if cut["cut_at"]:
            abc_text, abc_ids = new_text, list(tok.encode(new_text))
            frames = SL.frames_for(cut["kept_seconds"])
            log(f"[score] trimmed: {cut['bars']} bars ({SL.clock(cut['seconds'])}) down to "
                f"{cut['kept_bars']} bars ({SL.clock(cut['kept_seconds'])}), ending "
                f"{'just before the ' + cut['cut_at'] if cut['cut_kind'] == 'section' else 'after ' + cut['cut_at']}"
                f" -- rendering up to {SL.clock(frames * SL.SECONDS_PER_FRAME)}")
        else:
            cut, frames = None, asked
            log(f"[score] {measured['bars']} bars, about {SL.clock(measured['seconds'])} "
                f"-- already fits the duration, nothing trimmed")

    log(f"[score-frames] {int(frames)}")      # for the app's progress bar
    base = semantic or pipe.semantic_sampling
    sampling = Sampling(**{**base.__dict__, "max_tokens": int(frames),
                           "min_tokens": min(pipe.semantic_sampling.min_tokens, int(frames))})
    audio, info = render_score_ids(pipe, args, lyrics, abc_ids, abc_text, sampling,
                                   on_latents, log)
    return audio, info, int(frames), cut


def render_score_ids(pipe, args, lyrics, abc_ids, abc_text, sampling, on_latents, log):
    """The second half of Yue2Pipeline.__call__, from a score already in hand.
    Same calls, same order, same guidance rule - only the score's token ids
    are given rather than planned."""
    from generate import (token_prefix, negative_prefix, generate_tokens, synthesize,
                          CODEC_OFFSET, SAMPLE_RATE)

    tok = pipe.tokenizer
    prefix = token_prefix(tok, args.style, lyrics, args.cot, abc_ids)
    guidance = 1.0 if args.cfg_scale is None else args.cfg_scale
    negative = negative_prefix(tok, args.cot, abc_ids) if guidance != 1 else None
    log(f"[semantic] prefix {len(prefix)} tokens, cfg {guidance}")
    ids, truncated = generate_tokens(pipe.model, prefix, sampling, args.seed, "semantic",
                                      negative, guidance, on_token=pipe._progress("semantic"))
    if truncated:
        log("[semantic] hit max_tokens")
    codec = [t - CODEC_OFFSET for t in ids]
    if not codec:
        raise RuntimeError("Semantic stage produced no codec tokens")

    steps = args.steps or pipe.ode_steps
    log(f"[nar] {len(codec)} frames ({len(codec) * 1920 / SAMPLE_RATE:.1f}s), "
        f"{steps} midpoint steps")
    latents = synthesize(pipe.model, prefix, codec, args.seed, steps,
                         on_progress=lambda i, n: i % 8 == 0 and log(f"[nar] step {i}/{n}"))
    on_latents(latents)
    log("[vae] decoding")
    return pipe.decode(latents), {"abc": abc_text, "codec": codec}


def generate_with_contrast(pipe, args, lyrics, abc, negative_abc, semantic,
                            on_latents, log):
    """Render `abc`, with the guidance pointed at the SCORE instead of the prompt.

    Read generate.py before changing this. The repo's classifier-free guidance
    contrasts

        positive = instruction + style + lyrics + THE SCORE
        negative = instruction                 + THE SCORE

    and steers along (positive - negative). The score appears on both sides, so
    it cancels exactly: raising --cfg-scale sharpens the style prompt and does
    NOTHING for the score. A mutated harmony is conditioning of precisely the
    same strength at cfg 1.0 as at cfg 3.0. That is measurable in the output and
    it is why the harmony pass produced seven unrelated takes rather than seven
    harmonisations of one song.

    Here the negative branch carries the SAME style and lyrics and a DIFFERENT
    score -- in practice the original, un-mutated one. What survives the
    subtraction is then exactly what the mutation changed, and --cfg-scale
    multiplies that. It is the only control in the whole chain that makes the
    model lean on a reharmonisation.

    Cost: two forward passes per token, so roughly twice the render time, the
    same as any other use of CFG.
    """
    from generate import (token_prefix, negative_prefix, generate_tokens, synthesize,
                          CONTEXT, CODEC_OFFSET, SAMPLE_RATE)

    tok = pipe.tokenizer
    abc_ids, neg_ids = tok.encode(abc), tok.encode(negative_abc)
    guidance = 1.0 if args.cfg_scale is None else float(args.cfg_scale)

    prefix = token_prefix(tok, args.style, lyrics, args.cot, abc_ids)
    negative = token_prefix(tok, args.style, lyrics, args.cot, neg_ids)

    budget = (semantic or pipe.semantic_sampling).max_tokens
    if guidance == 1:
        negative = None
        log("[semantic] score contrast asked for, but cfg is 1.0 -- guidance does "
            "nothing at 1.0, so this is an ordinary render")
    elif len(negative) + budget > CONTEXT:
        # The contrast score is too long to sit beside the generation budget.
        # Fall back to the repo's own negative rather than failing the render.
        negative = negative_prefix(tok, args.cot, abc_ids)
        log(f"[semantic] the contrast score needs {len(negative)} tokens, over the "
            f"{CONTEXT} context with a {budget}-token budget -- falling back to the "
            "ordinary prompt contrast, which does not sharpen the score")
    else:
        log(f"[semantic] score contrast: {len(abc_ids)} tokens against {len(neg_ids)}, "
            f"cfg {guidance} -- guidance amplifies what the mutation changed")

    ids, truncated = generate_tokens(pipe.model, prefix, semantic or pipe.semantic_sampling,
                                      args.seed, "semantic", negative, guidance,
                                      on_token=pipe._progress("semantic"))
    if truncated:
        log("[semantic] hit max_tokens")
    codec = [t - CODEC_OFFSET for t in ids]
    if not codec:
        raise RuntimeError("Semantic stage produced no codec tokens")

    steps = args.steps or pipe.ode_steps
    log(f"[nar] {len(codec)} frames ({len(codec) * 1920 / SAMPLE_RATE:.1f}s), "
        f"{steps} midpoint steps")
    latents = synthesize(pipe.model, prefix, codec, args.seed, steps,
                         on_progress=lambda i, n: i % 8 == 0 and log(f"[nar] step {i}/{n}"))
    on_latents(latents)
    log("[vae] decoding")
    return pipe.decode(latents), {"abc": abc, "codec": codec}


def generate_from_prefix(repo, pipe, args, lyrics, seed_text, semantic, on_latents, log):
    """The repo's own pipeline, with the score's opening lines written for it.

    This mirrors Yue2Pipeline.__call__ exactly; the single difference is that
    the ABC stage starts from `token_prefix(...) + tokens(seed_text)` instead of
    from `token_prefix(...)` alone, and the seeded tokens are then counted as
    part of the score. Everything else -- CFG, flow matching, VAE -- is called,
    not copied.
    """
    from generate import (token_prefix, negative_prefix, generate_tokens, synthesize,
                          CODEC_OFFSET, SAMPLE_RATE)

    tok = pipe.tokenizer
    seed_ids = tok.encode(seed_text)
    log(f"[plan] score seeded with: {seed_text.strip().splitlines()[-1]!r} "
        f"({len(seed_ids)} tokens) -- the model continues from there")

    head = token_prefix(tok, args.style, lyrics, args.cot)
    rest, truncated = generate_tokens(pipe.model, head + list(seed_ids), pipe.abc_sampling,
                                       args.seed, "abc", on_token=pipe._progress("abc"))
    if truncated:
        log("[plan] ABC hit max_tokens")
    abc_ids = list(seed_ids) + list(rest)
    abc_text = tok.decode(abc_ids)

    prefix = token_prefix(tok, args.style, lyrics, args.cot, abc_ids)
    guidance = 1.0 if args.cfg_scale is None else args.cfg_scale
    negative = negative_prefix(tok, args.cot, abc_ids) if guidance != 1 else None
    log(f"[semantic] prefix {len(prefix)} tokens, cfg {guidance}")
    ids, truncated = generate_tokens(pipe.model, prefix, semantic or pipe.semantic_sampling,
                                      args.seed, "semantic", negative, guidance,
                                      on_token=pipe._progress("semantic"))
    if truncated:
        log("[semantic] hit max_tokens")
    codec = [t - CODEC_OFFSET for t in ids]
    if not codec:
        raise RuntimeError("Semantic stage produced no codec tokens")

    steps = args.steps or pipe.ode_steps
    log(f"[nar] {len(codec)} frames ({len(codec) * 1920 / SAMPLE_RATE:.1f}s), {steps} midpoint steps")
    latents = synthesize(pipe.model, prefix, codec, args.seed, steps,
                         on_progress=lambda i, n: i % 8 == 0 and log(f"[nar] step {i}/{n}"))
    on_latents(latents)
    log("[vae] decoding")
    return pipe.decode(latents), {"abc": abc_text, "codec": codec}


if __name__ == "__main__":
    sys.exit(main())
