# One-command local demo for Windows PowerShell.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

uv sync
if (-not (Test-Path .env)) {
  Copy-Item .env.example .env
}
$env:SAMPLE_MODE = "true"
uv run python -m recall ingest --sample
Push-Location web
if (Test-Path package-lock.json) { npm ci } else { npm install }
npm run build
Pop-Location
uv run python -m recall serve
