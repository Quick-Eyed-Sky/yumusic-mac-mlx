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

    lyrics = args.lyrics if args.lyrics is not None else args.lyrics_file.read_text()
    abc = args.abc_file.read_text() if args.abc_file else None
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
    if seed_text:
        audio, info = generate_from_prefix(repo, pipe, args, lyrics, seed_text,
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
    log(f"[done] {args.out} {audio.shape[0] / SAMPLE_RATE:.1f}s "
        f"in {time.perf_counter() - start:.0f}s")
    return 0


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
