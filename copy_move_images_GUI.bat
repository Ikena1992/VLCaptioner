@echo off
REM Launch the image-copy GUI.
call "%~dp0data\env\Scripts\activate.bat"
python "%~dp0data\copy_move_images_GUI.py"
IF ERRORLEVEL 1 goto Error
exit /b 0

:Error
echo Image-copy GUI failed. Exiting.
pause
exit /b 1
