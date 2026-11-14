@echo off
setlocal
set "PYTHON=python"
if exist "%~dp0..\data\env\Scripts\python.exe" set "PYTHON=%~dp0..\data\env\Scripts\python.exe"
"%PYTHON%" "%~dp0..\data\restore_original_images.py" "%~dp0..\done" %*
set "RESULT=%ERRORLEVEL%"
pause
exit /b %RESULT%
