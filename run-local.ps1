<#
.SYNOPSIS
  Runs the whole chatbot on this machine, without Docker (except Tika): edit .env, then run this file.

.DESCRIPTION
  Uses SQL Server, Redis and MongoDB that are already running on this machine (or
  reachable from it) - supply their connection strings in .env. Every step is skipped
  when it is already done, so the same command is both the first run and every later one:

    1. .env           created from .env.sample if missing, then you fill it in
    2. Python / Node  checked (Python 3.12+, Node 22+ for the web UI)
    3. install        a .venv per service + pip packages, redone only when a
                      requirements file or EMBEDDING_TYPE changes
    4. check          .env is filled in and Redis, MongoDB and SQL Server answer
    5. start          compute service :8001, main-service :8000, web UI :3006
                      (main-service creates the database and tables itself)

  Ctrl+C stops everything, including the web UI window and the Tika container.

.EXAMPLE
  .\run-local.ps1

  If Windows refuses to run scripts ("running scripts is disabled"):
  powershell -ExecutionPolicy Bypass -File .\run-local.ps1

.PARAMETER Reinstall
  Redo the pip installs even if nothing changed.
.PARAMETER SkipInstall
  Do not install; use the .venv folders as they are.
.PARAMETER NoUi
  Start only the two services, not the web UI.
.PARAMETER CheckOnly
  Install and check, then stop without starting anything.
.PARAMETER Reload
  Restart main-service when its code changes (for development).
.PARAMETER NoBrowser
  Do not open the browser when the stack is up.
#>
[CmdletBinding()]
param(
    [switch]$Reinstall,
    [switch]$SkipInstall,
    [switch]$NoUi,
    [switch]$CheckOnly,
    [switch]$Reload,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$MainService = Join-Path $Root 'main-service-chatbot'
$Compute     = Join-Path $Root 'parsing-indexing-service'
$Web         = Join-Path $Root 'FE'
$EnvFile     = Join-Path $Root '.env'
$MainPort    = 8000
$UiPort      = 3006

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "    OK   $text" -ForegroundColor Green }
function Fail($text) { Write-Host ""; Write-Host "    STOP $text" -ForegroundColor Red; exit 1 }

# A native command's exit code, without PowerShell turning stderr text into an error.
function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    # Out-Host: the program's output goes to the screen as it arrives, and only the exit code is returned.
    try { & $Exe @Arguments | Out-Host; return $LASTEXITCODE }
    finally { $ErrorActionPreference = $previous }
}

# The value of NAME in .env, or $Default. Same rules as local.py: quotes and trailing # comments dropped.
function Get-EnvValue([string]$Name, [string]$Default = '') {
    foreach ($line in Get-Content -LiteralPath $EnvFile -Encoding UTF8) {
        if ($line -match "^\s*$([regex]::Escape($Name))\s*=\s*(.*)$") {
            $value = $Matches[1].Trim()
            if ($value.Length -gt 1 -and ($value[0] -eq '"' -or $value[0] -eq "'") -and $value[-1] -eq $value[0]) {
                return $value.Substring(1, $value.Length - 2)
            }
            $hash = $value.IndexOf(' #')
            if ($hash -ge 0) { $value = $value.Substring(0, $hash).TrimEnd() }
            return $value
        }
    }
    return $Default
}

function Test-PortFree([int]$Port) {
    $listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
    return -not ($listeners | Where-Object { $_.Port -eq $Port })
}

