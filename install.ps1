<#
.SYNOPSIS
    TAU one-command installer for Windows. Works in the Windows PowerShell 5.1 that
    ships with Windows, and in PowerShell 7.

.DESCRIPTION
    From the web:          powershell -NoProfile -ExecutionPolicy Bypass -Command "irm <raw URL of this file> | iex"
    From a TAU checkout:   powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1

    What it does, in order. Each step is skipped if it's already done, so it's safe to run again.
      1. Gets a private copy of uv and Python 3.12 (no admin, nothing system-wide).
      2. Makes TAU's own Python environment and installs OpenJarvis from PyPI.
         Nothing is compiled: no Rust toolchain, no OpenJarvis installer, no Ollama.
      3. Installs TAU (with the CPU-only build of torch).
      4. Installs Kokoro and misaki without their GPL extras.
      5. Downloads the voice and hearing models up front, saying what each one is.
      6. Adds the `tau` command and a Start-menu shortcut called TAU.
      7. Starts `tau setup`, one question at a time.

    Everything the tools print goes to a log file. The screen shows one plain line per thing.

    Environment: TAU_INSTALL_DIR (default %LOCALAPPDATA%\TAU), TAU_SOURCE (a TAU source folder),
    TAU_SOURCE_ZIP (where to download TAU from), OPENJARVIS_HOME (where OpenJarvis and TAU keep
    settings), TAU_OPENJARVIS_INSTALLER (only with -UseOpenJarvisInstaller).

.PARAMETER DryRun
    Say what would run and change nothing.
.PARAMETER NoSetup
    Stop before `tau setup`.
.PARAMETER NoVoice
    Text only: skip the voice packages and models.
.PARAMETER NoModels
    Skip downloading the voice and hearing models now (they download on first use instead).
.PARAMETER UseOpenJarvisInstaller
    Use OpenJarvis's own installer and its environment instead. It compiles Rust and installs Ollama.
.PARAMETER Uninstall
    Remove TAU's environment, the `tau` command and the Start-menu shortcut. Your settings stay.
.PARAMETER AllowElevated
    Install even when this window's account isn't the one signed in at the desktop (for example a
    "Run as administrator" window signed in as Administrator). TAU needs no admin rights.
#>
param(
    [switch] $DryRun,
    [switch] $NoSetup,
    [switch] $NoVoice,
    [switch] $NoModels,
    [switch] $UseOpenJarvisInstaller,
    [switch] $Uninstall,
    [switch] $AllowElevated
)

$ErrorActionPreference = 'Stop'
# Windows PowerShell 5.1's progress bar makes downloads many times slower; it also hides our own lines.
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }

# Measured on a clean GitHub windows-latest runner (see results/tau_build_notes.md, Round 3).
$EstimateMinutes = '3 minutes on a fast connection, up to 15 on a slow one,'
$EstimateDownload = 'about 1.8 GB'
$Total = 7

# A window opened with "Run as administrator" can be signed in as another account (for example
# the built-in Administrator). Then %LOCALAPPDATA% is that account's, TAU lands in its folders and
# the Start-menu shortcut points there, so the signed-in user's TAU does nothing. Refuse that case:
# the account running this script must be the one signed in at the desktop (explorer.exe's owner).
$deskUser = $null
try {
    $owners = Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" -ErrorAction Stop |
        ForEach-Object { (Invoke-CimMethod -InputObject $_ -MethodName GetOwner -ErrorAction Stop).User }
    $deskUser = @($owners | Where-Object { $_ }) | Select-Object -First 1
} catch { }
if ($deskUser -and ($deskUser -ne $env:USERNAME) -and -not $AllowElevated -and -not $DryRun) {
    Write-Host ''
    Write-Host "This window is running as $env:USERNAME, but $deskUser is signed in at the desktop."
    Write-Host "Installing from here would put TAU in $env:USERNAME's folders, and $deskUser's Start menu"
    Write-Host 'would open nothing. TAU needs no admin rights: close this window, open PowerShell normally'
    Write-Host '(not "Run as administrator"), and run the same command again.'
    Write-Host 'To install for this account anyway, add -AllowElevated.'
    exit 3
}

