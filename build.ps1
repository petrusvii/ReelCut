# Builds dist\ReelCut (PyInstaller) and installer\Output\ReelCut-Setup-<version>.exe (Inno Setup).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
Get-Process ReelCut -ErrorAction SilentlyContinue | Stop-Process -Force

python make_icon.py
python -m PyInstaller --noconfirm --windowed --name ReelCut `
    --icon assets\reelcut.ico --add-data "assets;assets" `
    --collect-all imageio_ffmpeg app.py
if ($LASTEXITCODE) { throw "PyInstaller failed" }

$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
          "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }
& $iscc /Q installer\ReelCut.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }
Get-ChildItem installer\Output\*.exe | ForEach-Object { "{0}  {1:N0} MB" -f $_.FullName, ($_.Length / 1MB) }
