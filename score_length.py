"""
score_length.py -- how long a YuE2 score really is, and how to make it fit.

Found on 23 September 2026, on 181 test tracks: the model ALWAYS writes a
whole piece before it plays a note - typically 2 to 3 minutes of score, 1:30 to
5:00 at the extremes - and the Target duration only decides where the sound is
cut off. The median track played 45% of its own score. A 30-second render is,
in practice, the intro.

With lyrics, the score follows the lyrics (8-15 sung lines gave about two
minutes, 48 or more gave five). With [instrumental], the model decides alone.

This module reads a score as bars and seconds, so the apps can say what was
heard, and it can cut a score at the end of the section nearest a target
length, so the model plays a complete shorter piece instead of the opening of
a longer one.

Pure Python on purpose: the renderer imports it, and the renderer runs in the
model's own process, where nothing heavy should be loaded twice.
"""

import json
import math
import re

SECONDS_PER_FRAME = 0.04            # the model counts 40 ms codec frames
MODEL_MAX_FRAMES = 9000             # its own ceiling: 6 minutes

FIT_MODES = ("cut", "trim", "whole")

_FIELD = re.compile(r"^([A-Za-z]):\s*(.*)$")
_SECTION = re.compile(r"^%(?!%)\s*([A-Za-z][A-Za-z0-9 _'-]*?)\s*$")
_VOICE = re.compile(r"^V:\s*([^\s\]]+)")
_QUOTED = re.compile(r'"[^"]*"')
_DECOR = re.compile(r"![^!]*!|\+[^+]*\+")
_CONTENT = re.compile(r"[A-Ga-gzxZ]")
_MULTIREST = re.compile(r"^Z(\d*)$")


def _meter_whole(value, fallback=1.0):
    """'4/4' -> 1.0 whole note per bar, '7/8' -> 0.875, 'C' -> 1.0, 'C|' -> 1.0."""
    v = (value or "").strip()
    if v in ("C", "C|"):
        return 1.0
    m = re.match(r"(\d+)\s*/\s*(\d+)", v)
    if m and int(m.group(2)):
        return int(m.group(1)) / int(m.group(2))
    return fallback


def _tempo(value, fallback=(0.25, 120)):
    """'1/4=93' -> (0.25, 93). A bare '93' is read as quarter notes."""
    v = (value or "").strip()
    m = re.search(r"(\d+)\s*/\s*(\d+)\s*=\s*(\d+)", v)
    if m and int(m.group(2)) and int(m.group(3)):
        return int(m.group(1)) / int(m.group(2)), int(m.group(3))
    m = re.fullmatch(r"(\d+)", v)
    if m and int(m.group(1)):
        return 0.25, int(m.group(1))
    return fallback


def _bars_in(line):
    """How many bars a line of music holds. `Z` is a whole bar of rest and
    `Z4` four of them; a chord symbol alone is not a bar."""
    count = 0
    for piece in line.split("|"):
        body = _DECOR.sub("", _QUOTED.sub("", piece)).strip()
        if not body or not _CONTENT.search(body):
            continue
        m = _MULTIREST.match(body)
        count += int(m.group(1) or 1) if m else 1
    return count


def clock(seconds):
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60}:{seconds % 60:02d}"


def measure(abc):
    """Read a score as time.

    Returns a dict:
      bars, seconds       the whole score, counted on its first voice
      bpm, meter          as written at the top
      bar_seconds         a list, one entry per bar (a metre change mid-score
                          changes how long a bar lasts)
      sections            [(name, first bar, seconds at its start)]
      cuts                places the score can be cut without breaking a line:
                          [(char offset, bars before, seconds before, kind, label)]
                          kind is "section" (a section starts here) or
                          "phrase" (a line of music starts here)
    """
    text = abc or ""
    lines = text.splitlines(keepends=True)

    head_meter, head_tempo, first_voice = 1.0, (0.25, 120), None
    meter_str, in_body = "4/4", False
    for line in lines:
        s = line.strip()
        f = _FIELD.match(s)
        if not f:
            continue
        key, val = f.group(1), f.group(2)
        if key == "M":
            head_meter, meter_str = _meter_whole(val, head_meter), val.strip() or meter_str
        elif key == "Q":
            head_tempo = _tempo(val, head_tempo)
        elif key == "V" and first_voice is None:
            first_voice = _VOICE.match(s).group(1) if _VOICE.match(s) else None
        elif key == "K":
            break

    meter, (beat, bpm) = head_meter, head_tempo
    bar_seconds, sections, cuts = [], [], []
    current_voice = None
    offset, elapsed = 0, 0.0
    last_was_section = False
    for line in lines:
        start, offset = offset, offset + len(line)
        s = line.strip()
        if not in_body:
            if s.startswith("K:"):
                in_body = True
            continue
        if not s:
            continue
        sec = _SECTION.match(s)
        if sec:
            name = sec.group(1).strip()
            sections.append((name, len(bar_seconds), elapsed))
            cuts.append((start, len(bar_seconds), elapsed, "section", name))
            last_was_section = True
            continue
        if s.startswith("%"):
            continue
        f = _FIELD.match(s)
        if f:
            key, val = f.group(1), f.group(2)
            if key == "V":
                m = _VOICE.match(s)
                current_voice = m.group(1) if m else None
                if first_voice is None:
                    first_voice = current_voice
                if current_voice == first_voice and not last_was_section:
                    cuts.append((start, len(bar_seconds), elapsed, "phrase", ""))
                last_was_section = False
            elif current_voice in (None, first_voice):
                if key == "M":
                    meter = _meter_whole(val, meter)
                elif key == "Q":
                    beat, bpm = _tempo(val, (beat, bpm))
            continue
        # a line of music
        if first_voice is not None and current_voice != first_voice:
            continue
        if first_voice is None and not last_was_section:
            cuts.append((start, len(bar_seconds), elapsed, "phrase", ""))
        last_was_section = False
        one = meter / beat * 60.0 / bpm
        for _ in range(_bars_in(s)):
            bar_seconds.append(one)
            elapsed += one

    return {
        "bars": len(bar_seconds),
        "seconds": elapsed,
        "bpm": head_tempo[1],
        "meter": meter_str,
        "bar_seconds": bar_seconds,
        "sections": sections,
        "cuts": cuts,
    }


