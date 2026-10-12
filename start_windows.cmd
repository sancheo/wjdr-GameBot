@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Please follow WINDOWS_DEVELOPMENT.md to create .venv and install requirements.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch_desktop.ps1"
if errorlevel 1 (
    pause
    exit /b 1
)
