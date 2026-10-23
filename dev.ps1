<#
.SYNOPSIS
  Stop everything, rebuild backend containers, start the stack, launch FE dev server.

.PARAMETER NoBuild
  Skip docker compose build (use existing images).
.PARAMETER NoFE
  Start containers only, skip npm run dev.
.PARAMETER Down
  Just stop everything and exit.
#>
param(
    [switch]$NoBuild,
    [switch]$NoFE,
    [switch]$Down
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$BackendServices = 'redis','mongo','mssql','main-service','parsing-indexing-service','kb-worker'

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "    OK  $text" -ForegroundColor Green }
function Warn($text) { Write-Host "    --  $text" -ForegroundColor Yellow }

# ------------------------------------------------------------------ stop FE --
function Stop-FE {
    # Kill any node process running vite on port 3006
    $pids = (Get-NetTCPConnection -LocalPort 3006 -State Listen -ErrorAction SilentlyContinue).OwningProcess | Sort-Object -Unique
    foreach ($p in $pids) {
        try {
            $proc = Get-Process -Id $p -ErrorAction SilentlyContinue
            if ($proc -and $proc.ProcessName -match 'node') {
                Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
                Warn "Killed node PID $p (was on :3006)"
            }
        } catch { }
    }
}

# --------------------------------------------------------------- stop docker --
function Stop-Containers {
    Step 'Stopping containers'
    $previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    docker compose down 2>&1 | Out-Host
    $ErrorActionPreference = $previous
    Ok 'containers stopped'
}

# ----------------------------------------------------------------- stop all --
Step 'Stopping any running FE dev server (port 3006)'
Stop-FE

Stop-Containers

if ($Down) { Write-Host ""; Ok 'Done (--Down).'; exit 0 }

# -------------------------------------------------------------------- build --
if (-not $NoBuild) {
    Step "Building backend images"
    $previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    docker compose build $BackendServices 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""; Write-Host "    BUILD FAILED" -ForegroundColor Red; exit 1
    }
    $ErrorActionPreference = $previous
    Ok 'images built'
}

# --------------------------------------------------------------- start stack --
Step "Starting backend services: $($BackendServices -join ', ')"
$previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
docker compose up -d $BackendServices 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) {
    Write-Host ""; Write-Host "    COMPOSE UP FAILED" -ForegroundColor Red; exit 1
}
$ErrorActionPreference = $previous
Ok 'backend up'

# ------------------------------------------------------------------ FE (npm) --
if ($NoFE) {
    Write-Host ""
    Ok 'Done. Backend running. Skipped FE (-NoFE).'
    Write-Host "    API  http://127.0.0.1:8000/docs" -ForegroundColor DarkGray
    exit 0
}

Step 'FE dev server'
$FE = Join-Path $Root 'FE'

if (-not (Test-Path (Join-Path $FE 'node_modules'))) {
    Warn 'node_modules missing - running npm install first...'
    Push-Location $FE
    npm install | Out-Host
    Pop-Location
}

# Open in a new window so its log stays separate and Ctrl+C here doesn't kill it.
$ui = Start-Process -FilePath 'cmd.exe' `
    -ArgumentList '/d', '/k', 'title Chatbot FE && npm run dev' `
    -WorkingDirectory $FE -PassThru

Ok "FE started in new window (PID $($ui.Id))  ->  http://localhost:3006"
Write-Host ""
Write-Host "    API      http://127.0.0.1:8000/docs" -ForegroundColor DarkGray
Write-Host "    UI       http://localhost:3006" -ForegroundColor DarkGray
Write-Host ""
Write-Host "    To stop everything later:  .\dev.ps1 -Down" -ForegroundColor DarkGray
Write-Host ""
