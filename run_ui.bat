@echo off
chcp 65001 > nul
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\streamlit.exe" (
    echo [ERROR] Streamlit がインストールされていません。
    echo .\.venv\Scripts\pip install -r requirements.txt を実行してください。
    pause
    exit /b 1
)

echo YouTube 自動収集コントロールパネルを起動します...
".venv\Scripts\streamlit.exe" run app.py --server.port 8501