$installDir = if ($env:TAU_INSTALL_DIR) { $env:TAU_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA 'TAU' }
$venv = Join-Path $installDir 'venv'
$toolsDir = Join-Path $installDir 'tools'
$logDir = Join-Path $installDir 'logs'
$sourceZip = if ($env:TAU_SOURCE_ZIP) { $env:TAU_SOURCE_ZIP } else { 'https://codeload.github.com/RAnthonyBeckett/tau/zip/refs/heads/main' }
$ojInstaller = if ($env:TAU_OPENJARVIS_INSTALLER) { $env:TAU_OPENJARVIS_INSTALLER } else { 'https://open-jarvis.github.io/OpenJarvis/install.ps1' }
if ($UseOpenJarvisInstaller) {
    $ojRoot = if ($env:OPENJARVIS_HOME) { $env:OPENJARVIS_HOME } else { Join-Path $env:LOCALAPPDATA 'OpenJarvis' }
    $venv = Join-Path (Join-Path $ojRoot 'src') '.venv'
}
$py = Join-Path (Join-Path $venv 'Scripts') 'python.exe'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = Join-Path $logDir "install-$stamp.log"
if (-not $DryRun) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null; Set-Content -Path $log -Value "TAU install log, $stamp" -Encoding UTF8 }
$clock = [Diagnostics.Stopwatch]::StartNew()
if (-not $DryRun) { Add-Content -Path $log -Value "PowerShell $($PSVersionTable.PSVersion) ($($PSVersionTable.PSEdition))" -Encoding UTF8 }

# ------------------------------------------------------------------ helpers
function Say([string] $Text) { Write-Host $Text }
function Step([int] $N, [string] $Text) { Write-Host ''; Write-Host "[$N/$Total] $Text" }
function Fail([string] $What, [string] $Next) {
    Write-Host ''
    Write-Host "Stopped: $What"
    Write-Host "Next: $Next"
    if (-not $DryRun) { Write-Host "The details are in $log" }
    exit 1
}
function Log([string] $Text) { if (-not $DryRun) { Add-Content -Path $log -Value $Text -Encoding UTF8 } }

function Format-Secs([int] $S) {
    if ($S -lt 60) { return "$S seconds" }
    $m = [math]::Round($S / 60)
    if ($m -eq 1) { return 'about 1 minute' }
    return "about $m minutes"
}

# Quote one argument the way Windows programs read their command line. Windows PowerShell 5.1
# drops embedded quotes when it passes arguments itself, so every native call goes through here.
function Quote-Arg([string] $A) {
    if ($A -eq '') { return '""' }
    if ($A -notmatch '[\s"]') { return $A }
    $s = [regex]::Replace($A, '(\\*)"', { param($m) ($m.Groups[1].Value * 2) + '\"' })
    $s = [regex]::Replace($s, '(\\+)$', { param($m) $m.Groups[1].Value * 2 })
    return '"' + $s + '"'
}