def heard_bars(m, seconds):
    """How many bars fit in `seconds` of audio, fractions included."""
    t, n = 0.0, 0.0
    for b in m["bar_seconds"]:
        if t + b <= seconds:
            t += b
            n += 1
        else:
            n += max(0.0, (seconds - t) / b)
            break
    return n


def frames_for(seconds):
    """Codec frames to render `seconds` of score without clipping its end.

    A little over: the model does not follow the written tempo to the
    millisecond, and a render that runs short simply stops early - the model
    writes its own end-of-music mark - while one that runs out of frames loses
    the last notes."""
    return max(1, min(MODEL_MAX_FRAMES,
                      int(math.ceil((seconds * 1.05 + 4.0) / SECONDS_PER_FRAME))))


def trim(abc, target_seconds):
    """Cut the score to about `target_seconds`, at a section boundary if one is
    near, otherwise at the start of the line of music nearest the target.

    Returns (new_abc, report). The report says where it was cut, so the Log can
    say so in words. A score that already fits is returned untouched."""
    m = measure(abc)
    report = {"bars": m["bars"], "seconds": m["seconds"], "kept_bars": m["bars"],
              "kept_seconds": m["seconds"], "cut_at": None, "cut_kind": None}
    t = float(target_seconds or 0)
    if not m["bars"] or t <= 0 or m["seconds"] <= t * 1.1 + 2:
        return abc, report
    inside = [c for c in m["cuts"] if 0 < c[2] < m["seconds"]]
    near_sections = [c for c in inside if c[3] == "section" and 0.7 * t <= c[2] <= 1.35 * t]
    pool = near_sections or inside
    if not pool:
        return abc, report
    offset, bars, secs, kind, label = min(pool, key=lambda c: abs(c[2] - t))
    report.update(kept_bars=bars, kept_seconds=secs,
                  cut_at=label or f"bar {bars}", cut_kind=kind)
    return abc[:offset].rstrip() + "\n", report


def section_at(m, bar):
    """The name of the section a bar belongs to, or ''."""
    name = ""
    for sname, first, _ in m["sections"]:
        if first <= bar:
            name = sname
    return name


def summary(abc, audio_seconds, fit="cut", cut_report=None, frames=None):
    """Everything the page wants to say about one render, as a dict. It
    travels from the renderer to the app as one line of JSON in the Log."""
    m = measure(abc)
    heard = heard_bars(m, audio_seconds) if audio_seconds else 0.0
    unheard = [s for s, _, start in m["sections"] if audio_seconds and start >= audio_seconds]
    out = {
        "bars": m["bars"], "seconds": round(m["seconds"], 1), "bpm": m["bpm"],
        "meter": m["meter"], "audio_seconds": round(float(audio_seconds or 0), 1),
        "heard_bars": round(heard, 1), "fit": fit, "frames": frames,
        "sections": [s for s, _, _ in m["sections"]],
        "unheard_sections": unheard,
    }
    if cut_report and cut_report.get("cut_at"):
        out.update(original_bars=cut_report["bars"],
                   original_seconds=round(cut_report["seconds"], 1),
                   cut_at=cut_report["cut_at"], cut_kind=cut_report["cut_kind"])
    return out


DATA_TAG = "[score-data]"


def data_line(info):
    return f"{DATA_TAG} {json.dumps(info, ensure_ascii=True)}"


def read_data_line(line):
    """The dict back out of a Log line, or None."""
    if not line.startswith(DATA_TAG):
        return None
    try:
        return json.loads(line[len(DATA_TAG):].strip())
    except ValueError:
        return None


def cut_words(info):
    """'it now ends just before the chorus' / 'it now ends after bar 16'."""
    if info.get("cut_kind") == "section":
        return f"it now ends just before the **{info['cut_at']}**"
    return f"it now ends after {info.get('cut_at', 'a phrase')} (no section ended near the duration)"


def describe(info):
    """One or two plain sentences for the page, from summary()'s dict."""
    if not info or not info.get("bars"):
        return ""
    tempo = f"at \u2669={info['bpm']}" + (f" in {info['meter']}" if info.get("meter")
                                           and info["meter"] != "4/4" else "")
    parts = []
    if info.get("cut_at"):
        parts.append(f"**Score:** the model wrote {info['original_bars']} bars "
                     f"({clock(info['original_seconds'])}), shortened to {info['bars']} bars "
                     f"({clock(info['seconds'])}) {tempo} - {cut_words(info)}.")
    else:
        parts.append(f"**Score:** {info['bars']} bars, about {clock(info['seconds'])} {tempo}.")
    audio = info.get("audio_seconds") or 0
    if audio:
        pct = min(100, round(100 * info["heard_bars"] / info["bars"])) if info["bars"] else 0
        if pct >= 97:
            parts.append(f"**Heard:** all of it ({clock(audio)} of audio).")
        else:
            missing = info.get("unheard_sections") or []
            tail = (f" Never reached: {', '.join(missing)}." if missing else "")
            parts.append(f"**Heard:** {clock(audio)} = bars 1 to {int(info['heard_bars'])}, "
                         f"**{pct}%** of the score.{tail}")
    return "  \n".join(parts)
