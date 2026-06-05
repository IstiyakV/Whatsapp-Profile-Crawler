$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".venv")) {
  py -m venv .venv
}

& ".\.venv\Scripts\Activate.ps1"

python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
}

New-Item -ItemType Directory -Force -Path "data", "sessions", "images", "images\debug", "images\profiles", "logs", "uploads" | Out-Null

Write-Host "Setup complete. Run .\scripts\run-dev.ps1 and open http://localhost:3030"
