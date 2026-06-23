param(
  [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
  [string[]]$AgentArgs,
  [string]$EnvName = "industry-chain-graph"
)

$ErrorActionPreference = "Stop"

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $projectRoot

if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
  throw "conda was not found on PATH. Please open an Anaconda Prompt or add conda to PATH."
}

if (-not $AgentArgs -or $AgentArgs.Count -eq 0) {
  Write-Host "Usage: .\scripts\run-agent.ps1 tools\agent\validators\graph_validator.py --industry-id food_beverage"
  exit 1
}

conda run --no-capture-output -n $EnvName python @AgentArgs
