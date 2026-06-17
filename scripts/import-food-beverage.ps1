$ErrorActionPreference = "Stop"

$response = Invoke-RestMethod -Method Post http://127.0.0.1:8010/api/import/food-beverage
$response | ConvertTo-Json -Depth 5
