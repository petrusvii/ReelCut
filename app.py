"""Reel Cut: drop band rehearsal recordings, get cleaned-up single songs."""
import math
import os
import sys
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSettings, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
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
            min_song_s=self.get("min_song_s", 120.0, float),
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


# ---------------------------------------------------------------- worker

class Worker(QThread):
    status = Signal(str)
    song = Signal(str, float, float, object)     # path, start, end, envelope
    progress = Signal(float)
    finished_all = Signal(int)

    def __init__(self, files, out_dir, opt):
        super().__init__()
        self.files, self.out_dir, self.opt = files, out_dir, opt

    def run(self):
        count = 0
        for i, f in enumerate(self.files):
            self.progress.emit(i / len(self.files))

            def on_song(path, s, e):
                env, _ = core.load_envelope(path)
                self.song.emit(str(path), s, e, env)

            try:
                count += len(core.process_file(f, self.out_dir, self.opt, self.status.emit, on_song))
            except Exception as e:  # keep going with the next file
                self.status.emit(f"{Path(f).name}: failed ({e})")
        self.progress.emit(1.0)
        self.finished_all.emit(count)


# ---------------------------------------------------------------- deck

class Deck(QWidget):
    """Reel-to-reel transport: two reels, a VU meter, a counter and a label strip."""
    clicked = Signal()

    def __init__(self, metal):
        super().__init__()
        self.metal = metal
        self.setMinimumHeight(300)
        self.setCursor(Qt.PointingHandCursor)
        self.angle = 0.0
        self.speed = 0.0          # degrees per frame
        self.progress = 0.0       # 0 = tape on left reel, 1 = on right reel
        self.level = -60.0        # VU input in dB
        self.needle = -60.0
        self.counter = "--:--"
        self.label = "DROP RECORDINGS HERE"
        self.hover = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(33)

    def mousePressEvent(self, e):
        self.clicked.emit()

    def tick(self):
        self.angle = (self.angle + self.speed) % 360
        # VU ballistics: quick-ish rise, slower fall
        k = 0.35 if self.level > self.needle else 0.12
        self.needle += (self.level - self.needle) * k
        self.update()

    # -- drawing helpers
    def reel(self, p, c, R, pack):
        p.save()
        p.translate(c)
        # tape pack
        p.setPen(Qt.NoPen)
        g = QRadialGradient(0, 0, pack)
        g.setColorAt(0.0, QColor("#3a2a1f"))
        g.setColorAt(1.0, QColor("#57402e"))
        p.setBrush(g)
        p.drawEllipse(QPointF(0, 0), pack, pack)
        # aluminium flange with three windows
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
        p.setPen(QPen(QColor("#6d6d69"), 1.2))
        p.drawPath(flange)
        # hub
        p.setBrush(QColor("#2d2b28"))
        p.drawEllipse(QPointF(0, 0), R * 0.13, R * 0.13)
        p.setBrush(QColor("#d9d9d4"))
        for k in range(3):
            a = math.radians(k * 120)
            p.drawRect(QRectF(math.cos(a) * R * 0.1 - 2, math.sin(a) * R * 0.1 - 2, 4, 4))
        p.restore()

    def vu(self, p, r):
        p.save()
        p.setPen(QPen(QColor("#1b1814"), 3))
        p.setBrush(QColor("#151210"))
        p.drawRoundedRect(r.adjusted(-6, -6, 6, 6), 8, 8)
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0, QColor("#fff3cf"))
        g.setColorAt(1, QColor("#f0d48e"))
        p.setBrush(g)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(r, 4, 4)
        cx, cy = r.center().x(), r.bottom() + r.height() * 0.12
        rad = r.height() * 0.92
        scale = [-20, -10, -7, -5, -3, -2, -1, 0, 1, 2, 3]

        def ang(db):
            t = (max(-20, min(3, db)) + 20) / 23
            t = t ** 1.6                                     # VU-ish spacing, crowded at the bottom
            return math.radians(-138 + t * 96)

        p.setFont(QFont(LABEL, 7))
        for db in scale:
            a = ang(db)
            col = QColor(RED) if db >= 0 else QColor(INK)
            p.setPen(QPen(col, 1.6))
            p1 = QPointF(cx + math.cos(a) * rad * 0.78, cy + math.sin(a) * rad * 0.78)
            p2 = QPointF(cx + math.cos(a) * rad * 0.86, cy + math.sin(a) * rad * 0.86)
            p.drawLine(p1, p2)
            if db in (-20, -10, -5, 0, 3):
                t = QPointF(cx + math.cos(a) * rad * 0.97, cy + math.sin(a) * rad * 0.97)
                p.drawText(QRectF(t.x() - 12, t.y() - 7, 24, 12), Qt.AlignCenter, f"{db:+d}" if db else "0")
        p.setPen(QPen(QColor(RED), 3))
        p.drawArc(QRectF(cx - rad * 0.82, cy - rad * 0.82, rad * 1.64, rad * 1.64),
                  int(-math.degrees(ang(0)) * 16), int(-(math.degrees(ang(3)) - math.degrees(ang(0))) * 16))
        p.setPen(QColor(INK))
        p.setFont(QFont(LABEL, 11, QFont.Bold))
        p.drawText(QRectF(r.left(), r.bottom() - 24, r.width(), 18), Qt.AlignCenter, "VU")
        a = ang(self.needle + 18)                                # -18 dBFS reads 0 VU
        p.setPen(QPen(QColor("#1d1a17"), 2))
        p.setClipRect(r)
        p.drawLine(QPointF(cx, cy), QPointF(cx + math.cos(a) * rad * 0.88, cy + math.sin(a) * rad * 0.88))
        p.restore()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        # black faceplate inset in the aluminium
        face = QRectF(14, 10, w - 28, h - 20)
        p.setPen(QPen(QColor("#8d8d88"), 1))
        p.setBrush(QColor("#1f1c19"))
        p.drawRoundedRect(face, 10, 10)
        inner = face.adjusted(8, 8, -8, -8)
        p.drawTiledPixmap(inner, self.metal)
        p.setPen(QPen(QColor(0, 0, 0, 60), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(inner, 6, 6)

        R = min(inner.height() * 0.40, inner.width() * 0.17)
        cy = inner.top() + R + 14
        lc = QPointF(inner.left() + R + 22, cy)
        rc = QPointF(inner.right() - R - 22, cy)
        full, empty = R * 0.82, R * 0.36
        lp = full - (full - empty) * self.progress
        rp = empty + (full - empty) * self.progress
        # tape path past the head block
        head = QRectF(w / 2 - 34, inner.bottom() - 62, 68, 30)
        p.setPen(QPen(QColor("#4a3626"), 3))
        p.drawLine(QPointF(lc.x(), lc.y() + lp), QPointF(head.left() + 6, head.top() + 4))
        p.drawLine(QPointF(head.left() + 6, head.top() + 4), QPointF(head.right() - 6, head.top() + 4))
        p.drawLine(QPointF(head.right() - 6, head.top() + 4), QPointF(rc.x(), rc.y() + rp))
        g = QLinearGradient(head.topLeft(), head.bottomLeft())
        g.setColorAt(0, QColor("#55524c"))
        g.setColorAt(1, QColor("#25231f"))
        p.setPen(QPen(QColor("#111"), 1))
        p.setBrush(g)
        p.drawRoundedRect(head, 4, 4)
        self.reel(p, lc, R, lp)
        self.reel(p, rc, R, rp)

        # VU meter and counter in the middle
        vw = min(190, inner.width() - 4 * R - 110)
        vu = QRectF(w / 2 - vw / 2, inner.top() + 18, vw, vw * 0.58)
        self.vu(p, vu)
        cnt = QRectF(w / 2 - 54, vu.bottom() + 16, 108, 30)
        p.setPen(QPen(QColor("#0c0b0a"), 2))
        p.setBrush(QColor("#1a1612"))
        p.drawRoundedRect(cnt, 4, 4)
        p.setPen(QColor(AMBER))
        p.setFont(QFont("Consolas", 15, QFont.Bold))
        p.drawText(cnt, Qt.AlignCenter, self.counter)
        lamp = QPointF(cnt.right() + 16, cnt.center().y())
        on = self.speed != 0
        p.setPen(Qt.NoPen)
        gl = QRadialGradient(lamp, 9)
        gl.setColorAt(0, QColor("#ff7a5c" if on else "#5a2420"))
        gl.setColorAt(1, QColor("#7a1c12" if on else "#2a1210"))
        p.setBrush(gl)
        p.drawEllipse(lamp, 6, 6)

        # cream label strip (Dymo-style)
        strip = QRectF(inner.left() + 24, inner.bottom() - 26, inner.width() - 48, 20)
        p.setPen(QPen(QColor("#b9ab86"), 1))
        p.setBrush(QColor(CREAM if not self.hover else "#fff4d6"))
        p.drawRoundedRect(strip, 3, 3)
        p.setPen(QColor(INK))
        f = QFont(LABEL, 9, QFont.DemiBold)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 2)
        p.setFont(f)
        p.drawText(strip, Qt.AlignCenter, self.label.upper())


# ---------------------------------------------------------------- song list

START_AT = 0.10     # play from 10 % in, past the count-in


class SeekBar(QSlider):
    """Thin tape-position slider; a click jumps straight to that spot."""

    def __init__(self):
        super().__init__(Qt.Horizontal)
        self.setRange(0, 1000)
        self.setValue(int(START_AT * 1000))
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            v = QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), int(e.position().x()), self.width())
            self.setValue(v)
            self.sliderMoved.emit(v)
        super().mousePressEvent(e)


