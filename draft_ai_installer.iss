; draft.ai — Inno Setup Installer Script
; Requires Inno Setup 6+ from https://jrsoftware.org/isinfo.php
; Run build_installer.bat first to produce dist\Draft\ via PyInstaller.

#define AppName      "Draft"
#define AppVersion   "1.0.0"
#define AppPublisher "Draft"
#define AppExeName   "Draft.exe"
#define DistDir      "dist\Draft"

[Setup]
AppId={{B7C4D2E1-FFAA-4567-9876-ABCDEF123456}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://github.com/sysangel/AutoDraftApp
AppSupportURL=https://github.com/sysangel/AutoDraftApp
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
OutputDir=installer_output
OutputBaseFilename=Draft_Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
WizardSizePercent=120
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
SetupIconFile=static\brand\Draft-icon.ico
; Require Windows 10+
MinVersion=10.0
ShowLanguageDialog=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";  Description: "Create a &desktop shortcut";          GroupDescription: "Shortcuts:"; Flags: unchecked
Name: "startupentry"; Description: "Launch Draft automatically on login"; GroupDescription: "Startup:";   Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start Menu
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
; Desktop (optional)
Name: "{autodesktop}\{#AppName}";  Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon
; Uninstall entry
Name: "{autoprograms}\{#AppName}\Uninstall {#AppName}"; Filename: "{uninstallexe}"

[Registry]
; Auto-start on login (optional task)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\{#AppExeName}"""; \
  Flags: uninsdeletevalue; Tasks: startupentry

[Run]
; Offer to launch after install
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName} now"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up user data directory on uninstall only if user confirms
; (We leave %APPDATA%\draft.ai so data is preserved by default)
Type: dirifempty; Name: "{app}"

[Code]
// Check for Microsoft Edge WebView2 Runtime.
// WebView2 ships with Windows 11 and Windows 10 21H2+.
// For older Windows 10, we prompt the user to install it.
function IsWebView2Installed(): Boolean;
var
  ver: String;
begin
  // Check machine-wide install
  Result := RegQueryStringValue(HKLM,
    'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
    'pv', ver) and (ver <> '0.0.0.0') and (ver <> '');
  if not Result then
    // Check per-user install
    Result := RegQueryStringValue(HKCU,
      'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
      'pv', ver) and (ver <> '0.0.0.0') and (ver <> '');
end;

procedure InitializeWizard();
begin
  if not IsWebView2Installed() then
    MsgBox(
      'Draft requires Microsoft Edge WebView2, which does not appear to be installed on this PC.' + #13#10 + #13#10 +
      'Please download and install it from:' + #13#10 +
      'https://go.microsoft.com/fwlink/p/?LinkId=2124703' + #13#10 + #13#10 +
      'After installing WebView2, run this installer again.',
      mbInformation, MB_OK);
end;
