#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/data/env/bin/activate" ]]; then
  source "$SCRIPT_DIR/data/env/bin/activate"
fi
exec python "$SCRIPT_DIR/data/copy_move_images_GUI.py"