class SongRow(QFrame):
    play = Signal(object)
    renamed = Signal(object, str)
    seek = Signal(object, float)

    def __init__(self, num, path, start, end, env):
        super().__init__()
        self.path, self.env = Path(path), env
        self.setObjectName("songrow")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 6)
        n = QLabel(f"{num:02d}")
        n.setObjectName("num")
        self.btn = QPushButton("▶")
        self.btn.setObjectName("play")
        self.btn.setFixedSize(34, 34)
        self.btn.clicked.connect(lambda: self.play.emit(self))
        self.name = QLineEdit()
        self.name.setPlaceholderText(f"Song {num:02d}  (type a name)")
        self.name.editingFinished.connect(lambda: self.renamed.emit(self, self.name.text()))
        d = end - start
        t = QLabel(f"{int(d // 60)}:{int(d % 60):02d}")
        t.setObjectName("dur")
        self.bar = SeekBar()
        self.bar.setToolTip("Where to listen from. Click or drag.")
        self.bar.sliderMoved.connect(lambda v: self.seek.emit(self, v / 1000))
        mid = QVBoxLayout()
        mid.setSpacing(2)
        mid.addWidget(self.name)
        mid.addWidget(self.bar)
        lay.addWidget(n)
        lay.addWidget(self.btn)
        lay.addLayout(mid, 1)
        lay.addWidget(t)

    @property
    def start_frac(self):
        return self.bar.value() / 1000

    def set_playing(self, on):
        self.btn.setText("■" if on else "▶")

    def show_position(self, frac):
        if not self.bar.isSliderDown():
            self.bar.setValue(int(frac * 1000))


