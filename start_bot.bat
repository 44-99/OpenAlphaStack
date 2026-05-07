@echo off
chcp 65001 >nul
set PYTHONDONTWRITEBYTECODE=1
set PYTHONUNBUFFERED=1
set PYTHONUTF8=1
cd /d "E:\Project\AlphaClaude"
python -X utf8 -B main.py >> "E:\Project\AlphaClaude\data\logs\server.log" 2>&1
