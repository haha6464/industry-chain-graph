param(
  [string]$EnvName = "industry-chain-graph"
)

$ErrorActionPreference = "Stop"

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$backend = Join-Path $projectRoot "backend"
$setupScript = Join-Path $PSScriptRoot "setup-conda.ps1"
Set-Location $backend

if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
  throw "conda was not found on PATH. Please open an Anaconda Prompt or add conda to PATH."
}

$envList = conda env list
$envExists = $envList | Select-String -Pattern "^$EnvName\s" -Quiet
if (-not $envExists) {
  Write-Host "Conda environment '$EnvName' was not found. Creating it now..."
  & $setupScript -EnvName $EnvName
}

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
}

conda run --no-capture-output -n $EnvName python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8010
