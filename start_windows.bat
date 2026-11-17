@echo off
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
set "WG_PROJECT_ROOT=%~dp0"
set "WG_LAUNCH_LAN=0"
if /I "%~1"=="--lan" set "WG_LAUNCH_LAN=1"
cd /d "%~dp0"
if not exist bootstrap.py goto incomplete
if not exist data\catalog.json goto incomplete
if exist .runtime\python\python.exe goto run
where powershell.exe >nul 2>&1
if errorlevel 1 goto failed
echo Preparing portable Python. Keep this window open.
powershell.exe -NoLogo -NoProfile -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $r=$env:WG_PROJECT_ROOT; $cache=Join-Path $r '.runtime'; New-Item -ItemType Directory -Path $cache -Force | Out-Null; $zip=Join-Path $r 'portable\python-3.13.17-embed-amd64.zip'; if (!(Test-Path $zip)) { $zip=Join-Path $cache 'python-3.13.17-embed-amd64.zip'; if (!(Test-Path $zip)) { Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.13.17/python-3.13.17-embed-amd64.zip' -OutFile $zip } }; if ((Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower() -ne '9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38') { throw 'Python SHA256 mismatch. Download the complete package again.' }; $stage=Join-Path $cache 'python.stage'; if(Test-Path $stage){Remove-Item -Recurse -Force $stage}; Expand-Archive -LiteralPath $zip -DestinationPath $stage; [IO.File]::WriteAllText((Join-Path $stage 'python313._pth'), ('python313.zip'+[Environment]::NewLine+'.'+[Environment]::NewLine+'..\..'+[Environment]::NewLine+'Lib\site-packages'+[Environment]::NewLine+'import site'+[Environment]::NewLine)); Move-Item -LiteralPath $stage -Destination (Join-Path $cache 'python')"
if errorlevel 1 goto failed
:run
.runtime\python\python.exe -X utf8 bootstrap.py
if errorlevel 1 goto failed
exit /b 0
:incomplete
echo Project files are missing. Extract the COMPLETE V24 archive into a NEW folder.
goto failed
:failed
echo Startup failed. Check the FIRST error above and .runtime\launch_error.log.
echo Keep this window open when asking for help.
pause
exit /b 1
