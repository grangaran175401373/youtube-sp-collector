@echo off
chcp 65001 > nul
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Python仮想環境 .venv が見つかりません。
    exit /b 1
)

".venv\Scripts\python.exe" -m src.main
set EXIT_CODE=%ERRORLEVEL%

exit /b %EXIT_CODE%
