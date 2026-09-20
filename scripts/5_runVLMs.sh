#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

source "$SCRIPT_DIR/data/env/bin/activate"
python "$SCRIPT_DIR/data/pipeline_runner.py"
read -r -p "Press Enter to continue..."
