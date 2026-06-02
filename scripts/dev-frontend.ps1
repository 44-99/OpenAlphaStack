$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
chcp 65001 | Out-Null

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

$healthUrl = "http://127.0.0.1:8800/health"
$maxAttempts = 60

for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
  try {
    $response = Invoke-WebRequest -UseBasicParsing $healthUrl -TimeoutSec 2
    if ($response.StatusCode -eq 200) {
      Write-Host "Backend is ready: $healthUrl"
      break
    }
  } catch {
    if ($attempt -eq 1) {
      Write-Host "Waiting for backend: $healthUrl"
    }
  }

  if ($attempt -eq $maxAttempts) {
    Write-Error "Backend did not become ready after $maxAttempts seconds: $healthUrl"
    exit 1
  }

  Start-Sleep -Seconds 1
}

$vite = Join-Path $root "node_modules\.bin\vite.cmd"
if (-not (Test-Path $vite)) {
  Write-Error "Vite executable not found. Run npm install first."
  exit 1
}

& $vite --config dashboard/vite.config.ts
