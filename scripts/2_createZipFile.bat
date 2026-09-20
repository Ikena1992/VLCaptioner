@echo off
pushd "%~dp0.."
REM Activate the virtual environment (batch version)
call "data\env\Scripts\activate.bat"
IF ERRORLEVEL 1 goto Error

echo Running create_dataset_zip.py...
python "data\create_dataset_zip.py"
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
