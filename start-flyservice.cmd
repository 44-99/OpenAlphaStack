@echo off
setlocal

set "PROJECT_DIR=%~dp0"
set "VENV_PYTHON=%PROJECT_DIR%.venv\Scripts\python.exe"
set "LOG_DIR=%PROJECT_DIR%data\logs"

if not exist "%VENV_PYTHON%" (
    echo [%date% %time%] ERROR: venv Python not found at %VENV_PYTHON% >> "%LOG_DIR%\service_error.log"
    exit /b 1
)

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo [%date% %time%] Starting AlphaClaude Feishu service... >> "%LOG_DIR%\service.log"

"%VENV_PYTHON%" -m uvicorn alphaclaude.app.main:app --host 0.0.0.0 --port 8800 --log-level info >> "%LOG_DIR%\service.log" 2>&1
