$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path ".venv")) {
  Write-Host "Missing .venv. Run .\scripts\setup.ps1 first."
  exit 1
}

& ".\.venv\Scripts\Activate.ps1"

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
}

uvicorn app.main:app --host 127.0.0.1 --port 3030
