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
    min_song_s: float = 60.0      # pieces shorter than this are skipped
    pad_s: float = 5.0            # audio kept before and after each song
    merge_gap_s: float = 8.0      # breaks shorter than this stay inside one song
    eq_mode: str = "auto"         # "off", "fixed" or "auto" (match each song's own tone)
    eq_strength: float = 0.7      # how much of the measured imbalance auto EQ corrects
    compress: bool = True
    normalize: bool = True
    fmt: str = "mp3"              # "mp3", "wav", "flac" or "same" (as the original)


# ffmpeg codec arguments per output extension; mp3 is VBR medium quality (~165 kbps)
CODECS = {
    ".mp3": ["-c:a", "libmp3lame", "-q:a", "4"],
    ".wav": ["-c:a", "pcm_s16le"],
    ".flac": ["-c:a", "flac"],
    ".m4a": ["-c:a", "aac", "-b:a", "192k"],
    ".aac": ["-c:a", "aac", "-b:a", "192k"],
    ".ogg": ["-c:a", "libvorbis", "-q:a", "5"],
    ".opus": ["-c:a", "libopus", "-b:a", "128k"],
}
SAME_AS = {".mp4": ".m4a", ".mov": ".m4a"}     # video containers: keep the audio as m4a


def out_ext(src, opt):
    if opt.fmt != "same":
        return "." + opt.fmt
    ext = Path(src).suffix.lower()
    ext = SAME_AS.get(ext, ext)
    return ext if ext in CODECS else ".mp3"


def safe_name(name):
    """Turn a typed song title into a valid Windows file name."""
    bad = '<>:"/\\|?*'
    name = "".join("_" if ch in bad or ord(ch) < 32 else ch for ch in name).strip(" .")
    return name[:120]


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
    args += CODECS[Path(dst).suffix.lower()] + [str(dst)]
    r = _run(args)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode(errors="replace")[-400:])


def session_dir(out_dir, src):
    """One folder per recording, never reusing an existing one."""
    base = Path(out_dir) / Path(src).stem
    d, n = base, 2
    while d.exists():
        d = base.with_name(f"{base.name} ({n})")
        n += 1
    d.mkdir(parents=True)
    return d


