#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "----------------------------------------------------"
echo "Checking for Python 3 installation..."
echo "----------------------------------------------------"

if ! command -v python3 &>/dev/null; then
    echo "Python 3 not found! Make sure Python 3 is installed and added to PATH."
    exit 1
fi

PYMAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
if [ "$PYMAJOR" -lt 3 ]; then
    echo "Python 3 is required. Installed version is $(python3 --version)."
    exit 1
fi

echo "Python version: $(python3 --version)"

echo "----------------------------------------------------"
echo "Installing system dependency: python3-tk for GUI script..."
echo "----------------------------------------------------"

if command -v apt-get &>/dev/null; then
    sudo apt-get install -y python3-tk
elif command -v dnf &>/dev/null; then
    sudo dnf install -y python3-tkinter
elif command -v pacman &>/dev/null; then
    sudo pacman -S --noconfirm tk
elif command -v zypper &>/dev/null; then
    sudo zypper install -y python3-tk
else
    echo "Warning: Package manager not recognized."
    echo "Please install python3-tk manually if you plan to use copyImagesWithGUI.sh."
fi

echo "----------------------------------------------------"
echo "Creating Python virtual environment in data/env..."
echo "----------------------------------------------------"

mkdir -p "$SCRIPT_DIR/data"

if [ -d "$SCRIPT_DIR/data/env" ]; then
    echo "Existing data/env found. Reusing it."
else
    python3 -m venv "$SCRIPT_DIR/data/env"
fi

echo "----------------------------------------------------"
echo "Activating data/env temporarily to install packages..."
echo "----------------------------------------------------"

source "$SCRIPT_DIR/data/env/bin/activate"

echo "----------------------------------------------------"
echo "Upgrading pip..."
echo "----------------------------------------------------"

python -m pip install --upgrade pip

echo "----------------------------------------------------"
echo "Installing PyTorch with CUDA 13.0 wheel if needed..."
echo "----------------------------------------------------"

if ! python -m pip install torch==2.11.0 torchvision==0.26.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu130; then
    echo "----------------------------------------------------"
    echo "Failed to install PyTorch CUDA 13.0 wheel."
    echo "Falling back to CUDA 12.8 wheel..."
    echo "----------------------------------------------------"

    python -m pip install torch==2.11.0 torchvision==0.26.0 torchaudio==2.11.0 --index-url https://download.pytorch.org/whl/cu128
fi

echo "----------------------------------------------------"
echo "Installing model dependencies if needed..."
echo "----------------------------------------------------"

python -m pip install --requirement "$SCRIPT_DIR/requirements.txt"

echo "----------------------------------------------------"
echo "Checking CUDA / PyTorch installation..."
echo "----------------------------------------------------"

python - <<'PY'
import torch

print("torch:", torch.__version__)
print("torch cuda:", torch.version.cuda)
print("cuda available:", torch.cuda.is_available())

if torch.cuda.is_available():
    print("gpu:", torch.cuda.get_device_name(0))
    print("capability:", torch.cuda.get_device_capability(0))
else:
    print("gpu: none")
    print("capability: none")
PY

deactivate

echo "----------------------------------------------------"
echo "data/env setup complete!"
echo "----------------------------------------------------"
echo "All environments are ready!"

read -rp "Appuyez sur Entrée pour continuer..." _
