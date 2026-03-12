; draft.ai — Inno Setup installer script
; Requires Inno Setup 6+  (https://jrsoftware.org/isinfo.php)
; Run build_installer.bat first to produce dist\DraftAI\ via PyInstaller,
; then compile this script with Inno Setup Compiler (ISCC.exe).

#define MyAppName      "draft.ai"
#define MyAppVersion   "1.0.0"
#define MyAppPublisher "draft.ai"
#define MyAppExeName   "DraftAI.exe"
#define DistDir        "dist\DraftAI"

[Setup]
AppId={{A3B2C1D0-EEFF-4321-8765-ABCDEF012345}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=installer_output
OutputBaseFilename=DraftAI_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
UninstallDisplayName={#MyAppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
; Include everything PyInstaller produced in dist\DraftAI\
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}";  Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
