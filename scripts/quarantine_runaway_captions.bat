@echo off
call "%~dp0..\data\env\Scripts\activate.bat"
python "%~dp0..\data\quarantine_runaway_captions.py" %*
if errorlevel 1 pause
