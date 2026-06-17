$ErrorActionPreference = "Stop"

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$frontend = Join-Path $projectRoot "frontend"
Set-Location $frontend

if (-not (Test-Path "node_modules")) {
  npm install
}

npm run dev