# Run a program with its output going to the log, never to the screen. Says what's happening every
# 15 seconds so the screen never sits still. Returns $true when it exits with 0.
# Uses Start-Process with redirected files, so a program writing to stderr can't turn into a
# terminating NativeCommandError under Windows PowerShell 5.1 with ErrorActionPreference = Stop.
function Invoke-Logged([string] $Label, [string] $Exe, [string[]] $Arguments, [string] $Typical = '') {
    if ($DryRun) { Say ("    would run: $Exe " + ($Arguments -join ' ')); return $true }
    $line = ($Arguments | ForEach-Object { Quote-Arg $_ }) -join ' '
    Log ''
    Log ">>> $Exe $line"
    $out = [IO.Path]::GetTempFileName()
    $err = [IO.Path]::GetTempFileName()
    try {
        $p = Start-Process -FilePath $Exe -ArgumentList $line -NoNewWindow -PassThru `
            -RedirectStandardOutput $out -RedirectStandardError $err
    } catch {
        Log "could not start: $($_.Exception.Message)"
        return $false
    }
    $null = $p.Handle   # keeps ExitCode readable after the process ends
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $next = 15
    while (-not $p.HasExited) {
        Start-Sleep -Milliseconds 500
        $s = [int]$sw.Elapsed.TotalSeconds
        if ($s -ge $next) {
            $next += 15
            $t = if ($Typical) { "; this usually takes $Typical" } else { '' }
            Say "    still $Label ($(Format-Secs $s) so far$t)"
        }
    }
    $p.WaitForExit()
    foreach ($f in @($out, $err)) {
        $text = Get-Content -Raw -Path $f -ErrorAction SilentlyContinue
        if ($text) { Log $text }
        Remove-Item -Force -Path $f -ErrorAction SilentlyContinue
    }
    Log "<<< exit $($p.ExitCode) after $([int]$sw.Elapsed.TotalSeconds) s"
    return ($p.ExitCode -eq 0)
}

# Python one-liner: $true when it exits with 0. Quiet, logged, never terminating.
function Test-Py([string] $Code) {
    if ($DryRun -or -not (Test-Path $py)) { return $false }
    return (Invoke-Logged 'checking' $py @('-c', $Code))
}

# A step whose own output is meant for the screen (TAU's own commands print plain lines).
# Their stderr still goes to the log.
function Invoke-Shown([string] $Exe, [string[]] $Arguments) {
    if ($DryRun) { Say ("    would run: $Exe " + ($Arguments -join ' ')); return $true }
    $line = ($Arguments | ForEach-Object { Quote-Arg $_ }) -join ' '
    Log ">>> $Exe $line"
    $err = [IO.Path]::GetTempFileName()
    $p = Start-Process -FilePath $Exe -ArgumentList $line -NoNewWindow -PassThru -Wait -RedirectStandardError $err
    $text = Get-Content -Raw -Path $err -ErrorAction SilentlyContinue
    if ($text) { Log $text }
    Remove-Item -Force -Path $err -ErrorAction SilentlyContinue
    return ($p.ExitCode -eq 0)
}

function Get-File([string] $Url, [string] $Dest) {
    if ($DryRun) { Say "    would download: $Url"; return $true }
    try {
        Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing -TimeoutSec 300
        return $true
    } catch {
        Log "download failed: $Url : $($_.Exception.Message)"
        return $false
    }
}

$hasUv = $false
$uv = $null
function Install-Packages([string] $Label, [string[]] $PipArgs, [string] $Typical = '') {
    if ($script:hasUv) { return (Invoke-Logged $Label $script:uv (@('pip', 'install', '--python', $py) + $PipArgs) $Typical) }
    return (Invoke-Logged $Label $py (@('-m', 'pip', 'install', '--disable-pip-version-check') + $PipArgs) $Typical)
}

# ------------------------------------------------------------------ uninstall
if ($Uninstall) {
    Say 'Removing TAU from this computer. Your settings and TAU''s notes stay where they are.'
    if (Test-Path $py) { [void](Invoke-Shown $py @('-m', 'tau.launcher', 'remove')) }
    if ($DryRun) { Say "    would remove: $installDir"; exit 0 }
    Remove-Item -Recurse -Force -Path $venv -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force -Path $toolsDir -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force -Path (Join-Path $installDir 'src') -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force -Path (Join-Path $installDir 'bin') -ErrorAction SilentlyContinue
    Say 'Done. The `tau` command and the Start-menu shortcut are gone.'
    exit 0
}

# ------------------------------------------------------------------ hello
Say 'Installing TAU, a voice-first lab assistant.'
Say "This takes about $EstimateMinutes and downloads $EstimateDownload. I'll say what's happening the whole way."
if (-not $DryRun) { Say "Everything the tools print goes to $log" }

# ------------------------------------------------------------------ 1 uv and Python
Step 1 'Python tools (a private copy of uv and Python 3.12, about 50 MB)'
if (Test-Path $py) {
    Say "    TAU's Python environment is already here: $venv"
}
$found = Get-Command uv -ErrorAction SilentlyContinue
if ($found) { $uv = $found.Source; $hasUv = $true }
if (-not $hasUv) {
    $privateUv = Join-Path $toolsDir 'uv.exe'
    if (Test-Path $privateUv) { $uv = $privateUv; $hasUv = $true }
    elseif (-not $UseOpenJarvisInstaller) {
        $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'aarch64' } else { 'x86_64' }
        $uvUrl = "https://github.com/astral-sh/uv/releases/latest/download/uv-$arch-pc-windows-msvc.zip"
        Say '    downloading uv, a fast Python package tool (about 20 MB)'
        $zip = Join-Path $env:TEMP 'tau-uv.zip'
        if (Get-File $uvUrl $zip) {
            if ($DryRun) { $uv = $privateUv; $hasUv = $true }
            else {
                try {
                    New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
                    Expand-Archive -Path $zip -DestinationPath $toolsDir -Force
                    $inner = Get-ChildItem -Path $toolsDir -Recurse -Filter 'uv.exe' | Select-Object -First 1
                    if ($inner -and $inner.FullName -ne $privateUv) { Move-Item -Force $inner.FullName $privateUv }
                    if (Test-Path $privateUv) { $uv = $privateUv; $hasUv = $true }
                } catch { Log "uv unpack failed: $($_.Exception.Message)" }
            }
        }
        if (-not $hasUv) { Say "    couldn't get uv; I'll use this computer's own Python instead" }
    }
}
if ($hasUv -and -not $DryRun) {
    # Keep uv's Python inside TAU's folder, not system-wide.
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $installDir 'python'
}

# ------------------------------------------------------------------ 2 OpenJarvis
Step 2 'OpenJarvis (from PyPI: plain Python, nothing to compile)'
if ($UseOpenJarvisInstaller) {
    if (Test-Py 'import openjarvis') { Say "    already installed in $venv" }
    else {
        Say "    running OpenJarvis's own installer (it compiles Rust and sets up Ollama; this can take 30 minutes)"
        $script = $null
        try {
            if (Test-Path $ojInstaller) { $script = Get-Content -Raw $ojInstaller }
            else { $script = Invoke-RestMethod -Uri $ojInstaller -TimeoutSec 30 }
        } catch { $script = $null }
        if (-not $script) { Fail "I couldn't download OpenJarvis's installer." 'Run this script again without -UseOpenJarvisInstaller.' }
        if ($DryRun) { Say "    would run: OpenJarvis installer from $ojInstaller" }
        else {
            $tmp = Join-Path $env:TEMP 'openjarvis-install.ps1'
            Set-Content -Path $tmp -Value $script -Encoding UTF8
            $exe = (Get-Process -Id $PID).Path
            if (-not (Invoke-Shown $exe @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $tmp, '-SkipService'))) {
                Fail "OpenJarvis's installer didn't finish." 'Run this script again without -UseOpenJarvisInstaller.'
            }
        }
    }
} else {
    if (-not (Test-Path $py)) {
        Say "    making TAU's own Python environment in $venv"
        if ($hasUv) {
            if (-not (Invoke-Logged 'making the environment' $uv @('venv', '--seed', '--python', '3.12', $venv) '1 minute')) {
                Fail "I couldn't make a Python environment." 'Check your internet connection, then run this script again.'
            }
        } else {
            $pyVer = $null
            if (Get-Command py -ErrorAction SilentlyContinue) {
                foreach ($v in '3.12', '3.11', '3.13', '3.10') {   # OpenJarvis supports 3.10 to 3.13
                    if ($DryRun) { $pyVer = $v; break }
                    if (Invoke-Logged 'checking' 'py' @("-$v", '-c', 'import sys')) { $pyVer = $v; break }
                }
            }
            if (-not $pyVer) { Fail "I couldn't get uv, and this computer has no Python 3.10 to 3.13." 'Install Python 3.12 from python.org, then run this script again.' }
            if (-not (Invoke-Logged 'making the environment' 'py' @("-$pyVer", '-m', 'venv', $venv))) {
                Fail "I couldn't make a Python environment in $venv." 'Check you can write to that folder, then run this script again.'
            }
        }
    }
    if (Test-Py 'import openjarvis') { Say '    OpenJarvis is already installed' }
    elseif (-not (Install-Packages 'installing OpenJarvis' @('openjarvis') '1 to 2 minutes')) {
        Fail "Installing OpenJarvis from PyPI didn't work." 'Check your internet connection, then run this script again.'
    }
}
if (-not $DryRun -and -not (Test-Py 'import openjarvis')) { Fail "OpenJarvis isn't working in $venv." 'Run this script again; if it stops here twice, send the log to whoever gave you TAU.' }
Say '    OpenJarvis is ready'

# ------------------------------------------------------------------ 3 TAU
Step 3 'TAU'
$src = $env:TAU_SOURCE
if (-not $src) {
    $here = if ($PSScriptRoot) { $PSScriptRoot } else { $null }
    if ($here -and (Test-Path (Join-Path $here 'pyproject.toml')) -and
        (Select-String -Path (Join-Path $here 'pyproject.toml') -Pattern '^name = "tau"' -Quiet)) {
        $src = $here
    } else {
        Say '    downloading TAU (about 5 MB)'
        $zip = Join-Path $env:TEMP 'tau-src.zip'
        $unz = Join-Path $installDir 'src'
        if (-not (Get-File $sourceZip $zip)) { Fail "I couldn't download TAU." 'Check your internet connection, then run this script again.' }
        if ($DryRun) { $src = Join-Path $unz 'tau' }
        else {
            Remove-Item -Recurse -Force -Path $unz -ErrorAction SilentlyContinue
            Expand-Archive -Path $zip -DestinationPath $unz -Force
            $proj = Get-ChildItem -Path $unz -Recurse -Filter 'pyproject.toml' |
                Where-Object { Select-String -Path $_.FullName -Pattern '^name = "tau"' -Quiet } | Select-Object -First 1
            if (-not $proj) { Fail "The TAU download didn't contain TAU." 'Run this script again in a few minutes.' }
            $src = $proj.DirectoryName
        }
    }
}
if (-not $DryRun -and -not (Test-Path (Join-Path $src 'pyproject.toml'))) { Fail "There's no TAU source at $src." 'Set TAU_SOURCE to the tau folder, or unset it, then run this script again.' }
$extras = if ($NoVoice) { 'display,windows' } else { 'voice,display,windows' }
if (Test-Py 'import tau') { Say '    TAU is already installed; updating it' }
if (-not $NoVoice -and -not (Test-Py 'import torch')) {
    # The CPU-only torch build avoids NVIDIA's proprietary CUDA libraries (TAU runs voice on the CPU).
    Say '    installing torch, the maths library the voice uses (CPU-only build, about 200 MB)'
    if (-not (Install-Packages 'installing torch' @('torch', '--index-url', 'https://download.pytorch.org/whl/cpu') '2 to 4 minutes')) {
        Say "    (the CPU-only build wasn't reachable; the normal build will come with TAU)"
    }
}
Say '    installing TAU and the packages it needs (about 300 MB)'
# A PEP 508 file URL works the same in uv and pip, with spaces in the path and on any drive.
$tauReq = if ($DryRun) { "tau[$extras] @ $src" } else { "tau[$extras] @ " + ([Uri](Resolve-Path $src).Path).AbsoluteUri }
$tauArgs = if ($hasUv) { @('--reinstall-package', 'tau', $tauReq) } else { @($tauReq) }
if (-not (Install-Packages 'installing TAU' $tauArgs '2 to 5 minutes')) { Fail "Installing TAU didn't work." 'Check your internet connection, then run this script again.' }
Say '    TAU is installed'

# ------------------------------------------------------------------ 4 Kokoro
Step 4 'Voice packages (Kokoro, without GPL extras)'
if ($NoVoice) {
    Say '    skipped (text only)'
} elseif (Test-Py "import importlib.util as u, sys; sys.exit(0 if all(u.find_spec(m) for m in ('kokoro', 'misaki', 'en_core_web_sm')) else 1)") {
    Say '    already installed'
} else {
    $nodeps = @('--no-deps', '-r', (Join-Path $src 'requirements-kokoro.txt'))
    $next = 'Run this script again, or add -NoVoice to finish without a voice.'
    if (Test-Py 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,13) else 1)') {
        # kokoro and misaki declare Python <3.13 but run on it; only pip can be told to ignore that.
        if (-not (Invoke-Logged 'installing Kokoro' $py (@('-m', 'pip', 'install', '--ignore-requires-python') + $nodeps) '1 minute')) { Fail "Installing Kokoro didn't work." $next }
    } elseif (-not (Install-Packages 'installing Kokoro' $nodeps '1 minute')) { Fail "Installing Kokoro didn't work." $next }
    Say '    Kokoro is installed'
}

# ------------------------------------------------------------------ 5 models
Step 5 'Voice and hearing models (downloaded now, so nothing downloads mid-conversation)'
if ($NoVoice -or $NoModels) { Say '    skipped' }
elseif (-not (Invoke-Shown $py @('-m', 'tau', 'fetch-models'))) {
    Say "    Some models didn't download. TAU still works by typing; run 'tau fetch-models' later to try again."
}

# ------------------------------------------------------------------ 6 launcher
Step 6 'The tau command and a Start-menu shortcut'
if (-not (Invoke-Shown $py @('-m', 'tau.launcher', 'install'))) {
    Say "    I couldn't add the shortcut. You can still start TAU with: $py -m tau run"
}

Say ''
Say "TAU is installed ($(Format-Secs ([int]$clock.Elapsed.TotalSeconds))). Open it from the Start menu: TAU."
Log "install finished in $([int]$clock.Elapsed.TotalSeconds) s"

# ------------------------------------------------------------------ 7 setup
$tau = Join-Path (Join-Path $venv 'Scripts') 'tau.exe'
Step 7 'Setup'
if ($NoSetup) { Say "    skipped. Run 'tau setup' when you're ready."; exit 0 }
Say '    starting tau setup'
if ($DryRun) { Say "    would run: $tau setup"; exit 0 }
$ErrorActionPreference = 'Continue'
& $tau setup
exit $LASTEXITCODE
