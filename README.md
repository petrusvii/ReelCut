# Rehearsal Cutter

Drop band rehearsal recordings (phone audio) on the window. The app finds the songs, cuts them into separate mp3 files and cleans up the sound.

- Song detection: loudness-based, adapts to quiet recordings. Options: shortest song, padding.
- Cleanup (each step can be turned off): high-pass 80 Hz, EQ (-2.5 dB at 250 Hz, -2 dB at 3.5 kHz, +1.5 dB air), gentle compression, loudness normalisation to -16 LUFS.
- Output: `<name>-song-01.mp3`, 192 kbps, in the chosen folder.

Run from source: `python app.py`. Build exe: `python -m PyInstaller --noconfirm --windowed --name RehearsalCutter --collect-all imageio_ffmpeg app.py` (result in `dist/RehearsalCutter/`). ffmpeg is bundled through `imageio-ffmpeg`.
