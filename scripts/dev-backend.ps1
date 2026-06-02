$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
chcp 65001 | Out-Null

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root

$env:PYTHONPATH = "src"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONLEGACYWINDOWSSTDIO = "0"
$env:LC_ALL = "C.UTF-8"
$env:LANG = "C.UTF-8"

python -m uvicorn alphaclaude.app.main:app `
  --host 0.0.0.0 `
  --port 8800 `
  --reload `
  --reload-dir src `
  --reload-dir . `
  --reload-exclude node_modules `
  --reload-exclude dashboard/dist `
  --reload-exclude data `
  --reload-exclude .git `
  --reload-exclude .venv `
  --timeout-graceful-shutdown 3
