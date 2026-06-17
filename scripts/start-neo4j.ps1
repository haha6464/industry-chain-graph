$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")
docker compose up -d neo4j

Write-Host "Neo4j is starting."
Write-Host "Browser: http://localhost:7474"
Write-Host "User: neo4j"
Write-Host "Password: password123"

