param(
  [string]$EnvName = "industry-chain-graph"
)

$ErrorActionPreference = "Stop"

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$environmentFile = Join-Path $projectRoot "environment.yml"

if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
  throw "conda was not found on PATH. Please open an Anaconda Prompt or add conda to PATH."
}

if (-not (Test-Path $environmentFile)) {
  throw "environment.yml not found: $environmentFile"
}

$envList = conda env list
$envExists = $envList | Select-String -Pattern "^$EnvName\s" -Quiet

if ($envExists) {
  Write-Host "Updating conda environment '$EnvName' from environment.yml..."
  conda env update -n $EnvName -f $environmentFile --prune
} else {
  Write-Host "Creating conda environment '$EnvName' from environment.yml..."
  conda env create -n $EnvName -f $environmentFile
}

Write-Host "Conda environment '$EnvName' is ready."
Write-Host "Use: conda activate $EnvName"
