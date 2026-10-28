# Reel Cut

Drop band rehearsal or gig recordings (phone audio) on the tape deck. Reel Cut finds the songs, cuts them into separate files and balances the sound. Play a song to recognise it, type its name, done.

- **Songs:** found by loudness (adapts to quiet recordings). Saved as `<Save folder>\<recording name>\Song 01.mp3`; typing a name renames the file to `01 <name>.mp3`.
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
