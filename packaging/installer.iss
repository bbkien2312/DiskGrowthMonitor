#ifndef AppVersion
  #define AppVersion "1.1.0"
#endif
#define SourceRoot AddBackslash(SourcePath) + ".."
#ifndef InstallerOutputDir
  #define InstallerOutputDir SourceRoot + "\dist"
#endif

[Setup]
AppId={{8F156E32-B0A2-4C97-AE5D-7808676FAF11}
AppName=Disk Growth Monitor
AppVersion={#AppVersion}
AppVerName=Disk Growth Monitor {#AppVersion}
AppPublisher=bbkien2312
AppPublisherURL=https://github.com/bbkien2312/DiskGrowthMonitor
AppSupportURL=https://github.com/bbkien2312/DiskGrowthMonitor/issues
DefaultDirName={code:DefaultDirectory}
DefaultGroupName=Disk Growth Monitor
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#InstallerOutputDir}
OutputBaseFilename=installer
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
DisableDirPage=no
CloseApplications=no
RestartApplications=no
SetupLogging=yes
CreateUninstallRegKey=not PortableMode
UninstallDisplayIcon={app}\DiskGrowthMonitor.exe
InfoBeforeFile=INSTALL-NOTES.txt
VersionInfoVersion={#AppVersion}.0

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked; Check: not PortableMode

[Files]
Source: "{#SourceRoot}\dist\DiskGrowthMonitor\DiskGrowthMonitor.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\dist\DiskGrowthMonitor\dgm.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\dist\DiskGrowthMonitor\.venv\*"; DestDir: "{app}\.venv"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceRoot}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\RELEASE_NOTES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\THIRD_PARTY.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "installed-app.json"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Disk Growth Monitor"; Filename: "{app}\DiskGrowthMonitor.exe"; WorkingDir: "{app}"; Check: not PortableMode
Name: "{group}\Uninstall Disk Growth Monitor"; Filename: "{uninstallexe}"; Check: not PortableMode
Name: "{autodesktop}\Disk Growth Monitor"; Filename: "{app}\DiskGrowthMonitor.exe"; WorkingDir: "{app}"; Tasks: desktopicon; Check: not PortableMode

[Run]
Filename: "{app}\DiskGrowthMonitor.exe"; Description: "Launch Disk Growth Monitor"; Flags: nowait postinstall skipifsilent; Check: not PortableMode

[Code]
function PortableMode: Boolean;
begin
  Result := ExpandConstant('{param:PORTABLE|0}') = '1';
end;

function DefaultDirectory(Param: String): String;
begin
  if DirExists('D:\') then
    Result := 'D:\DiskGrowthMonitor-App'
  else
    Result := ExpandConstant('{localappdata}\Programs\DiskGrowthMonitor');
end;

function HasFiles(Directory: String): Boolean;
var Found: TFindRec;
begin
  Result := False;
  if FindFirst(AddBackslash(Directory) + '*', Found) then begin
    try
      repeat
        if (Found.Name <> '.') and (Found.Name <> '..') then begin
          Result := True;
          Break;
        end;
      until not FindNext(Found);
    finally
      FindClose(Found);
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Target, Guard: String; Marker: AnsiString; ExitCode: Integer;
begin
  Result := '';
  Target := ExpandConstant('{app}');
  if HasFiles(Target) and not FileExists(AddBackslash(Target) + 'installed-app.json') then
    Result := 'Choose an empty folder. This installer must not overwrite source code or unrelated files.';
  if (Result = '') and FileExists(AddBackslash(Target) + 'installed-app.json') then begin
    Guard := 'uninstall-check';
    if LoadStringFromFile(AddBackslash(Target) + 'installed-app.json', Marker) then
      if (Pos('"update_protocol":1', String(Marker)) > 0) or (Pos('"update_protocol": 1', String(Marker)) > 0) then Guard := 'upgrade-check';
    if not Exec(AddBackslash(Target) + 'dgm.exe', '--home "' + Target + '" ' + Guard,
      Target, SW_HIDE, ewWaitUntilTerminated, ExitCode) then
      Result := 'Cannot check the previous installation. No update was started.'
    else if ExitCode <> 0 then
      Result := 'Close the application and stop scans. Disable/stop its Task or Service before upgrading; legacy versions may require removing the runner.';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var PortableValue: String;
begin
  if CurStep = ssPostInstall then begin
    if PortableMode then PortableValue := 'true' else PortableValue := 'false';
    SaveStringToFile(ExpandConstant('{app}\installed-app.json'),
      '{"app":"DiskGrowthMonitor","version":"{#AppVersion}","update_protocol":1,"portable":' + PortableValue +
      ',"runtime":"private bundled .venv","preserve_on_uninstall":["data","logs",".venv/tools"]}', False);
  end;
end;

function InitializeUninstall: Boolean;
var ExitCode: Integer;
begin
  Result := False;
  if not Exec(ExpandConstant('{app}\dgm.exe'), '--home "' + ExpandConstant('{app}') + '" uninstall-check',
    ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) then begin
    SuppressibleMsgBox('Cannot verify application/background status. Close the app before trying again.', mbError, MB_OK, IDOK);
    Exit;
  end;
  if ExitCode <> 0 then begin
    SuppressibleMsgBox('Close Disk Growth Monitor and stop scans. Remove its Task/Service in the application before uninstalling. No files have been removed.', mbError, MB_OK, IDOK);
    Exit;
  end;
  Result := True;
end;

// No recursive UninstallDelete: preserve data, logs and tools downloaded later.
