@echo off
pushd "%~dp0.."
call "data\env\Scripts\activate.bat"
IF ERRORLEVEL 1 goto Error
python "data\move_parquet_images.py"
IF ERRORLEVEL 1 goto Error
popd
pause
exit /b 0

:Error
popd
echo Script failed. Exiting.
pause
exit /b 1
