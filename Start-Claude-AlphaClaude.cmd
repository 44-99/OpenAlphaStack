@echo off
setlocal

mode con: cols=250 lines=75

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8765/health' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -ne 200) { throw 'proxy health check failed' } } catch { Start-Process powershell -WindowStyle Minimized -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-Command','Set-Location ''E:\Project\AlphaClaude''; uv run python anthropic_proxy.py' }"

set ANTHROPIC_BASE_URL=http://localhost:8765
claude --resume
