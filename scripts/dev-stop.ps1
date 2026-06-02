$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
chcp 65001 | Out-Null

$ports = @(5173, 8800)
$currentPid = $PID
$targets = @{}

foreach ($port in $ports) {
  $connections = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue

  foreach ($conn in $connections) {
    $ownerPid = [int]$conn.OwningProcess
    if ($ownerPid -le 0 -or $ownerPid -eq $currentPid) {
      continue
    }

    if (-not $targets.ContainsKey($ownerPid)) {
      $targets[$ownerPid] = [System.Collections.Generic.List[int]]::new()
    }
    $targets[$ownerPid].Add($port)
  }
}

if ($targets.Count -eq 0) {
  Write-Host "No processes are listening on ports 5173 or 8800."
  exit 0
}

foreach ($entry in $targets.GetEnumerator() | Sort-Object Name) {
  $pidToStop = [int]$entry.Key
  $portList = ($entry.Value | Sort-Object -Unique) -join ","
  $proc = Get-Process -Id $pidToStop -ErrorAction SilentlyContinue

  if (-not $proc) {
    Write-Host "PID $pidToStop for port(s) $portList is already gone."
    continue
  }

  $path = $proc.Path
  if (-not $path) {
    $path = "<unknown>"
  }

  Write-Host "Stopping PID $pidToStop ($($proc.ProcessName)) on port(s) ${portList}: $path"
  Stop-Process -Id $pidToStop -Force -ErrorAction Stop
  try {
    Wait-Process -Id $pidToStop -Timeout 5 -ErrorAction SilentlyContinue
  } catch {
    # Some Windows Store Python wrapper processes do not support Wait-Process reliably.
  }
}

for ($i = 0; $i -lt 20; $i++) {
  $busy = $false
  foreach ($port in $ports) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
      $busy = $true
    }
  }
  if (-not $busy) {
    break
  }
  Start-Sleep -Milliseconds 250
}

foreach ($port in $ports) {
  $remaining = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  if ($remaining) {
    $owners = ($remaining | ForEach-Object { $_.OwningProcess } | Sort-Object -Unique) -join ","
    Write-Warning "Port $port is still occupied by PID(s): $owners"
  }
}
