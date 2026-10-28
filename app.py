"""Reel Cut: drop band rehearsal recordings, get cleaned-up single songs."""
import math
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QFile, QPointF, QRectF, QSettings, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap, QRadialGradient,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QScrollArea, QSlider, QStyle, QVBoxLayout, QWidget,
)

import core

APP = "Reel Cut"
AUDIO = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".mp4", ".mov", ".wma", ".opus"}
FORMATS = [("mp3", "MP3, medium quality (small files)"), ("wav", "WAV (full quality, large)"),
           ("flac", "FLAC (full quality, smaller)"), ("same", "Same as the original")]
EQ_MODES = [("auto", "Auto (match each song's tone)"), ("fixed", "Fixed phone preset"), ("off", "Off")]

# palette: brushed aluminium, walnut, cream labels, amber lamps
CREAM, INK, AMBER, RED = "#efe4c8", "#2a241e", "#ffb02e", "#b8352a"
LABEL = "Bahnschrift"


def resource(name):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return str(base / "assets" / name)


def brushed_metal(w=512, h=512, seed=7):
    """Horizontal brushed aluminium texture."""
    rng = np.random.default_rng(seed)
    rows = rng.normal(0, 1, (h, w + 64))
    k = np.ones(64) / 64
    rows = np.array([np.convolve(r, k, mode="valid")[:w] for r in rows])
    rows = rows / rows.std() * 6 + rng.normal(0, 2.2, (h, w))
    img = np.clip(rows + 188, 0, 255).astype(np.uint8)
    q = QImage(img.data, w, h, w, QImage.Format_Grayscale8).copy()
    return QPixmap.fromImage(q)


# ---------------------------------------------------------------- settings

class Settings:
    def __init__(self):
        self.s = QSettings("ReelCut", "ReelCut")

    def get(self, key, default, kind=str):
        v = self.s.value(key, default)
        if kind is bool:
            return v in (True, "true", "1", 1)
        return kind(v)

    def set(self, key, value):
        self.s.setValue(key, value)

    @property
    def out_dir(self):
        return self.get("out_dir", str(Path.home() / "Music" / "Reel Cut"))

    def options(self):
        return core.Options(
            cut=self.get("cut", True, bool),
            min_song_s=self.get("min_song_s", 60.0, float),
            pad_s=self.get("pad_s", 5.0, float),
            eq_mode=self.get("eq_mode", "auto"),
            eq_strength=self.get("eq_strength", 0.7, float),
            compress=self.get("compress", True, bool),
            normalize=self.get("normalize", True, bool),
            fmt=self.get("fmt", "mp3"),
        )


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.st = settings
        self.setWindowTitle(f"{APP} settings")
        self.setMinimumWidth(520)
        o = settings.options()
        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.setVerticalSpacing(10)

        row = QHBoxLayout()
        self.out = QLineEdit(settings.out_dir)
        b = QPushButton("Browse...")
        b.clicked.connect(self.browse)
        row.addWidget(self.out, 1)
        row.addWidget(b)
        form.addRow("Save songs to", row)

        self.fmt = QComboBox()
        for k, t in FORMATS:
            self.fmt.addItem(t, k)
        self.fmt.setCurrentIndex([k for k, _ in FORMATS].index(o.fmt))
        form.addRow("File format", self.fmt)

        self.cut = QCheckBox("Cut into separate songs")
        self.cut.setChecked(o.cut)
        form.addRow("", self.cut)
        self.min_len = QDoubleSpinBox()
        self.min_len.setRange(20, 900)
        self.min_len.setSuffix(" s")
        self.min_len.setValue(o.min_song_s)
        form.addRow("Shortest song", self.min_len)
        self.pad = QDoubleSpinBox()
        self.pad.setRange(0, 30)
        self.pad.setSuffix(" s")
        self.pad.setValue(o.pad_s)
        form.addRow("Extra time around songs", self.pad)

        self.eq = QComboBox()
        for k, t in EQ_MODES:
            self.eq.addItem(t, k)
        self.eq.setCurrentIndex([k for k, _ in EQ_MODES].index(o.eq_mode))
        form.addRow("Tone (EQ)", self.eq)
        self.strength = QDoubleSpinBox()
        self.strength.setRange(0.1, 1.0)
        self.strength.setSingleStep(0.1)
        self.strength.setValue(o.eq_strength)
        form.addRow("Auto EQ strength", self.strength)
        self.comp = QCheckBox("Even out loud and quiet parts (compression)")
        self.comp.setChecked(o.compress)
        form.addRow("", self.comp)
        self.norm = QCheckBox("Same loudness for every song (normalise)")
        self.norm.setChecked(o.normalize)
        form.addRow("", self.norm)
        lay.addLayout(form)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel | QDialogButtonBox.RestoreDefaults)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.RestoreDefaults).clicked.connect(self.defaults)
        lay.addWidget(bb)

    def browse(self):
        d = QFileDialog.getExistingDirectory(self, "Save songs to", self.out.text())
        if d:
            self.out.setText(d)

    def defaults(self):
        o = core.Options()
        self.fmt.setCurrentIndex(0)
        self.cut.setChecked(o.cut)
        self.min_len.setValue(o.min_song_s)
        self.pad.setValue(o.pad_s)
        self.eq.setCurrentIndex(0)
        self.strength.setValue(o.eq_strength)
        self.comp.setChecked(o.compress)
        self.norm.setChecked(o.normalize)

    def save(self):
        s = self.st
        s.set("out_dir", self.out.text())
        s.set("fmt", self.fmt.currentData())
        s.set("cut", self.cut.isChecked())
        s.set("min_song_s", self.min_len.value())
        s.set("pad_s", self.pad.value())
        s.set("eq_mode", self.eq.currentData())
        s.set("eq_strength", self.strength.value())
        s.set("compress", self.comp.isChecked())
        s.set("normalize", self.norm.isChecked())
        self.accept()


