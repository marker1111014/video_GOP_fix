@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY_EXE=python"
if exist "C:\Users\Mark\miniconda3\python.exe" (
    set "PY_EXE=C:\Users\Mark\miniconda3\python.exe"
)

"%PY_EXE%" "%~dp0check_videos.py"
pause
