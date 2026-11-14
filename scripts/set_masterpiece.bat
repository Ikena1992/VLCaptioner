@echo off
setlocal
set "PYTHON=python"
if exist "%~dp0..\data\env\Scripts\python.exe" set "PYTHON=%~dp0..\data\env\Scripts\python.exe"
"%PYTHON%" "%~dp0..\data\set_masterpiece.py" "%~dp0..\done" --recursive %*
if errorlevel 1 (
    pause
    exit /b 1
)
endlocal
