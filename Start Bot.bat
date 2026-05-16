@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_EXE=pythonw"
if exist ".venv\Scripts\pythonw.exe" (
    set "PYTHON_EXE=.venv\Scripts\pythonw.exe"
)

start "" /b "%PYTHON_EXE%" "%~dp0bot.py"

endlocal
