@echo off
call "%~dp0..\data\env\Scripts\activate.bat"
python "%~dp0..\data\check_caption_quality.py" %*
if errorlevel 1 pause