# ---------------------------------------------------------------- 1. .env ----
Step 'Configuration (.env)'
if (-not (Test-Path -LiteralPath $EnvFile)) {
    Copy-Item -LiteralPath (Join-Path $Root '.env.sample') -Destination $EnvFile
    Write-Host "    Created .env from .env.sample." -ForegroundColor Yellow
    Write-Host "    Open it and fill in: section 1 (chat model), section 2 (LOCAL_STORAGE_PATH) and"
    Write-Host "    sections 4-5 (SQL Server, Redis, MongoDB). Anything still saying CHANGE_ME is a blank to fill."
    Write-Host "    Then run this script again."
    Start-Process notepad.exe -ArgumentList "`"$EnvFile`""
    exit 1
}
Ok '.env found'

# ------------------------------------------------------ 2. Python and Node ----
Step 'Tools'
$Python = $null
foreach ($candidate in @(@('py', '-3.12'), @('py', '-3'), @('python'), @('python3'))) {
    if (-not (Get-Command $candidate[0] -ErrorAction SilentlyContinue)) { continue }
    # No quote characters inside the code: Windows PowerShell 5.1 drops embedded double quotes when it hands arguments to a program.
    $probe = @($candidate | Select-Object -Skip 1) + @('-c', 'import sys; print(sys.executable) if sys.version_info >= (3, 12) else None')
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $found = (& $candidate[0] @probe 2>$null | Out-String).Trim() } catch { $found = '' }
    finally { $ErrorActionPreference = $previous }
    if ($found -and (Test-Path -LiteralPath $found)) { $Python = $found; break }
}
if (-not $Python) { Fail 'Python 3.12 or newer was not found. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run this again.' }
Ok "Python: $Python"

if (-not $NoUi) {
    $node = Get-Command node -ErrorAction SilentlyContinue
    if (-not $node) { Fail 'Node 22+ is needed for the web UI: https://nodejs.org . Or run with -NoUi to start only the services.' }
    $nodeMajor = [int](((& node --version) -replace '^v', '') -split '\.')[0]
    if ($nodeMajor -lt 22) { Fail "Node $nodeMajor found, 22 or newer is needed for the web UI. Or run with -NoUi." }
    Ok "Node $nodeMajor"
}

# ------------------------------------------------------------- 3. install ----
$mainPy  = Join-Path $MainService '.venv\Scripts\python.exe'
$compPy  = Join-Path $Compute '.venv\Scripts\python.exe'
$stamp   = Join-Path $MainService '.venv\.local-install-stamp'

# What the installed packages were built from. A change to any of it means installing again.
$embedding = (Get-EnvValue 'EMBEDDING_TYPE' 'local').ToLower()
$inputs = @(
    (Join-Path $MainService 'requirements.txt'),
    (Join-Path $Compute 'requirements.txt'),
    (Join-Path $Compute 'requirements-local-embeddings.txt')
) | Where-Object { Test-Path -LiteralPath $_ }
$wanted = ((($inputs | ForEach-Object { (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash }) -join ':') + ":embedding=$embedding")
$installed = if (Test-Path -LiteralPath $stamp) { (Get-Content -LiteralPath $stamp -Raw).Trim() } else { '' }

Step 'Install'
if ($SkipInstall) {
    Ok 'skipped (-SkipInstall)'
} elseif (-not $Reinstall -and (Test-Path -LiteralPath $mainPy) -and (Test-Path -LiteralPath $compPy) -and $installed -eq $wanted) {
    Ok 'already installed (use -Reinstall to force)'
} else {
    if ($embedding -eq 'local') {
        Write-Host '    EMBEDDING_TYPE=local: the first install downloads PyTorch and a ~600 MB model. This is slow, once.' -ForegroundColor Yellow
    }
    if ((Invoke-Native $Python @('local.py', 'install')) -ne 0) { Fail 'Install failed - the error is above. Fix it and run this again; finished steps are not repeated.' }
    Set-Content -LiteralPath $stamp -Value $wanted -Encoding ASCII
    Ok 'installed'
}

# --------------------------------------------------------------- 4. check ----
Step 'Check (.env filled in, Redis / MongoDB / SQL Server reachable, ports free)'
if (-not (Test-PortFree $UiPort) -and -not $NoUi) {
    Write-Host "    Port $UiPort (web UI) is already in use." -ForegroundColor Yellow
    Fail "Free port $UiPort and run this again."
}
if ((Invoke-Native $Python @('local.py', 'check')) -ne 0) {
    Fail 'Fix what is marked FAIL above (usually a value in .env, or a server that is not running) and run this again.'
}
if ($CheckOnly) { Ok 'everything checks out (-CheckOnly: not starting)'; exit 0 }

# ---------------------------------------------------------------- 5. start ---
$ui = $null
$opener = $null
try {
    if (-not $NoUi) {
        Step 'Web UI'
        if (-not (Test-Path -LiteralPath (Join-Path $Web 'node_modules'))) {
            Write-Host '    npm install (first time) ...'
            Push-Location $Web
            try { if ((Invoke-Native 'npm.cmd' @('install')) -ne 0) { Fail 'npm install failed - the error is above.' } }
            finally { Pop-Location }
        }
        # Its own window, so its log does not mix with the services'. Closed again when this script ends.
        $ui = Start-Process -FilePath 'cmd.exe' -ArgumentList '/d', '/k', 'title Chatbot web UI && npm run dev' `
            -WorkingDirectory $Web -PassThru
        Ok "starting at http://localhost:$UiPort/ (own window)"
    }

    if (-not $NoBrowser -and -not $NoUi) {
        # Waits for main-service to answer (the first start loads the embedding model - up to a minute or two), then opens the UI.
        $opener = Start-Job -ScriptBlock {
            param($Url, $Health)
            for ($i = 0; $i -lt 150; $i++) {
                try { if ((Invoke-WebRequest -UseBasicParsing -Uri $Health -TimeoutSec 2).StatusCode -eq 200) { Start-Process $Url; return } } catch { }
                Start-Sleep -Seconds 2
            }
        } -ArgumentList "http://localhost:$UiPort/", "http://127.0.0.1:$MainPort/health"
    }

    Step 'Services (Ctrl+C stops everything)'
    Write-Host "    main-service http://127.0.0.1:$MainPort/docs   Prompts API http://127.0.0.1:$MainPort/prompts   compute REST 127.0.0.1:8001   kb-worker (celery)"
    Write-Host '    The first start loads the embedding model - give it a minute.'
    $runArgs = @('local.py', 'run')
    if ($Reload) { $runArgs += '--reload' }
    $code = Invoke-Native $Python $runArgs
    exit $code
}
finally {
    if ($opener) { Stop-Job $opener -ErrorAction SilentlyContinue; Remove-Job $opener -Force -ErrorAction SilentlyContinue }
    if ($ui -and -not $ui.HasExited) {
        # /T: the window's whole process tree (cmd -> npm -> node -> vite), or the dev server keeps running.
        & taskkill.exe /T /F /PID $ui.Id 2>&1 | Out-Null
    }
}