# ---------------------------------------------------------------- workers

class RecutWorker(QThread):
    """Re-exports one song with wider edges."""
    done = Signal(object, float, float, object, str)   # row, start, end, envelope, error

    def __init__(self, row, opt):
        super().__init__()
        self.row, self.opt = row, opt

    def run(self):
        r = self.row
        try:
            s, e = core.recut_song(r.src, r.path, r.start, r.end, self.opt)
            env, _ = core.load_envelope(r.path)
            self.done.emit(r, s, e, env, "")
        except Exception as ex:
            self.done.emit(r, r.start, r.end, r.env, str(ex))


class Worker(QThread):
    """Works through a queue of recordings; more can be added while it runs."""
    job = Signal(int, int, str)                    # recording number, total so far, file name
    parts = Signal(int)                            # songs found in the current recording
    song = Signal(str, float, float, object, str)  # path, start, end, envelope, source
    progress = Signal(float)
    failed = Signal(str)
    finished_all = Signal(int, int)                # songs written, recordings done

    def __init__(self, files, out_dir, opt):
        super().__init__()
        self.out_dir, self.opt = out_dir, opt
        self.queue, self.done = list(files), 0
        self.lock = threading.Lock()

    def add(self, files):
        """Queue more files; False if the worker already finished (start a new one)."""
        with self.lock:
            if self.queue is None:
                return False
            self.queue += [f for f in files if f not in self.queue]
            return True

    def waiting(self):
        with self.lock:
            return len(self.queue or [])

    def run(self):
        count = 0
        while True:
            with self.lock:
                if not self.queue:
                    self.queue = None            # closed: later drops start a new worker
                    break
                f = self.queue.pop(0)
                total = self.done + len(self.queue) + 1
            self.job.emit(self.done + 1, total, Path(f).name)
            self.progress.emit(self.done / total)
            state = {"n": 0, "k": 0}

            def on_parts(n):
                state["n"] = n
                self.parts.emit(n)

            def on_song(path, s, e, src=f):
                env, _ = core.load_envelope(path)
                self.song.emit(str(path), s, e, env, str(src))
                state["k"] += 1
                self.progress.emit((self.done + state["k"] / max(1, state["n"])) / total)

            try:
                count += len(core.process_file(f, self.out_dir, self.opt, lambda m: None, on_song, on_parts))
            except Exception as e:  # keep going with the next file
                self.failed.emit(f"{Path(f).name}: {e}")
            self.done += 1
        self.progress.emit(1.0)
        self.finished_all.emit(count, self.done)


# ---------------------------------------------------------------- deck

def fmt_time(s):
    s = max(0, int(s))
    return f"{s // 60}:{s % 60:02d}"


