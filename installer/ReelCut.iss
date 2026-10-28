; Reel Cut installer: per-user install, no admin rights, nothing downloaded.
#define AppName "Reel Cut"
#define AppVersion "1.0.2"
#define AppExe "ReelCut.exe"

[Setup]
AppId={{6C1E9A52-4B7D-4E0F-9C3B-52A1D7E3F0C1}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Petrus Viitanen
DefaultDirName={localappdata}\Programs\{#AppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=ReelCut-Setup-{#AppVersion}
SetupIconFile=..\assets\reelcut.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\ReelCut\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
