@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Please follow WINDOWS_DEVELOPMENT.md to create .venv and install requirements.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0gui.py"
