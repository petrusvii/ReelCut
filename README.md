# Reel Cut

Drop band rehearsal or gig recordings (phone audio) on the window. Reel Cut finds the songs, cuts them into separate files and balances the sound. Listen to recognise each song, type its name, press DONE.

- **Flow:** 1 Drop recordings (several at once, or more while it works) → 2 Listen & name → 3 DONE (saves all names, opens the folder, clears the list). The deck display shows the cutting job and what is playing on separate lines; the reels spin only while cutting.
- **Songs:** found by rhythm (a steady pulse means the band is playing; talk and tuning don't have one), so quiet passages and breakdowns stay inside a song. Saved as `<Save folder>\<recording name>\Song 01.mp3`; typing a name renames the file to `01 <name>.mp3`.
- **Per song:** small waveform that is also the seek bar (playback starts 10 % in, past the count-in), ⟷ recut wider (brings back a cut-off intro or ending), ✕ discard (to the Recycle Bin).
- **Sound:** Auto EQ per song (matches a balanced band tone, e.g. tames bass when the phone sat on the bass amp), gentle compression, loudness normalisation to -16 LUFS. Each can be switched off in Settings.
- **Formats:** MP3 medium quality (default, VBR q4), WAV, FLAC, or same as the original.
- **Settings:** save folder, format, shortest song, extra time around songs, EQ mode and strength, compression, normalisation.

## Install

Run `ReelCut-Setup-<version>.exe`. It installs for the current user (no admin), adds Start menu and optional desktop shortcuts, and includes an uninstaller. Everything (Python, Qt, ffmpeg) is inside, nothing is downloaded. The installer is not code-signed, so Windows SmartScreen asks once: More info, Run anyway.

## Develop

- Run from source: `python app.py` (needs `pip install -r requirements.txt`).
- Build exe + installer: `powershell -File build.ps1` (needs Inno Setup 6: `winget install JRSoftware.InnoSetup`). Output: `installer\Output\ReelCut-Setup-<version>.exe`.
- Version: `installer\ReelCut.iss` (`AppVersion`).
- `core.py` = audio (detection, EQ, export, rename), `app.py` = UI, `make_icon.py` = app icon.
