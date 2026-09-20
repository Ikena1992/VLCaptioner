@echo off
:: Prevent immediate closure when double-clicked
if not defined IS_BATCH (
    set IS_BATCH=1
    cmd /c "%~f0"
    exit /b
)

echo ----------------------------------------------------
echo Checking for Python 3 installation...
echo ----------------------------------------------------

python --version >nul 2>&1
if errorlevel 1 (
    echo Python not found! Make sure Python 3 is installed and added to PATH.
    pause
    exit /b 1
)

:: Check Python version
for /f "tokens=2 delims= " %%a in ('python --version') do set pyver=%%a
for /f "tokens=1 delims=." %%b in ("%pyver%") do set majorver=%%b
for /f "tokens=2 delims=." %%c in ("%pyver%") do set minorver=%%c

if %majorver% lss 3 (
    echo Python 3 is required. Installed version is %pyver%.
    pause
    exit /b 1
)

echo Python version: %pyver%

echo ----------------------------------------------------
echo Creating Python virtual environment in data\env...
echo ----------------------------------------------------

if not exist data (
    mkdir data
)

if exist data\env (
    echo Existing data\env found. Reusing it.
) else (
    python -m venv data\env
    if errorlevel 1 (
        echo Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo ----------------------------------------------------
echo Activating data\env...
echo ----------------------------------------------------

call data\env\Scripts\activate.bat

if errorlevel 1 (
    echo Failed to activate virtual environment.
    pause
    exit /b 1
)

echo ----------------------------------------------------
echo Upgrading pip...
echo ----------------------------------------------------

python -m pip install --upgrade pip

if errorlevel 1 (
    echo Failed to upgrade pip.
    pause
    exit /b 1
)

echo ----------------------------------------------------
echo Installing PyTorch with CUDA 13.0 wheel if needed...
echo ----------------------------------------------------

python -m pip install torch==2.11.0 torchvision==0.26.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu130

if errorlevel 1 (
    echo ----------------------------------------------------
    echo Failed to install PyTorch CUDA 13.0 wheel.
    echo Falling back to CUDA 12.8 wheel...
    echo ----------------------------------------------------

    python -m pip install torch==2.11.0 torchvision==0.26.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu128

    if errorlevel 1 (
        echo PyTorch install failed for both cu130 and cu128.
        call data\env\Scripts\deactivate.bat
        pause
        exit /b 1
    )
)

echo ----------------------------------------------------
echo Installing model dependencies if needed...
echo ----------------------------------------------------

python -m pip install --requirement "%~dp0requirements.txt"

if errorlevel 1 (
    echo Failed to install model dependencies.
    call data\env\Scripts\deactivate.bat
    pause
    exit /b 1
)

echo ----------------------------------------------------
echo Checking CUDA / PyTorch installation...
echo ----------------------------------------------------

python -c "import torch, importlib.metadata as m; print('torch:', m.version('torch')); print('torch cuda:', torch.version.cuda); print('cuda available:', torch.cuda.is_available()); print('gpu:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none'); print('capability:', torch.cuda.get_device_capability(0) if torch.cuda.is_available() else 'none')"

if errorlevel 1 (
    echo PyTorch CUDA check failed.
    call data\env\Scripts\deactivate.bat
    pause
    exit /b 1
)

echo ----------------------------------------------------
echo data\env setup complete!
echo ----------------------------------------------------

call data\env\Scripts\deactivate.bat

echo All environments are ready!
pause
exit /b 0