STYLE = f"""
QMainWindow, QDialog {{ background: #c9c9c4; }}
QLabel {{ color: {INK}; font-family: {LABEL}; }}
QWidget#sheet {{ background: {CREAM}; border: 1px solid #b9ab86; border-radius: 6px; }}
QLabel#sheettitle {{ font-size: 12px; letter-spacing: 3px; font-weight: 600; color: #6b5d43; }}
QFrame#songrow {{ border-bottom: 1px solid #d9cba6; }}
QLabel#num {{ font-family: Consolas; font-size: 15px; color: #8a7a58; min-width: 26px; }}
QLabel#dur {{ font-family: Consolas; font-size: 13px; color: #6b5d43; }}
QLineEdit {{ background: transparent; border: none; border-bottom: 1px dashed #b9ab86;
            font-family: {LABEL}; font-size: 15px; color: {INK}; padding: 4px; }}
QLineEdit:focus {{ border-bottom: 2px solid {RED}; }}
QPushButton#play {{ border-radius: 17px; font-size: 13px; color: #f4f4f0;
    background: qradialgradient(cx:0.4, cy:0.35, radius:0.8, stop:0 #5a5650, stop:1 #1e1c19);
    border: 2px solid #8d8d88; }}
QPushButton#play:hover {{ border-color: {AMBER}; }}
QPushButton#key {{ font-family: {LABEL}; font-size: 12px; font-weight: 600; letter-spacing: 2px;
    color: {INK}; padding: 10px 18px; border: 1px solid #7d776a; border-radius: 4px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #fbf4e2, stop:1 #ddd0ad); }}
QPushButton#key:hover {{ background: #fff8e8; }}
QPushButton#key:pressed {{ background: #cfc19b; padding-top: 12px; }}
QPushButton#key:disabled {{ color: #9b937f; }}
QSlider::groove:horizontal {{ height: 4px; background: #d9cba6; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: #6b4a30; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 6px; margin: -6px 0; background: {RED}; border-radius: 1px; }}
QScrollArea, QScrollArea > QWidget > QWidget, QWidget#listbox {{ border: none; background: transparent; }}
QDialog QLabel, QDialog QCheckBox {{ font-size: 13px; }}
"""


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP)
        self.setAcceptDrops(True)
        self.resize(820, 760)
        self.setMinimumSize(700, 620)
        self.st = Settings()
        self.worker = None
        self.rows = []
        self.current = None           # SongRow being played
        self.pending = {}             # row -> name waiting for playback to stop
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
        lay.setContentsMargins(34, 16, 34, 18)
        lay.setSpacing(12)

        top = QHBoxLayout()
        title = QLabel("REEL CUT")
        title.setStyleSheet(f"font-family:{LABEL}; font-size:24px; font-weight:700; letter-spacing:8px; color:{INK};")
        sub = QLabel("rehearsal splitter  ·  model RC-1")
        sub.setStyleSheet("font-size:12px; letter-spacing:2px; color:#55524c;")
        top.addWidget(title)
        top.addSpacing(14)
        top.addWidget(sub)
        top.addStretch(1)
        lay.addLayout(top)

        self.deck = Deck(self.metal)
        self.deck.clicked.connect(self.choose_files)
        lay.addWidget(self.deck, 4)

        sheet = QWidget()
        sheet.setObjectName("sheet")
        sl = QVBoxLayout(sheet)
        sl.setContentsMargins(14, 10, 14, 10)
        st = QLabel("TRACK SHEET")
        st.setObjectName("sheettitle")
        sl.addWidget(st)
        self.list_box = QWidget()
        self.list_lay = QVBoxLayout(self.list_box)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(0)
        self.empty = QLabel("Songs appear here. Press play to hear one, then type its name.")
        self.empty.setStyleSheet("color:#8a7a58; font-size:13px; padding:12px 4px;")
        self.list_lay.addWidget(self.empty)
        self.list_lay.addStretch(1)
        self.list_box.setObjectName("listbox")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.list_box)
        sl.addWidget(scroll)
        lay.addWidget(sheet, 3)

        keys = QHBoxLayout()
        self.k_load = self.key("LOAD", self.choose_files)
        self.k_open = self.key("OPEN FOLDER", self.open_out)
        self.k_set = self.key("SETTINGS", self.settings)
        keys.addWidget(self.k_load)
        keys.addStretch(1)
        keys.addWidget(self.k_open)
        keys.addWidget(self.k_set)
        lay.addLayout(keys)
        self.last_folder = None

    def key(self, text, fn):
        b = QPushButton(text)
        b.setObjectName("key")
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(fn)
        return b

    def paintEvent(self, e):
        p = QPainter(self)
        p.drawTiledPixmap(self.rect(), self.metal)
        # walnut side cheeks
        for x in (0, self.width() - 18):
            g = QLinearGradient(x, 0, x + 18, 0)
            g.setColorAt(0, QColor("#3b2414"))
            g.setColorAt(0.5, QColor("#6a4325"))
            g.setColorAt(1, QColor("#3b2414"))
            p.fillRect(QRectF(x, 0, 18, self.height()), g)

    # -- input
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.deck.hover = True

    def dragLeaveEvent(self, e):
        self.deck.hover = False

    def dropEvent(self, e):
        self.deck.hover = False
        self.start([u.toLocalFile() for u in e.mimeData().urls()])

    def choose_files(self):
        if self.busy():
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, "Choose recordings", "",
            "Audio (*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.mp4 *.mov *.wma *.opus)")
        if files:
            self.start(files)

    def settings(self):
        SettingsDialog(self.st, self).exec()

    def open_out(self):
        d = self.last_folder or self.st.out_dir
        Path(d).mkdir(parents=True, exist_ok=True)
        os.startfile(d)

    def busy(self):
        return bool(self.worker and self.worker.isRunning())

    # -- processing
    def start(self, paths):
        if self.busy():
            self.deck.label = "Still working, one batch at a time"
            return
        files = sorted(p for p in paths if Path(p).suffix.lower() in AUDIO)
        if not files:
            self.deck.label = "That doesn't look like an audio file"
            return
        self.stop_playback()
        self.clear_rows()
        self.deck.progress = 0
        self.deck.speed = 6
        self.deck.counter = "00 / --"
        self.k_load.setEnabled(False)
        self.k_set.setEnabled(False)
        self.worker = Worker(files, self.st.out_dir, self.st.options())
        self.worker.status.connect(self.on_status)
        self.worker.song.connect(self.add_row)
        self.worker.progress.connect(lambda v: setattr(self.deck, "progress", v))
        self.worker.finished_all.connect(self.on_done)
        self.worker.start()

    def on_status(self, text):
        self.deck.label = text

    def on_done(self, count):
        self.deck.speed = 0
        self.deck.progress = 1
        self.k_load.setEnabled(True)
        self.k_set.setEnabled(True)
        self.deck.label = (f"{count} song{'s' if count != 1 else ''} ready. Play, name, done."
                           if count else "No songs found. Try a shorter song length in Settings")
        self.deck.counter = f"{count:02d} SONGS"

    def clear_rows(self):
        for r in self.rows:
            r.setParent(None)
        self.rows = []
        self.pending = {}
        self.empty.show()

    def add_row(self, path, s, e, env):
        self.empty.hide()
        row = SongRow(len(self.rows) + 1, path, s, e, env)
        row.play.connect(self.toggle_play)
        row.renamed.connect(self.rename)
        row.seek.connect(self.seek)
        self.rows.append(row)
        self.list_lay.insertWidget(self.list_lay.count() - 1, row)
        self.last_folder = str(Path(path).parent)
        self.deck.counter = f"{len(self.rows):02d} / --"

    # -- naming
    def rename(self, row, title):
        if row is self.current:
            self.pending[row] = title        # the player holds the file open; rename when it stops
            return
        try:
            row.path = core.rename_song(row.path, title)
        except FileExistsError as e:
            QMessageBox.warning(self, APP, f"A song called '{e}' already exists in this folder.")
        except OSError as e:
            QMessageBox.warning(self, APP, f"Could not rename: {e}")

    def flush_pending(self):
        for row, title in list(self.pending.items()):
            if row is not self.current:
                del self.pending[row]
                self.rename(row, title)

    # -- playback
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
        self.deck.speed = 2.5

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
        if not self.busy():
            self.deck.speed = 0
        self.flush_pending()

    def on_position(self, ms):
        if not self.current:
            return
        s = ms / 1000
        self.deck.counter = f"{int(s // 60):02d}:{int(s % 60):02d}"
        env = self.current.env
        i = int(s * 2)
        if env is not None and 0 <= i < len(env):
            self.deck.level = float(env[i])
        dur = self.player.duration()
        if dur > 0 and self.seek_to is not None:
            self.player.setPosition(int(self.seek_to * dur))
            self.seek_to = None
            return
        if dur > 0:
            self.current.show_position(ms / dur)
            if not self.busy():
                self.deck.progress = ms / dur

    def on_media_status(self, status):
        if status == QMediaPlayer.EndOfMedia:
            if self.current:
                self.current.bar.setValue(int(START_AT * 1000))
            self.stop_playback()

    def closeEvent(self, e):
        self.stop_playback()
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
