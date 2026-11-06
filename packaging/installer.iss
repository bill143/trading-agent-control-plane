; Inno Setup script for the Windows installer (build step 1.21).
;   iscc /DSourceDir=..\dist\TradingSystem packaging\installer.iss
; Per-user install (no administrator rights), desktop icon, Start menu entry, uninstaller.
; No auto-updater is installed: updates are applied by running a newer installer.

#define AppName "Trading System"
#define AppVersion "0.1.0"
#ifndef SourceDir
  #define SourceDir "..\dist\TradingSystem"
#endif

[Setup]
AppId={{6D3E9C2A-8B41-4F7E-9A55-1C0B7E3F2D19}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Bill Asmar
DefaultDirName={localappdata}\Programs\TradingSystem
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=..\dist\installer
OutputBaseFilename=TradingSystem-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\TradingSystem.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\TradingSystem.exe"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\TradingSystem.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\TradingSystem.exe"; Description: "Open {#AppName} now"; Flags: nowait postinstall skipifsilent

[Code]
// The app's window uses Microsoft Edge WebView2 (built into Windows 11 and current Windows 10).
function WebView2Installed(): Boolean;
var
  Version: String;
begin
  Result :=
    RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) or
    RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version);
  Result := Result and (Version <> '') and (Version <> '0.0.0.0');
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not WebView2Installed() then
    MsgBox('Microsoft Edge WebView2 Runtime was not found. Install it from ' +
           'https://developer.microsoft.com/microsoft-edge/webview2/ (Evergreen) and run this setup again ' +
           'if the app window does not open.', mbInformation, MB_OK);
end;