class Deck(QWidget):
    """Compact transport strip: reels (spin only while cutting), a status display, a VU meter."""

    def __init__(self, metal):
        super().__init__()
        self.metal = metal
        self.setFixedHeight(150)
        self.angle = 0.0
        self.working = False
        self.progress = 0.0       # target: 0 = tape on the left reel, 1 = all on the right
        self.shown = 0.0          # what is drawn; eases towards progress
        self.level = -60.0        # VU input in dB
        self.needle = -60.0
        self.title, self.detail, self.play_line = "READY", "Drop recordings to start", ""
        self.playing = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(33)

    def tick(self):
        if self.working:
            self.angle = (self.angle + 5) % 360
        self.shown += (self.progress - self.shown) * 0.08
        k = 0.35 if self.level > self.needle else 0.12      # VU ballistics
        self.needle += (self.level - self.needle) * k
        self.update()

    def reel(self, p, c, R, pack):
        p.save()
        p.translate(c)
        p.setPen(Qt.NoPen)
        g = QRadialGradient(0, 0, pack)
        g.setColorAt(0.0, QColor("#3a2a1f"))
        g.setColorAt(1.0, QColor("#57402e"))
        p.setBrush(g)
        p.drawEllipse(QPointF(0, 0), pack, pack)
        flange = QPainterPath()
        flange.addEllipse(QPointF(0, 0), R, R)
        p.rotate(self.angle)
        for k in range(3):
            w = QPainterPath()
            w.moveTo(0, 0)
            w.arcTo(QRectF(-R * 0.86, -R * 0.86, R * 1.72, R * 1.72), k * 120 + 18, 84)
            w.closeSubpath()
            hole = QPainterPath()
            hole.addEllipse(QPointF(0, 0), R * 0.30, R * 0.30)
            flange = flange.subtracted(w.subtracted(hole))
        g = QLinearGradient(-R, -R, R, R)
        g.setColorAt(0, QColor("#f2f2ee"))
        g.setColorAt(0.5, QColor("#a9aaa6"))
        g.setColorAt(1, QColor("#e3e3de"))
        p.setBrush(g)
        p.setPen(QPen(QColor("#6d6d69"), 1))
        p.drawPath(flange)
        p.setBrush(QColor("#2d2b28"))
        p.drawEllipse(QPointF(0, 0), R * 0.14, R * 0.14)
        p.restore()

    def vu(self, p, r):
        p.save()
        p.setPen(QPen(QColor("#1b1814"), 2))
        p.setBrush(QColor("#151210"))
        p.drawRoundedRect(r.adjusted(-5, -5, 5, 5), 6, 6)
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0, QColor("#fff3cf"))
        g.setColorAt(1, QColor("#f0d48e"))
        p.setBrush(g)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(r, 3, 3)
        cx, cy = r.center().x(), r.bottom() + r.height() * 0.12
        rad = r.height() * 0.92

        def ang(db):
            t = ((max(-20, min(3, db)) + 20) / 23) ** 1.6     # VU-ish spacing
            return math.radians(-138 + t * 96)

        p.setFont(QFont(LABEL, 6))
        for db in [-20, -10, -7, -5, -3, -2, -1, 0, 1, 2, 3]:
            a = ang(db)
            p.setPen(QPen(QColor(RED) if db >= 0 else QColor(INK), 1.3))
            p.drawLine(QPointF(cx + math.cos(a) * rad * 0.78, cy + math.sin(a) * rad * 0.78),
                       QPointF(cx + math.cos(a) * rad * 0.86, cy + math.sin(a) * rad * 0.86))
            if db in (-20, -10, -5, 0, 3):
                t = QPointF(cx + math.cos(a) * rad * 0.97, cy + math.sin(a) * rad * 0.97)
                p.drawText(QRectF(t.x() - 10, t.y() - 6, 20, 11), Qt.AlignCenter, f"{db:+d}" if db else "0")
        p.setPen(QColor(INK))
        p.setFont(QFont(LABEL, 8, QFont.Bold))
        p.drawText(QRectF(r.left(), r.bottom() - 17, r.width(), 14), Qt.AlignCenter, "VU")
        a = ang(self.needle + 18)                            # -18 dBFS reads 0 VU
        p.setPen(QPen(QColor("#1d1a17"), 1.6))
        p.setClipRect(r)
        p.drawLine(QPointF(cx, cy), QPointF(cx + math.cos(a) * rad * 0.88, cy + math.sin(a) * rad * 0.88))
        p.restore()

    def lamp(self, p, c, on, color):
        g = QRadialGradient(c, 7)
        g.setColorAt(0, QColor(color) if on else QColor("#3a332b"))
        g.setColorAt(1, QColor(color).darker(260) if on else QColor("#1d1915"))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawEllipse(c, 5, 5)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        face = QRectF(6, 4, w - 12, h - 8)
        p.setPen(QPen(QColor("#8d8d88"), 1))
        p.setBrush(QColor("#1f1c19"))
        p.drawRoundedRect(face, 9, 9)
        inner = face.adjusted(6, 6, -6, -6)
        p.drawTiledPixmap(inner, self.metal)

        # engraved model plate
        p.setPen(QColor(INK))
        f = QFont(LABEL, 10, QFont.Bold)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 4)
        p.setFont(f)
        p.drawText(QRectF(inner.left() + 14, inner.top() + 4, 200, 16), Qt.AlignLeft, "REEL CUT")
        p.setPen(QColor("#5f5b54"))
        p.setFont(QFont(LABEL, 7))
        p.drawText(QRectF(inner.left() + 110, inner.top() + 6, 200, 14), Qt.AlignLeft, "MODEL RC-1")

        # reels
        R = min(42.0, (inner.height() - 34) / 2)
        cy = inner.center().y() + 9
        lc = QPointF(inner.left() + 18 + R, cy)
        rc = QPointF(lc.x() + 2 * R + 16, cy)
        full, empty = R * 0.82, R * 0.36
        lp = full - (full - empty) * self.shown
        rp = empty + (full - empty) * self.shown
        p.setPen(QPen(QColor("#4a3626"), 2))
        p.drawLine(QPointF(lc.x(), cy + lp), QPointF(rc.x(), cy + rp))
        self.reel(p, lc, R, lp)
        self.reel(p, rc, R, rp)

        # VU meter
        vw, vh = 118.0, 68.0
        vu = QRectF(inner.right() - 16 - vw, cy - vh / 2, vw, vh)
        self.vu(p, vu)

        # status display
        disp = QRectF(rc.x() + R + 20, inner.top() + 22, vu.left() - 22 - (rc.x() + R + 20), inner.height() - 32)
        g = QLinearGradient(disp.topLeft(), disp.bottomLeft())
        g.setColorAt(0, QColor("#1d1915"))
        g.setColorAt(1, QColor("#2b241d"))
        p.setPen(QPen(QColor("#0c0b0a"), 2))
        p.setBrush(g)
        p.drawRoundedRect(disp, 5, 5)
        tx = disp.left() + 26
        tw = disp.width() - 36

        self.lamp(p, QPointF(disp.left() + 13, disp.top() + 18), self.working, AMBER)
        f = QFont(LABEL, 14, QFont.Bold)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 3)
        p.setFont(f)
        p.setPen(QColor(AMBER))
        p.drawText(QRectF(tx, disp.top() + 6, tw, 24), Qt.AlignLeft | Qt.AlignVCenter, self.title)
        p.setFont(QFont(LABEL, 10))
        p.setPen(QColor("#e8c27a"))
        fm = QFontMetrics(p.font())
        p.drawText(QRectF(tx, disp.top() + 31, tw, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   fm.elidedText(self.detail, Qt.ElideRight, int(tw)))

        # progress tape under the job lines
        bar = QRectF(tx, disp.top() + 53, tw, 3)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#3d342a"))
        p.drawRect(bar)
        if self.working:
            p.setBrush(QColor(AMBER))
            p.drawRect(QRectF(bar.left(), bar.top(), bar.width() * self.shown, bar.height()))

        self.lamp(p, QPointF(disp.left() + 13, disp.bottom() - 15), self.playing, "#7ddc5a")
        p.setFont(QFont("Consolas", 10))
        p.setPen(QColor("#cfe8b8") if self.playing else QColor("#8a7a62"))
        fm = QFontMetrics(p.font())
        p.drawText(QRectF(tx, disp.bottom() - 25, tw, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   fm.elidedText(self.play_line, Qt.ElideRight, int(tw)))


# ---------------------------------------------------------------- song list

START_AT = 0.10     # play from 10 % in, past the count-in


class SeekBar(QSlider):
    """Small waveform of the song that doubles as the play-position slider.

    Click or drag anywhere to move the position. The played part is drawn dark.
    """

    def __init__(self):
        super().__init__(Qt.Horizontal)
        self.setRange(0, 1000)
        self.setValue(int(START_AT * 1000))
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(26)
        self.levels = None

    def set_env(self, env):
        """env = loudness in dB per 0.5 s. Scaled between the song's own quiet and loud
        parts, so verses, choruses and breaks stand out even after compression."""
        if env is None or len(env) == 0:
            self.levels = None
        else:
            env = np.asarray(env)
            lo, hi = np.percentile(env, 5), np.percentile(env, 98)
            hi = max(hi, lo + 8)                       # don't blow up tiny differences
            self.levels = np.clip((env - lo) / (hi - lo), 0.06, 1.0) ** 1.5
        self.update()

    def _move(self, e):
        v = QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), int(e.position().x()), self.width())
        self.setValue(v)
        self.sliderMoved.emit(v)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.isEnabled():
            self.setSliderDown(True)
            self._move(e)

    def mouseMoveEvent(self, e):
        if self.isSliderDown():
            self._move(e)

    def mouseReleaseEvent(self, e):
        self.setSliderDown(False)

    def paintEvent(self, e):
        p = QPainter(self)
        w, h = self.width(), self.height()
        mid = h / 2
        x_pos = self.value() / 1000 * w
        played, rest = QColor("#6b4a30"), QColor("#cdbb91" if self.isEnabled() else "#e3d6b4")
        if self.levels is None:
            p.fillRect(QRectF(0, mid - 1, w, 2), rest)
        else:
            step = 3                                         # 2 px bar + 1 px gap
            n = max(1, w // step)
            lv = self.levels
            edges = np.linspace(0, len(lv), n + 1).astype(int)
            for i in range(n):
                a, b = edges[i], max(edges[i] + 1, edges[i + 1])
                amp = lv[a:b].max() * (mid - 1)
                x = i * step
                p.fillRect(QRectF(x, mid - amp, 2, amp * 2), played if x < x_pos else rest)
        p.fillRect(QRectF(x_pos - 1.5, 0, 3, h), QColor(RED))


class SongRow(QFrame):
    play = Signal(object)
    renamed = Signal(object, str)
    seek = Signal(object, float)
    recut = Signal(object)
    discard = Signal(object)

    def __init__(self, num, path, start, end, env, src):
        super().__init__()
        self.path, self.env, self.src = Path(path), env, src
        self.start, self.end = start, end
        self.num = num
        self.applied = ""                 # the name the file currently carries
        self.setObjectName("songrow")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 3, 8, 5)
        n = QLabel(f"{num:02d}")
        n.setObjectName("num")
        self.btn = QPushButton("▶")
        self.btn.setObjectName("play")
        self.btn.setFixedSize(32, 32)
        self.btn.setToolTip("Listen")
        self.btn.clicked.connect(lambda: self.play.emit(self))
        self.name = QLineEdit()
        self.name.setPlaceholderText(f"Song {num:02d}  (type a name)")
        self.name.editingFinished.connect(lambda: self.renamed.emit(self, self.name.text()))
        self.name.textEdited.connect(lambda _: self.saved.setText(""))
        self.saved = QLabel("")
        self.saved.setObjectName("saved")
        self.saved.setFixedWidth(16)
        self.dur = QLabel()
        self.dur.setObjectName("dur")
        self.bar = SeekBar()
        self.set_span(start, end, env)
        self.b_recut = QPushButton("⟷")
        self.b_recut.setObjectName("small")
        self.b_recut.setToolTip("Recut wider: brings back a cut-off intro or ending.\nPress again to widen more.")
        self.b_recut.clicked.connect(lambda: self.recut.emit(self))
        self.b_discard = QPushButton("✕")
        self.b_discard.setObjectName("small")
        self.b_discard.setToolTip("Discard this take (moves the file to the Recycle Bin)")
        self.b_discard.clicked.connect(lambda: self.discard.emit(self))
        for b in (self.b_recut, self.b_discard):
            b.setFixedSize(28, 28)
            b.setCursor(Qt.PointingHandCursor)
        self.bar.setToolTip("Where to listen from. Click or drag.")
        self.bar.sliderMoved.connect(lambda v: self.seek.emit(self, v / 1000))
        top = QHBoxLayout()
        top.setSpacing(4)
        top.addWidget(self.name, 1)
        top.addWidget(self.saved)
        mid = QVBoxLayout()
        mid.setSpacing(1)
        mid.addLayout(top)
        mid.addWidget(self.bar)
        lay.addWidget(n)
        lay.addWidget(self.btn)
        lay.addLayout(mid, 1)
        lay.addWidget(self.dur)
        lay.addWidget(self.b_recut)
        lay.addWidget(self.b_discard)

    @property
    def title(self):
        return self.name.text().strip() or f"Song {self.num:02d}"

    def mark_saved(self, ok=True):
        self.saved.setText("✓" if ok and self.applied else "")
        self.saved.setToolTip(f"Saved as {self.path.name}" if ok else "")

    def set_span(self, start, end, env):
        self.start, self.end, self.env = start, end, env
        self.bar.set_env(env)
        self.dur.setText(fmt_time(end - start))

    def set_working(self, on):
        for w in (self.btn, self.b_recut, self.b_discard, self.bar):
            w.setEnabled(not on)
        if on:
            self.dur.setText("···")

    @property
    def start_frac(self):
        return self.bar.value() / 1000

    def set_playing(self, on):
        self.btn.setText("■" if on else "▶")
        self.btn.setToolTip("Stop" if on else "Listen")
        self.setProperty("playing", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def show_position(self, frac):
        if not self.bar.isSliderDown():
            self.bar.setValue(int(frac * 1000))


class DropTarget(QLabel):
    clicked = Signal()

    def __init__(self):
        super().__init__("Drop rehearsal or gig recordings here\n\nor click to choose files")
        self.setObjectName("drop")
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e):
        self.clicked.emit()


