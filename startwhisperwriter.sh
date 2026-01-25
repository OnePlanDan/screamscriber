#!/usr/bin/env bash

# Exit immediately on error
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate virtual environment
if [ ! -f "venv/bin/activate" ]; then
  echo "Virtual environment not found. Run ./setup.sh first." >&2
  exit 1
fi
source venv/bin/activate

# Start the application
exec python3.11 run.py "$@" 