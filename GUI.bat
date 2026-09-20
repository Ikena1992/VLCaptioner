@echo off
REM Activate the virtual environment (batch version)
pushd "%~dp0"
call "data\env\Scripts\activate.bat"
IF ERRORLEVEL 1 goto Error

echo Starting VLTagger GUI...
python "data\gui.py"
IF ERRORLEVEL 1 goto Error

echo All scripts ran successfully!
popd
pause
exit /b 0

:Error
popd
echo Script failed. Exiting.
pause
exit /b 1