STYLE = f"""
QMainWindow, QDialog {{ background: #c9c9c4; }}
QLabel {{ color: {INK}; font-family: {LABEL}; background: transparent; }}
QWidget#sheet {{ background: {CREAM}; border: 1px solid #b9ab86; border-radius: 6px; }}
QLabel#step {{ font-size: 12px; letter-spacing: 2px; color: #7d776a; padding: 4px 2px; }}
QLabel#step[active="true"] {{ color: {INK}; font-weight: 700; border-bottom: 3px solid {RED}; }}
QLabel#drop {{ font-size: 17px; color: #7a6a4a; border: 2px dashed #c2b086; border-radius: 10px;
               margin: 8px; background: #f6edd6; }}
QLabel#drop:hover {{ background: #fff6df; border-color: {RED}; color: {INK}; }}
QFrame#songrow {{ border-bottom: 1px solid #d9cba6; }}
QFrame#songrow[playing="true"] {{ background: #f8eccc; border-left: 3px solid #5fae3e; }}
QLabel#rechead {{ font-family: Consolas; font-size: 12px; color: #6b5d43; padding: 8px 4px 2px 4px;
                  border-bottom: 2px solid #b9ab86; }}
QLabel#num {{ font-family: Consolas; font-size: 14px; color: #8a7a58; min-width: 22px; }}
QLabel#dur {{ font-family: Consolas; font-size: 13px; color: #6b5d43; min-width: 34px; }}
QLabel#saved {{ color: #3f8a2a; font-size: 14px; font-weight: 700; }}
QLineEdit {{ background: transparent; border: none; border-bottom: 1px dashed #b9ab86;
            font-family: {LABEL}; font-size: 14px; color: {INK}; padding: 2px 4px; }}
QLineEdit:focus {{ border-bottom: 2px solid {RED}; }}
QPushButton#play {{ border-radius: 16px; font-size: 12px; color: #f4f4f0;
    background: qradialgradient(cx:0.4, cy:0.35, radius:0.8, stop:0 #5a5650, stop:1 #1e1c19);
    border: 2px solid #8d8d88; }}
QPushButton#play:hover {{ border-color: {AMBER}; }}
QPushButton#play:disabled {{ color: #8d8d88; }}
QPushButton#small {{ font-size: 13px; color: #6b5d43; background: transparent;
    border: 1px solid #c9b98f; border-radius: 4px; }}
QPushButton#small:hover {{ color: {RED}; border-color: {RED}; background: #fff6df; }}
QPushButton#small:disabled {{ color: #cdbf9a; border-color: #e0d3b0; }}
QPushButton#key {{ font-family: {LABEL}; font-size: 12px; font-weight: 600; letter-spacing: 2px;
    color: {INK}; padding: 9px 16px; border: 1px solid #7d776a; border-radius: 4px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #fbf4e2, stop:1 #ddd0ad); }}
QPushButton#key:hover {{ background: #fff8e8; }}
QPushButton#key:pressed {{ background: #cfc19b; }}
QPushButton#key:disabled {{ color: #a39b86; }}
QPushButton#primary {{ font-family: {LABEL}; font-size: 13px; font-weight: 700; letter-spacing: 3px;
    color: #fff4e0; padding: 9px 26px; border: 1px solid #6e1d15; border-radius: 4px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #cf4a3b, stop:1 #9b2a1f); }}
QPushButton#primary:hover {{ background: #d9564a; }}
QPushButton#primary:disabled {{ color: #e6c9c2; background: #c49a92; border-color: #a5817a; }}
QSlider::groove:horizontal {{ height: 4px; background: #d9cba6; }}
QScrollArea, QScrollArea > QWidget > QWidget, QWidget#listbox {{ border: none; background: transparent; }}
QDialog QLabel, QDialog QCheckBox {{ font-size: 13px; }}
"""


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP)
        self.setAcceptDrops(True)
        self.resize(780, 640)
        self.setMinimumSize(640, 500)
        self.st = Settings()
        self.worker = None
        self.job = None               # what the worker is doing now
        self.rows = []
        self.current = None           # SongRow being played
        self.pending = {}             # row -> name waiting for playback to stop
        self.recuts = set()
        self.note, self.note_until = "", 0.0
        self.finished = False         # after DONE, until the next drop
        self.metal = brushed_metal()

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.positionChanged.connect(self.on_position)
        self.player.mediaStatusChanged.connect(self.on_media_status)
        self.seek_to = None

        c = QWidget()
        self.setCentralWidget(c)
        lay = QVBoxLayout(c)
        lay.setContentsMargins(26, 10, 26, 14)
        lay.setSpacing(8)

        self.deck = Deck(self.metal)
        lay.addWidget(self.deck)

        steps = QHBoxLayout()
        steps.setSpacing(18)
        self.steps = []
        for i, t in enumerate(["1  DROP RECORDINGS", "2  LISTEN & NAME", "3  DONE"]):
            s = QLabel(t)
            s.setObjectName("step")
            self.steps.append(s)
            steps.addWidget(s)
            if i < 2:
                arrow = QLabel("→")
                arrow.setStyleSheet("color:#7d776a;")
                steps.addWidget(arrow)
        steps.addStretch(1)
        lay.addLayout(steps)

        sheet = QWidget()
        sheet.setObjectName("sheet")
        sl = QVBoxLayout(sheet)
        sl.setContentsMargins(10, 6, 10, 6)
        self.drop = DropTarget()
        self.drop.clicked.connect(self.choose_files)
        sl.addWidget(self.drop, 1)
        self.list_box = QWidget()
        self.list_box.setObjectName("listbox")
        self.list_lay = QVBoxLayout(self.list_box)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(0)
        self.list_lay.addStretch(1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.list_box)
        sl.addWidget(self.scroll, 1)
        lay.addWidget(sheet, 1)

        keys = QHBoxLayout()
        self.k_add = self.key("+ ADD", self.choose_files, "Add more recordings")
        self.k_set = self.key("SETTINGS", self.settings, "Save folder, file format, song detection, sound")
        self.k_open = self.key("OPEN FOLDER", self.open_out, "Open the folder where the songs are saved")
        self.k_done = self.key("DONE  ✓", self.done, "Save all names, open the folder and clear the list", "primary")
        keys.addWidget(self.k_add)
        keys.addWidget(self.k_set)
        keys.addStretch(1)
        keys.addWidget(self.k_open)
        keys.addWidget(self.k_done)
        lay.addLayout(keys)

        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self.refresh)
        self.ui_timer.start(200)
        self.refresh()

    def key(self, text, fn, tip, name="key"):
        b = QPushButton(text)
        b.setObjectName(name)
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(tip)
        b.clicked.connect(fn)
        return b

    def paintEvent(self, e):
        p = QPainter(self)
        p.drawTiledPixmap(self.rect(), self.metal)
        for x in (0, self.width() - 14):                 # walnut side cheeks
            g = QLinearGradient(x, 0, x + 14, 0)
            g.setColorAt(0, QColor("#3b2414"))
            g.setColorAt(0.5, QColor("#6a4325"))
            g.setColorAt(1, QColor("#3b2414"))
            p.fillRect(QRectF(x, 0, 14, self.height()), g)

    # -- status: one place decides what the deck says
    def say(self, text, seconds=6):
        self.note, self.note_until = text, time.monotonic() + seconds

    def busy(self):
        return bool(self.worker and self.worker.isRunning())

    def refresh(self):
        d = self.deck
        busy, recutting = self.busy(), bool(self.recuts)
        d.working = busy or recutting
        note = self.note if time.monotonic() < self.note_until else ""
        if busy and self.job:
            j = self.job
            total = j["i"] + self.worker.waiting()
            d.title = f"CUTTING  {j['i']}/{total}" if total > 1 else "CUTTING"
            what = ("finding songs" if j["parts"] is None
                    else f"song {min(j['k'] + 1, j['parts'])} of {j['parts']}" if j["parts"]
                    else "no songs found")
            d.detail = f"{what}  ·  {Path(j['name']).stem}"
        elif recutting:
            d.title = "RECUTTING"
            d.detail = note or "Making the song wider"
            d.progress = 0.5
        elif self.finished:
            d.title = "DONE"
            d.detail = note or "Drop the next recordings to start again"
        elif self.rows:
            d.title = "READY"
            d.detail = note or "Listen, type the names, then press DONE"
        else:
            d.title = "READY"
            d.detail = note or "Drop recordings to start"
        if busy and note and not self.job:
            d.detail = note
        if self.current:
            pos, dur = self.player.position() / 1000, max(0, self.player.duration()) / 1000
            d.play_line = f"▶  {self.current.title}   {fmt_time(pos)} / {fmt_time(dur)}"
        else:
            d.play_line = "Press ▶ on a song to listen" if self.rows else "Nothing to listen to yet"
        d.playing = bool(self.current)

        has_rows = bool(self.rows)
        self.drop.setVisible(not has_rows)
        self.scroll.setVisible(has_rows)
        step = 3 if self.finished and not has_rows and not busy else (2 if has_rows or busy else 1)
        for i, s in enumerate(self.steps, 1):
            if s.property("active") != (i == step):
                s.setProperty("active", i == step)
                s.style().unpolish(s)
                s.style().polish(s)
        self.k_done.setEnabled(has_rows and not busy and not recutting)
        self.k_done.setToolTip("Wait until cutting is finished" if busy else
                               "Save all names, open the folder and clear the list")
        self.k_set.setEnabled(not busy)
        self.k_add.setVisible(has_rows or busy)

    # -- input
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.start([u.toLocalFile() for u in e.mimeData().urls()])

    def choose_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Choose recordings", "",
            "Audio (*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.mp4 *.mov *.wma *.opus)")
        if files:
            self.start(files)

    def settings(self):
        SettingsDialog(self.st, self).exec()

    def folders(self):
        seen = []
        for r in self.rows:
            if r.path.parent not in seen:
                seen.append(r.path.parent)
        return seen

    def open_out(self):
        folders = self.folders()
        if len(folders) == 1:
            os.startfile(folders[0])
        else:                                        # several recordings: open the parent
            d = Path(self.st.out_dir)
            d.mkdir(parents=True, exist_ok=True)
            os.startfile(d)

    # -- processing
    def start(self, paths):
        """Process dropped files; while busy they join the queue. Earlier songs stay listed."""
        files = []
        for p in paths:
            p = Path(p)
            if p.is_dir():                       # a dropped folder: take the audio files in it
                files += sorted(str(f) for f in p.iterdir() if f.suffix.lower() in AUDIO)
            elif p.suffix.lower() in AUDIO:
                files.append(str(p))
        files = sorted(dict.fromkeys(files))
        if not files:
            self.say("That doesn't look like an audio file")
            return
        self.finished = False
        if self.worker and self.worker.add(files):
            self.say(f"Added {len(files)} recording{'s' if len(files) > 1 else ''} to the queue")
            return
        self.deck.progress = self.deck.shown = 0
        self.job = None
        self.worker = Worker(files, self.st.out_dir, self.st.options())
        self.worker.job.connect(self.on_job)
        self.worker.parts.connect(self.on_parts)
        self.worker.song.connect(self.add_row)
        self.worker.failed.connect(lambda m: self.say(f"Could not read {m}", 10))
        self.worker.progress.connect(lambda v: setattr(self.deck, "progress", v))
        self.worker.finished_all.connect(self.on_done)
        self.worker.start()
        self.refresh()

    def on_job(self, i, total, name):
        self.job = {"i": i, "name": name, "parts": None, "k": 0}
        self.add_header(name)

    def on_parts(self, n):
        if self.job:
            self.job["parts"] = n

    def on_done(self, count, recordings):
        self.job = None
        self.deck.progress = 1
        if count:
            self.say(f"{count} song{'s' if count != 1 else ''} cut. Listen, name them, press DONE", 15)
        else:
            self.say("No songs found. Try a shorter 'Shortest song' in Settings", 15)
        self.refresh()

    def add_header(self, name):
        h = QLabel(Path(name).stem)
        h.setObjectName("rechead")
        self.list_lay.insertWidget(self.list_lay.count() - 1, h)

    def add_row(self, path, s, e, env, src):
        if self.job:
            self.job["k"] += 1
        stem = Path(path).stem
        num = int(stem.split(" ")[1]) if stem.startswith("Song ") else len(self.rows) + 1
        row = SongRow(num, path, s, e, env, src)
        row.play.connect(self.toggle_play)
        row.renamed.connect(self.rename)
        row.seek.connect(self.seek)
        row.recut.connect(self.recut)
        row.discard.connect(self.discard)
        self.rows.append(row)
        self.list_lay.insertWidget(self.list_lay.count() - 1, row)
        self.refresh()

    def clear_list(self):
        while self.list_lay.count() > 1:         # keep the stretch
            self.list_lay.takeAt(0).widget().setParent(None)
        self.rows = []
        self.pending = {}
        self.deck.progress = 0

    # -- done
    def commit_names(self):
        """Apply every typed name, also one still being typed (no Enter pressed)."""
        self.stop_playback()
        for r in self.rows:
            if r.name.text().strip() != r.applied:
                self.rename(r, r.name.text())

    def done(self):
        if self.busy() or not self.rows:
            return
        self.commit_names()
        folders, n = self.folders(), len(self.rows)
        for d in folders[:3]:
            os.startfile(d)
        self.clear_list()
        self.finished = True
        where = folders[0].name if len(folders) == 1 else f"{len(folders)} folders"
        self.say(f"{n} song{'s' if n != 1 else ''} saved in {where}. Drop the next recordings", 30)
        self.refresh()

    # -- naming
    def rename(self, row, title):
        title = title.strip()
        if title == row.applied:
            return
        if row is self.current:
            self.pending[row] = title        # the player holds the file open; rename when it stops
            return
        try:
            row.path = core.rename_song(row.path, title)
            row.applied = title
            row.mark_saved()
        except FileExistsError as e:
            QMessageBox.warning(self, APP, f"A song called '{e}' already exists in this folder.")
        except OSError as e:
            QMessageBox.warning(self, APP, f"Could not rename: {e}")

    def flush_pending(self):
        for row, title in list(self.pending.items()):
            if row is not self.current:
                del self.pending[row]
                self.rename(row, title)

    # -- fixing takes
    def recut(self, row):
        if row is self.current:
            self.stop_playback()
        row.set_working(True)
        w = RecutWorker(row, self.st.options())
        w.done.connect(self.on_recut)
        self.recuts.add(w)                  # keep a reference until it finishes
        w.finished.connect(lambda: self.recuts.discard(w))
        w.start()
        self.say(f"Recutting {row.title} wider", 30)

    def on_recut(self, row, s, e, env, err):
        if row not in self.rows:            # discarded or cleared meanwhile
            return
        grew = (row.start - s) + (e - row.end)
        row.set_span(s, e, env)
        row.set_working(False)
        row.bar.setValue(int(START_AT * 1000))
        self.say(f"Recut failed: {err[:60]}" if err else
                 f"{row.title}: {grew:.0f} s added. Press ⟷ again for more")

    def discard(self, row):
        if row is self.current:
            self.stop_playback()
        self.pending.pop(row, None)
        if row.path.exists() and not QFile.moveToTrash(str(row.path)):
            QMessageBox.warning(self, APP, f"Could not move {row.path.name} to the Recycle Bin.")
            return
        self.rows.remove(row)
        row.setParent(None)
        self.say(f"{row.title} moved to the Recycle Bin")
        self.refresh()

    # -- playback (never moves the reels: they only show cutting)
    def toggle_play(self, row):
        was = self.current
        self.stop_playback()
        if was is row:
            return
        self.current = row
        row.set_playing(True)
        self.seek_to = row.start_frac      # applied on the first position update, once playing
        self.player.setSource(QUrl.fromLocalFile(str(row.path)))
        self.player.play()
        self.refresh()

    def seek(self, row, frac):
        if row is self.current and self.player.duration() > 0:
            self.player.setPosition(int(frac * self.player.duration()))

    def stop_playback(self):
        if self.current:
            self.current.set_playing(False)
        self.current = None                # before stop(), so the reset to 0 doesn't move the bar
        self.seek_to = None
        self.player.stop()
        self.player.setSource(QUrl())
        self.deck.level = -60
        self.flush_pending()

    def on_position(self, ms):
        if not self.current:
            return
        dur = self.player.duration()
        if dur > 0 and self.seek_to is not None:
            self.player.setPosition(int(self.seek_to * dur))
            self.seek_to = None
            return
        if dur > 0:
            self.current.show_position(ms / dur)
        env = self.current.env
        i = int(ms / 500)
        if env is not None and 0 <= i < len(env):
            self.deck.level = float(env[i])

    def on_media_status(self, status):
        if status == QMediaPlayer.EndOfMedia:
            if self.current:
                self.current.bar.setValue(int(START_AT * 1000))
            self.stop_playback()

    def closeEvent(self, e):
        self.commit_names()                # a name typed without Enter is still saved
        if self.busy():
            self.worker.wait(2000)
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP)
    app.setStyleSheet(STYLE)
    icon = resource("reelcut.ico")
    if Path(icon).exists():
        app.setWindowIcon(QIcon(icon))
    w = Main()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
