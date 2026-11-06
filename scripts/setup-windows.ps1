<#
.SYNOPSIS
    Sets up the Windows development environment for the trading system (build step 0.05).

.DESCRIPTION
    Installs (per-user, no admin rights needed) and pins:
      * uv 0.11.32            - Python and dependency manager
      * Python 3.13.14        - from .python-version, managed by uv
      * Rust 1.97.1           - from rust-toolchain.toml, via rustup
      * all Python packages   - exactly as locked in uv.lock (uv sync --frozen)
      * pre-commit hooks      - gitleaks secret scan + lint/type checks on every commit
    Then verifies everything with tools/check_toolchain.py and runs the test suite.

    Safe to re-run: every step checks before it installs.

.EXAMPLE
    PS> cd C:\path\to\trading-agent-control-plane
    PS> powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipRust,
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$UvVersion   = '0.11.32'
$RepoRoot    = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }

function Invoke-Checked {
    param([string]$File, [string[]]$Arguments)
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "'$File $($Arguments -join ' ')' failed with exit code $LASTEXITCODE" }
}

function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user    = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$user;$machine;$env:USERPROFILE\.local\bin;$env:USERPROFILE\.cargo\bin"
}

if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
    throw "64-bit Windows (x64) is required; found $env:PROCESSOR_ARCHITECTURE."
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'Git for Windows is required: https://git-scm.com/download/win'
}

# --- uv -------------------------------------------------------------------------------------
Update-SessionPath
$haveUv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $haveUv -or -not ((& uv --version) -match [regex]::Escape($UvVersion))) {
    Write-Step "Installing uv $UvVersion"
    $installer = Join-Path $env:TEMP "uv-installer-$UvVersion.ps1"
    Invoke-WebRequest -UseBasicParsing -Uri "https://astral.sh/uv/$UvVersion/install.ps1" -OutFile $installer
    & powershell -NoProfile -ExecutionPolicy Bypass -File $installer
    if ($LASTEXITCODE -ne 0) { throw 'uv installation failed' }
    Remove-Item $installer -Force
    Update-SessionPath
}
Write-Step "uv: $(& uv --version)"

# --- Python + locked dependencies -------------------------------------------------------------
$pythonPin = (Get-Content .python-version -Raw).Trim()
Write-Step "Installing Python $pythonPin (uv-managed)"
Invoke-Checked uv @('python', 'install', $pythonPin)
Write-Step 'Installing locked dependencies (uv sync --frozen)'
Invoke-Checked uv @('sync', '--frozen')

# --- Rust -----------------------------------------------------------------------------------
if (-not $SkipRust) {
    if (-not (Get-Command rustup -ErrorAction SilentlyContinue)) {
        Write-Step 'Installing rustup (minimal profile)'
        $rustupInit = Join-Path $env:TEMP 'rustup-init.exe'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://win.rustup.rs/x86_64' -OutFile $rustupInit
        Invoke-Checked $rustupInit @('-y', '--profile', 'minimal', '--default-toolchain', 'none')
        Remove-Item $rustupInit -Force
        Update-SessionPath
    }
    Write-Step 'Installing the pinned Rust toolchain from rust-toolchain.toml'
    Invoke-Checked rustup @('toolchain', 'install')   # reads rust-toolchain.toml in this folder
    Write-Step "Rust: $(& rustc --version)"
}

# --- Git hooks ------------------------------------------------------------------------------
Write-Step 'Installing pre-commit hooks (gitleaks secret scan on every commit)'
Invoke-Checked uv @('run', '--frozen', 'pre-commit', 'install', '--install-hooks')

# --- Verify ---------------------------------------------------------------------------------
Write-Step 'Verifying toolchain pins'
$checkArgs = @('run', '--frozen', 'python', '-m', 'tools.check_toolchain')
if (-not $SkipRust) { $checkArgs += '--rust' }
Invoke-Checked uv $checkArgs

if (-not $SkipTests) {
    Write-Step 'Running the test suite'
    Invoke-Checked uv @('run', '--frozen', 'pytest')
}

Write-Host ''
Write-Host 'Development environment ready.' -ForegroundColor Green
