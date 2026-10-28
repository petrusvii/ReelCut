"""Rehearsal Cutter: drop band rehearsal recordings, get cleaned-up single songs."""
import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QSettings
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QMainWindow, QPlainTextEdit, QProgressBar, QPushButton,
    QVBoxLayout, QWidget,
)

import core

AUDIO = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".mp4", ".mov", ".wma", ".opus"}


class Worker(QThread):
    message = Signal(str)
    progress = Signal(int)
    done = Signal()

    def __init__(self, files, out_dir, opt):
        super().__init__()
        self.files, self.out_dir, self.opt = files, out_dir, opt

    def run(self):
        for i, f in enumerate(self.files):
            self.progress.emit(i)
            try:
                core.process_file(f, self.out_dir, self.opt, self.message.emit)
            except Exception as e:  # keep going with the next file
                self.message.emit(f"{Path(f).name}: FAILED - {e}")
        self.progress.emit(len(self.files))
        self.done.emit()


class Drop(QLabel):
    def __init__(self, on_click):
        super().__init__("Drop rehearsal recordings here\n(or click to choose files)")
        self.on_click = on_click
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(120)
        self.setStyleSheet("border: 2px dashed #888; border-radius: 10px; font-size: 15px; color: #666;")

    def mousePressEvent(self, e):
        self.on_click()


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Rehearsal Cutter")
        self.setAcceptDrops(True)
        self.resize(640, 660)
        self.settings = QSettings("Petrus", "RehearsalCutter")
        self.worker = None

        c = QWidget()
        self.setCentralWidget(c)
        lay = QVBoxLayout(c)
        lay.addWidget(Drop(self.choose_files))

        box = QGroupBox("Options")
        form = QFormLayout(box)
        self.cut = QCheckBox("Cut into separate songs")
        self.cut.setChecked(True)
        self.min_len = QDoubleSpinBox(); self.min_len.setRange(20, 600); self.min_len.setValue(120); self.min_len.setSuffix(" s")
        self.pad = QDoubleSpinBox(); self.pad.setRange(0, 30); self.pad.setValue(5); self.pad.setSuffix(" s")
        self.eq = QComboBox()
        self.eq.addItems(["Auto (match each song's tone)", "Fixed preset", "Off"])
        self.strength = QDoubleSpinBox(); self.strength.setRange(0.1, 1.0); self.strength.setSingleStep(0.1); self.strength.setValue(0.7)
        self.comp = QCheckBox("Gentle compression"); self.comp.setChecked(True)
        self.norm = QCheckBox("Normalize loudness (-16 LUFS)"); self.norm.setChecked(True)
        form.addRow(self.cut)
        form.addRow("Shortest song", self.min_len)
        form.addRow("Padding around each song", self.pad)
        form.addRow("EQ", self.eq)
        form.addRow("Auto EQ strength", self.strength)
        form.addRow(self.comp)
        form.addRow(self.norm)
        out_row = QHBoxLayout()
        self.out_label = QLabel(self.settings.value("out_dir", str(Path.home() / "Music" / "Rehearsals")))
        self.out_label.setWordWrap(True)
        b = QPushButton("Change...")
        b.clicked.connect(self.choose_out)
        out_row.addWidget(self.out_label, 1)
        out_row.addWidget(b)
        form.addRow("Save to", out_row)
        lay.addWidget(box)

        self.bar = QProgressBar()
        lay.addWidget(self.bar)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        lay.addWidget(self.log, 1)
        open_btn = QPushButton("Open output folder")
        open_btn.clicked.connect(self.open_out)
        lay.addWidget(open_btn)

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

    def choose_out(self):
        d = QFileDialog.getExistingDirectory(self, "Output folder", self.out_label.text())
        if d:
            self.out_label.setText(d)
            self.settings.setValue("out_dir", d)

    def open_out(self):
        Path(self.out_label.text()).mkdir(parents=True, exist_ok=True)
        os.startfile(self.out_label.text())

    def start(self, paths):
        if self.worker and self.worker.isRunning():
            self.log.appendPlainText("Busy, wait for the current job to finish.")
            return
        files = [p for p in paths if Path(p).suffix.lower() in AUDIO]
        if not files:
            self.log.appendPlainText("No audio files in the drop.")
            return
        opt = core.Options(cut=self.cut.isChecked(), min_song_s=self.min_len.value(), pad_s=self.pad.value(),
                           eq_mode=["auto", "fixed", "off"][self.eq.currentIndex()], eq_strength=self.strength.value(), compress=self.comp.isChecked(), normalize=self.norm.isChecked())
        self.bar.setRange(0, len(files))
        self.bar.setValue(0)
        self.worker = Worker(sorted(files), self.out_label.text(), opt)
        self.worker.message.connect(self.log.appendPlainText)
        self.worker.progress.connect(self.bar.setValue)
        self.worker.done.connect(lambda: self.log.appendPlainText("Done."))
        self.worker.start()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = Main()
    w.show()
    sys.exit(app.exec())