def music_activity(path, sr=11025, hop=512, n=1024):
    """Per-second rhythm strength and loudness.

    A band playing has a steady pulse: the onset strength (spectral flux) repeats every
    beat, so its autocorrelation over a 6 s window is high (0.5-0.8). Talk, tuning and
    noodling have no steady pulse (0.1-0.3), even when they are loud.
    Returns (beat[1 per s], db[1 per s], duration_s).
    """
    raw = _run(["-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]).stdout
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
    frames = (len(x) - n) // hop
    if frames < 100:
        return np.array([]), np.array([]), len(x) / sr
    win = np.hanning(n).astype(np.float32)
    flux = np.zeros(frames, np.float32)
    power = np.zeros(frames, np.float32)
    prev = None
    for c in range(0, frames, 4096):                 # chunked STFT keeps memory low
        idx = np.arange(n)[None, :] + hop * np.arange(c, min(frames, c + 4096))[:, None]
        mag = np.abs(np.fft.rfft(x[idx] * win, axis=1)).astype(np.float32)
        logm = np.log1p(100 * mag)
        d = np.diff(logm, axis=0, prepend=logm[:1] if prev is None else prev[None, :])
        flux[c:c + len(mag)] = np.maximum(0, d).sum(1)
        power[c:c + len(mag)] = (mag ** 2).mean(1)
        prev = logm[-1]
    fps = sr / hop
    W = int(6 * fps)
    lag_lo, lag_hi = int(0.3 * fps), int(1.5 * fps)  # 40-200 BPM
    secs = int(frames / fps)
    beat = np.zeros(secs, np.float32)
    db = np.zeros(secs, np.float32)
    for s in range(secs):
        a = int(s * fps)
        lo, hi = max(0, a - W // 2), min(frames, a + W // 2)
        seg = flux[lo:hi] - flux[lo:hi].mean()
        if len(seg) > lag_hi + 4:
            ac = np.correlate(seg, seg, "full")[len(seg) - 1:]
            beat[s] = ac[lag_lo:lag_hi].max() / (ac[0] + 1e-9)
        db[s] = 10 * np.log10(np.median(power[a:a + int(fps)]) + 1e-12)
    return beat, db, len(x) / sr


def detect_music(beat, db, duration, opt):
    """Songs = stretches with a steady pulse, allowing short breaks inside a song."""
    if len(beat) == 0:
        return []
    k = 5
    smooth = np.convolve(beat, np.ones(k) / k, mode="same")
    loud_ref = np.percentile(db, 90)
    playing = (smooth > 0.42) & (db > loud_ref - 25)
    # hysteresis: once playing, a weaker pulse (quiet verse, ending) still counts;
    # going back towards the start is stricter so talk before the count-in stays out
    hold_end = (smooth > 0.32) & (db > loud_ref - 30)
    hold_start = (smooth > 0.38) & (db > loud_ref - 30)
    for i in range(1, len(playing)):
        if playing[i - 1] and hold_end[i]:
            playing[i] = True
    for i in range(len(playing) - 2, -1, -1):
        if playing[i + 1] and hold_start[i]:
            playing[i] = True
    segs, start = [], None
    for i, v in enumerate(playing):
        if v and start is None:
            start = i
        elif not v and start is not None:
            segs.append([start, i])
            start = None
    if start is not None:
        segs.append([start, len(playing)])
    merged = []
    for s in segs:
        if merged:
            gap = s[0] - merged[-1][1]
            band_level = np.median(db[merged[-1][0]:merged[-1][1]])
            # a short break, or a longer one that stays as loud as the band
            # (breakdown, free section, feedback), is still the same song
            still_loud = gap <= 25 and np.median(db[merged[-1][1]:s[0]]) >= band_level - 3
            if gap <= opt.merge_gap_s or still_loud:
                merged[-1][1] = s[1]
                continue
        merged.append(s)
    return [(max(0.0, s - opt.pad_s), min(duration, e + opt.pad_s))
            for s, e in merged if e - s >= opt.min_song_s]


def find_parts(src, opt):
    if not opt.cut:
        _, duration = load_envelope(src)
        if duration == 0:
            raise RuntimeError("Could not read audio")
        return [(0.0, duration)]
    beat, db, duration = music_activity(src)
    if duration == 0:
        raise RuntimeError("Could not read audio")
    return detect_music(beat, db, duration, opt)


def process_file(src, out_dir, opt, log=print, on_song=None, on_parts=None):
    """Cut one recording into songs (or keep it whole) and write cleaned files.

    Songs are written to <out_dir>/<recording name>/Song 01.<ext> and so on.
    on_parts(n) is called once the songs are found; on_song(path, start, end) as each
    file is finished.
    """
    src = Path(src)
    log(f"{src.name}: listening for songs")
    parts = find_parts(src, opt)
    if on_parts:
        on_parts(len(parts))
    if not parts:
        log(f"{src.name}: no songs found (try a shorter minimum song length in Settings)")
        return []
    folder = session_dir(out_dir, src)
    ext = out_ext(src, opt)
    written = []
    for i, (s, e) in enumerate(parts, 1):
        dst = folder / f"Song {i:02d}{ext}"
        log(f"{src.name}: {dst.name} ({int(s // 60)}:{int(s % 60):02d} - {int(e // 60)}:{int(e % 60):02d})")
        gains = measure_eq(src, s, e, opt) if opt.eq_mode == "auto" else None
        export(src, dst, s, e, opt, gains)
        written.append(dst)
        if on_song:
            on_song(dst, s, e)
    return written


def rename_song(path, title):
    """Rename a finished song to '<NN> <title>.<ext>'; returns the new path."""
    path = Path(path)
    num = path.stem.split(" ")[1] if path.stem.startswith("Song ") else path.stem[:2]
    title = safe_name(title)
    new = path.with_name(f"{num} {title}{path.suffix}" if title else f"Song {num}{path.suffix}")
    if new == path:
        return path
    if new.exists():
        raise FileExistsError(new.name)
    path.rename(new)
    return new


def widen(db, duration, start, end, pad_s=2.0, reach_s=45.0, step_s=15.0):
    """Wider edges for a song whose quiet intro or ringing outro was cut off.

    db is the 0.5 s loudness envelope of the whole recording. Each edge moves out to
    the next real pause (1.5 s near the room's noise floor), at least 3 s further out and
    at most reach_s away; if there is no pause in reach it moves step_s. Calling it again
    widens again, so a user can keep pressing until the song is whole.
    """
    floor = np.percentile(db, 10)
    quiet = db < floor + 6
    run = 3                                            # 3 x 0.5 s = 1.5 s of quiet

    def pause_before(i):
        lo = max(0, i - int(reach_s * 2))
        for j in range(i - 6, lo + run - 1, -1):
            if quiet[j - run + 1:j + 1].all():
                return j + 1
        return None

    def pause_after(i):
        hi = min(len(db), i + int(reach_s * 2))
        for j in range(i + 6, hi - run + 1):
            if quiet[j:j + run].all():
                return j
        return None

    si, ei = int(start * 2), int(end * 2)
    ps, pe = pause_before(si), pause_after(ei)
    new_s = ps / 2 - pad_s if ps is not None else start - step_s
    new_e = pe / 2 + pad_s if pe is not None else end + step_s
    return max(0.0, new_s), min(duration, new_e)


def recut_song(src, dst, start, end, opt, db=None, duration=None):
    """Re-export dst from src with wider edges; returns the new (start, end)."""
    if db is None:
        db, duration = load_envelope(src)
    s, e = widen(db, duration, start, end)
    gains = measure_eq(src, s, e, opt) if opt.eq_mode == "auto" else None
    tmp = Path(dst).with_name(Path(dst).stem + ".recut" + Path(dst).suffix)
    export(src, tmp, s, e, opt, gains)
    tmp.replace(dst)
    return s, e
