#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$SCRIPT_DIR/data/env/bin/activate"

trap 'echo "Script failed. Exiting."; exit 1' ERR

echo "Running create_dataset_zip.py..."
python "$SCRIPT_DIR/data/create_dataset_zip.py"

echo "All scripts ran successfully!"
read -rp "Appuyez sur Entrée pour continuer..." _
