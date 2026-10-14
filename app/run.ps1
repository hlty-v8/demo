<#
    Starts the API and the frontend together, for a quick look at the PDP.

    It assumes the setup steps in README.md are done:
      * backend dependencies installed   (python -m venv .venv; pip install -r requirements-dev.txt)
      * frontend dependencies installed  (pnpm install, inside app/frontend)

    The backend runs on app/backend/.venv when that virtualenv exists, otherwise
    on whatever `python` is on PATH.

    The frontend dev server proxies /api to http://127.0.0.1:8000, so both
    processes are needed for the page to work. Ctrl+C stops the frontend; the API
    process started here is stopped on the way out.

    Two terminals are still the clearer way to develop; this script exists so a
    reviewer can start everything with one command.
#>
[CmdletBinding()]
param(
    [int]$ApiPort = 8000,
    [switch]$SkipChecks
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path $here "backend"
$frontend = Join-Path $here "frontend"

$backendPython = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path $backendPython)) {
    $backendPython = "python"
}

if (-not $SkipChecks) {
    foreach ($command in @($backendPython, "pnpm")) {
        if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
            throw "'$command' was not found. See README.md for the setup steps."
        }
    }
    if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
        Write-Warning "app/frontend/node_modules is missing - run 'pnpm install' there first."
    }
}

Write-Host "Starting the API on http://127.0.0.1:$ApiPort (using $backendPython) ..."
# Working directory app/backend - the same way the test suite and CI start it,
# so the command here is one that is actually exercised.
$api = Start-Process -PassThru -FilePath $backendPython `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--port", "$ApiPort") `
    -WorkingDirectory $backend

try {
    Write-Host "Starting the frontend (Vite) - open http://localhost:5173 ..."
    Push-Location $frontend
    pnpm dev
}
finally {
    Pop-Location
    if ($api -and -not $api.HasExited) {
        Write-Host "Stopping the API (pid $($api.Id)) ..."
        Stop-Process -Id $api.Id -Force
    }
}
