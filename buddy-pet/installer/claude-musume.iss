; Claude娘 安装程序。由 build.py 调用 ISCC 编译，路径和版本号通过 /D 传进来，不要直接编译。
; 装到当前用户的 %LOCALAPPDATA%\Programs，不需要管理员权限；Claude Code 插件装到 %USERPROFILE%\.claude\skills。

#ifndef AppName
  #error 请用 build.py 编译
#endif
#define ExeName AppName + ".exe"

[Setup]
AppId={{6F0B7E52-3C1D-4D8A-9B5E-2A7C4E1F9D30}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Ancean
AppPublisherURL=https://github.com/Ancean/claude-musume
DefaultDirName={userpf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutDir}
OutputBaseFilename={#AppName}-安装程序
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#ExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "chs"; MessagesFile: "{#ChineseIsl}"

[Tasks]
Name: "follow"; Description: "开机后常驻一个小哨兵，Claude 桌面端打开时叫她出来、关掉时她跟着退出"; GroupDescription: "启动方式："
Name: "desktopicon"; Description: "在桌面创建快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked

[Files]
Source: "{#AppDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; 给 Claude Code 的插件：把额度、上下文、花费和任务进度实时写给她。
Source: "{#PluginDir}\*"; DestDir: "{%USERPROFILE}\.claude\skills\claude-buddy-bridge"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#ExeName}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#ExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ClaudeBuddyPet"; ValueData: """{app}\{#ExeName}"" --follow"; Flags: uninsdeletevalue; Tasks: follow

[Run]
Filename: "{app}\{#ExeName}"; Parameters: "--follow"; Flags: nowait; Tasks: follow
Filename: "{app}\{#ExeName}"; Description: "现在就叫她出来"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 运行时生成的文件。lines.json（台词）和 config.json（设置）留着，重装后还在；不要了可以手动删掉整个文件夹。
Type: filesandordirs; Name: "{app}\sounds"
Type: files; Name: "{app}\met.json"
Type: files; Name: "{app}\pet.log"
Type: files; Name: "{app}\follow.log"

[Code]
// 覆盖安装和卸载前先关掉正在运行的桌宠和哨兵，不然文件被占用。只按程序名结束，不碰别的进程。
procedure StopRunning();
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM "{#ExeName}"', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunning();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  StopRunning();
  Result := True;
end;
