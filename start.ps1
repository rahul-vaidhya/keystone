# Starts Keystone (run setup.ps1 once first). From the repo root:
#   powershell -ExecutionPolicy Bypass -File start.ps1
# Opens three windows (API, worker, frontend); close them to stop the app.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"

if (-not (Test-Path (Join-Path $backend ".venv\Scripts\python.exe"))) { Write-Host "Run setup.ps1 first." -ForegroundColor Red; exit 1 }

docker info *> $null
if ($LASTEXITCODE -ne 0) { Write-Host "Docker Desktop is not running. Start it, then run this again." -ForegroundColor Red; exit 1 }
Push-Location $root; docker compose up -d postgres redis; Pop-Location

Start-Process powershell -WorkingDirectory $backend -ArgumentList "-NoExit", "-Command", "`$host.UI.RawUI.WindowTitle='Keystone API'; .\.venv\Scripts\uvicorn.exe main:app --host 127.0.0.1 --port 8010"
Start-Process powershell -WorkingDirectory $backend -ArgumentList "-NoExit", "-Command", "`$host.UI.RawUI.WindowTitle='Keystone worker'; .\.venv\Scripts\arq.exe worker.WorkerSettings"
Start-Process powershell -WorkingDirectory $frontend -ArgumentList "-NoExit", "-Command", "`$host.UI.RawUI.WindowTitle='Keystone frontend'; npm run dev"

Write-Host "Waiting for the API..."
$up = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep 2
    try { Invoke-RestMethod http://127.0.0.1:8010/health -TimeoutSec 2 | Out-Null; $up = $true; break } catch {}
}
if (-not $up) { Write-Host "The API did not start. Read the message in the 'Keystone API' window (it says what to fix)." -ForegroundColor Red; exit 1 }
Start-Sleep 3
Start-Process "http://localhost:5173"
Write-Host "Keystone is running at http://localhost:5173" -ForegroundColor Green
