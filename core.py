"""Audio logic for Rehearsal Cutter: song detection, cleanup chain, export."""
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
NOWINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


@dataclass
class Options:
    cut: bool = True
    min_song_s: float = 120.0     # pieces shorter than this are skipped
    pad_s: float = 5.0            # audio kept before and after each song
    merge_gap_s: float = 10.0     # pauses shorter than this stay inside one song
    eq_mode: str = "auto"         # "off", "fixed" or "auto" (match each song's own tone)
    eq_strength: float = 0.7      # how much of the measured imbalance auto EQ corrects
    compress: bool = True
    normalize: bool = True
    bitrate: str = "192k"


def _run(args):
    return subprocess.run([FFMPEG, "-v", "error", *args], capture_output=True, creationflags=NOWINDOW)


def load_envelope(path):
    """Loudness in dB per 0.5 s, plus total duration in seconds."""
    raw = _run(["-i", str(path), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"]).stdout
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    w = 4000
    n = len(x) // w
    if n == 0:
        return np.array([]), 0.0
    rms = np.sqrt((x[: n * w].reshape(n, w) ** 2).mean(1))
    return 20 * np.log10(rms + 1e-6), len(x) / 8000


def detect_songs(db, duration, opt):
    """Return [(start_s, end_s)] of loud stretches that look like songs."""
    if len(db) == 0:
        return []
    sm = np.convolve(db, np.ones(10) / 10, mode="same")        # 5 s smoothing
    thr = np.percentile(db, 50) - 1.5                          # adapts to quiet phone recordings
    loud = sm > thr
    segs, start = [], None
    for i, v in enumerate(loud):
        if v and start is None:
            start = i
        elif not v and start is not None:
            segs.append([start, i])
            start = None
    if start is not None:
        segs.append([start, len(loud)])
    gap = opt.merge_gap_s * 2
    merged = []
    for s in segs:
        if merged and s[0] - merged[-1][1] < gap:
            merged[-1][1] = s[1]
        else:
            merged.append(s)
    out = []
    for s, e in merged:
        if (e - s) / 2 >= opt.min_song_s:
            out.append((max(0.0, s / 2 - opt.pad_s), min(duration, e / 2 + opt.pad_s)))
    return out


BANDS = [63, 125, 250, 500, 1000, 2000, 4000, 8000]
# Typical long-term average level of a balanced band mix per octave band (dB, relative).
TARGET = [-4, 0, 0, -3, -6, -9, -12, -15]


def measure_eq(src, start, end, opt):
    """Per-band gains (dB) that move this song's average tone toward TARGET."""
    sr = 24000
    raw = _run(["-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", str(src), "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]).stdout
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    n = 4096
    frames = len(x) // n
    if frames < 10:
        return [0.0] * len(BANDS)
    fr = x[: frames * n].reshape(frames, n) * np.hanning(n)
    power = np.abs(np.fft.rfft(fr, axis=1)) ** 2
    loud = power.sum(1) >= np.median(power.sum(1))           # ignore pauses and talk
    spec = power[loud].mean(0)
    freqs = np.fft.rfftfreq(n, 1 / sr)
    levels = []
    for c in BANDS:
        m = (freqs >= c / 2 ** 0.5) & (freqs < c * 2 ** 0.5)
        levels.append(10 * np.log10(spec[m].sum() + 1e-12))
    resid = np.array(TARGET) - np.array(levels)
    resid -= np.median(resid)                                 # overall level is normalised later
    # cut up to 6 dB anywhere; boost up to 6 dB in the bass (thin recordings), 3 dB above
    return [float(np.clip(r * opt.eq_strength, -6, 6 if c <= 250 else 3)) for c, r in zip(BANDS, resid)]


def filter_chain(opt, gains=None):
    f = []
    if opt.eq_mode == "auto" and gains is not None:
        f.append("highpass=f=60")
        f += [f"equalizer=f={c}:t=o:w=1:g={g:.1f}" for c, g in zip(BANDS, gains) if abs(g) >= 0.3]
    if opt.eq_mode == "fixed":
        f += [
            "highpass=f=80",                                   # rumble and handling noise
            "equalizer=f=250:t=q:w=1:g=-2.5",                  # boxy low mids
            "equalizer=f=3500:t=q:w=1.2:g=-2",                 # harsh phone-mic highs
            "equalizer=f=9000:t=h:w=4000:g=1.5",               # a bit of air
        ]
    if opt.compress:
        f.append("acompressor=threshold=-20dB:ratio=2.5:attack=20:release=250:makeup=2")
    if opt.normalize:
        f.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    return ",".join(f)


def export(src, dst, start, end, opt, gains=None):
    args = ["-y", "-ss", f"{start:.2f}", "-to", f"{end:.2f}", "-i", str(src)]
    chain = filter_chain(opt, gains)
    if chain:
        args += ["-af", chain]
    args += ["-c:a", "libmp3lame", "-b:a", opt.bitrate, str(dst)]
    r = _run(args)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode(errors="replace")[-400:])


def process_file(src, out_dir, opt, log=print):
    """Cut one recording into songs (or keep it whole) and write cleaned mp3s."""
    src, out_dir = Path(src), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"{src.name}: analysing")
    db, duration = load_envelope(src)
    if duration == 0:
        raise RuntimeError("Could not read audio")
    parts = detect_songs(db, duration, opt) if opt.cut else [(0.0, duration)]
    if not parts:
        log(f"{src.name}: no songs found (try a shorter minimum length)")
        return []
    written = []
    for i, (s, e) in enumerate(parts, 1):
        name = f"{src.stem}-song-{i:02d}.mp3" if opt.cut else f"{src.stem}-clean.mp3"
        dst = out_dir / name
        log(f"{src.name}: writing {name} ({int(s // 60)}:{int(s % 60):02d} - {int(e // 60)}:{int(e % 60):02d})")
        gains = measure_eq(src, s, e, opt) if opt.eq_mode == "auto" else None
        if gains is not None:
            log("  auto EQ: " + ", ".join(f"{c} Hz {g:+.1f}" for c, g in zip(BANDS, gains)))
        export(src, dst, s, e, opt, gains)
        written.append(dst)
    return written
