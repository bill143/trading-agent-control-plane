<#
.SYNOPSIS
    Builds Trading System from this source folder and installs it for the current Windows user.

.DESCRIPTION
    No administrator rights, no Git and no GitHub sign-in needed. Steps:
      1. installs uv 0.11.32 (Python/dependency manager) for this user if missing;
      2. installs the pinned Python 3.13.14 and every locked package (uv sync --frozen);
      3. bundles the app with PyInstaller (5-10 minutes on first run);
      4. copies it to %LOCALAPPDATA%\Programs\TradingSystem (replacing an older copy, keeping
         your data, users, vault and settings in %LOCALAPPDATA%\TradingSystem);
      5. creates "Trading System" icons on the desktop and in the Start menu;
      6. opens the app (unless -NoLaunch).

    API keys and logins are never typed here: enter them in the app under
    Settings > Connections, where they go into the encrypted vault.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install-windows.ps1
#>
[CmdletBinding()]
param([switch]$NoLaunch)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProgressPreference = 'SilentlyContinue'

$UvVersion = '0.11.32'
$Repo      = Split-Path -Parent $PSScriptRoot
$AppDir    = Join-Path $env:LOCALAPPDATA 'Programs\TradingSystem'
$Exe       = Join-Path $AppDir 'TradingSystem.exe'
Set-Location $Repo

function Step([string]$m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Run([string]$file, [string[]]$argv) {
    & $file @argv
    if ($LASTEXITCODE -ne 0) { throw "'$file $($argv -join ' ')' failed (exit $LASTEXITCODE)" }
}

if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw '64-bit Windows (x64) is required.' }

# --- 1. uv --------------------------------------------------------------------------------------
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv -or -not ((& uv --version) -match [regex]::Escape($UvVersion))) {
    Step "Installing uv $UvVersion (for this user only)"
    $installer = Join-Path $env:TEMP "uv-installer-$UvVersion.ps1"
    Invoke-WebRequest -UseBasicParsing -Uri "https://astral.sh/uv/$UvVersion/install.ps1" -OutFile $installer
    & powershell -NoProfile -ExecutionPolicy Bypass -File $installer
    if ($LASTEXITCODE -ne 0) { throw 'uv installation failed' }
    Remove-Item $installer -Force
}
Step "uv: $(& uv --version)"

# --- 2. Python + locked packages ----------------------------------------------------------------
Step 'Installing Python 3.13.14 and the locked packages (first run downloads ~300 MB)'
Run uv @('sync', '--frozen')

# --- 3. Bundle ----------------------------------------------------------------------------------
Step 'Bundling the app (5-10 minutes on first run)'
Run uv @('run', '--frozen', 'pyinstaller', 'packaging/TradingSystem.spec', '--noconfirm',
         '--distpath', 'dist', '--workpath', 'build', '--log-level', 'WARN')
$built = Join-Path $Repo 'dist\TradingSystem\TradingSystem.exe'
if (-not (Test-Path $built)) { throw 'build did not produce TradingSystem.exe' }

# --- 4. Install ---------------------------------------------------------------------------------
Step "Installing to $AppDir"
Get-Process -Name TradingSystem -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 1
New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
& robocopy (Join-Path $Repo 'dist\TradingSystem') $AppDir /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "copy failed (robocopy exit $LASTEXITCODE)" }
$global:LASTEXITCODE = 0

# --- 5. Shortcuts -------------------------------------------------------------------------------
Step 'Creating desktop and Start menu icons'
$shell = New-Object -ComObject WScript.Shell
foreach ($folder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
    $lnk = $shell.CreateShortcut((Join-Path $folder 'Trading System.lnk'))
    $lnk.TargetPath = $Exe
    $lnk.WorkingDirectory = $AppDir
    $lnk.IconLocation = "$Exe,0"
    $lnk.Description = 'Trading System'
    $lnk.Save()
}

# --- WebView2 check (the app window uses Microsoft Edge WebView2) -------------------------------
$wv2 = 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
$hasWv2 = (Test-Path "HKLM:\$wv2") -or (Test-Path "HKCU:\$($wv2 -replace 'WOW6432Node\\','')")
if (-not $hasWv2) {
    Write-Warning ('Microsoft Edge WebView2 Runtime was not found. If the app window stays blank, ' +
                   'install it from https://developer.microsoft.com/microsoft-edge/webview2/ (Evergreen).')
}

Write-Host ''
Write-Host 'Trading System is installed. Use the "Trading System" icon on your desktop.' -ForegroundColor Green
if (-not $NoLaunch) { Start-Process $Exe }
