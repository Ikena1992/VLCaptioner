@echo off
if not exist "%~dp0data\env\Scripts\pythonw.exe" (
    echo Python environment not found. Run the installer first.
    pause
    exit /b 1
)
start "" "%~dp0data\env\Scripts\pythonw.exe" "%~dp0GUI.pyw"
exit /b 0
