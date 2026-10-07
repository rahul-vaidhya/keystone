# One-time setup for Keystone on Windows. Run from the repo root:
#   powershell -ExecutionPolicy Bypass -File setup.ps1
# Safe to run again: every step skips what is already done.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "`nSETUP FAILED: $msg" -ForegroundColor Red; exit 1 }

# 1. Docker Desktop
Step "Checking Docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail "Docker is not installed. Install Docker Desktop, then run this again." }
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    $candidates = @("$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe", "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe")
    $exe = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $exe) { Fail "Docker Desktop is not running. Start it, wait until it says 'running', then run this again." }
    Write-Host "Starting Docker Desktop (this can take a minute)..."
    Start-Process $exe
    for ($i = 0; $i -lt 60; $i++) { Start-Sleep 3; docker info *> $null; if ($LASTEXITCODE -eq 0) { break } }
    docker info *> $null
    if ($LASTEXITCODE -ne 0) { Fail "Docker Desktop did not start. Start it manually, then run this again." }
}

# 2. Postgres + Redis
Step "Starting Postgres and Redis"
Push-Location $root
docker compose up -d postgres redis
if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "docker compose could not start the containers (is port 55432 or 6379 already in use?)." }
$ready = $false
for ($i = 0; $i -lt 40; $i++) {
    docker compose exec -T postgres pg_isready -U veratas -d veratas *> $null
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep 2
}
Pop-Location
if (-not $ready) { Fail "Postgres did not become ready. Check 'docker compose logs postgres'." }

# 3. Python environment (recreated if it was copied from another computer)
Step "Setting up the Python environment"
$py = $null
foreach ($cand in @("py -3", "python")) {
    $exe, $arg = $cand.Split(" ")
    if (Get-Command $exe -ErrorAction SilentlyContinue) {
        $ver = & $exe $arg -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($ver -and [version]$ver -ge [version]"3.12") { $py = $cand; break }
    }
}
if (-not $py) { Fail "Python 3.12 or newer is required (python.org). Install it, then run this again." }
$venvPy = Join-Path $backend ".venv\Scripts\python.exe"
$venvOk = $false
if (Test-Path $venvPy) { & $venvPy --version *> $null; $venvOk = ($LASTEXITCODE -eq 0) }
if (-not $venvOk) {
    if (Test-Path (Join-Path $backend ".venv")) { Write-Host "Existing .venv is broken (copied from another PC?); recreating it."; Remove-Item -Recurse -Force (Join-Path $backend ".venv") }
    $exe, $arg = $py.Split(" ")
    & $exe $arg -m venv (Join-Path $backend ".venv")
    if ($LASTEXITCODE -ne 0) { Fail "Could not create the Python virtual environment." }
}
Push-Location $backend
& $venvPy -m pip install -q --upgrade pip
& $venvPy -m pip install -q -e ".[dev,real]"
if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "pip install failed (see the error above)." }

# 4. backend/.env (asks for the OpenRouter key once)
Step "Configuring backend/.env"
$envFile = Join-Path $backend ".env"
if (-not (Test-Path $envFile)) { Copy-Item (Join-Path $backend ".env.example") $envFile; Write-Host "Created backend/.env from .env.example" }
$envText = [IO.File]::ReadAllText($envFile)
if ($envText -match "(?m)^OPENAI_API_KEY=\s*$") {
    $key = Read-Host "Paste your OpenRouter API key (https://openrouter.ai/keys), or press Enter to use offline fake models"
    if ($key.Trim()) {
        $envText = $envText -replace "(?m)^OPENAI_API_KEY=.*$", "OPENAI_API_KEY=$($key.Trim())"
        $envText = $envText -replace "(?m)^OPENROUTER_API_KEY=.*$", "OPENROUTER_API_KEY=$($key.Trim())"
    } else {
        $envText = $envText -replace "(?m)^(PARSER_MODE|EMBEDDER_MODE|LLM_MODE)=.*$", '$1=fake'
        Write-Host "No key given: using fake models (the app works, but answers are placeholders)." -ForegroundColor Yellow
    }
    [IO.File]::WriteAllText($envFile, $envText)
}

# 5. Database tables
Step "Creating / updating database tables (alembic upgrade head)"
& (Join-Path $backend ".venv\Scripts\alembic.exe") upgrade head
if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "Migrations failed (see the error above). Is DATABASE_URL in backend/.env pointing at 127.0.0.1:55432?" }
Pop-Location

# 6. Frontend
Step "Installing frontend packages"
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Fail "Node.js 18+ is required (nodejs.org). Install it, then run this again." }
Push-Location $frontend
npm install --no-fund --no-audit
if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "npm install failed (see the error above)." }
Pop-Location

Write-Host "`nSetup complete. Start the app with:  powershell -ExecutionPolicy Bypass -File start.ps1" -ForegroundColor Green
