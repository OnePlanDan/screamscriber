#!/usr/bin/env bash

# Exit immediately on error
set -e

# Change to the directory where this script resides (project root)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_BIN="python3.11"

# Check that Python 3.11 is available
if ! command -v "$PYTHON_BIN" &>/dev/null; then
  echo "Error: $PYTHON_BIN not found. Please install Python 3.11 first." >&2
  exit 1
fi

# Create virtual environment if it does not exist
if [ ! -d "venv" ]; then
  echo "Creating virtual environment using $PYTHON_BIN ..."
  "$PYTHON_BIN" -m venv venv
fi

# Activate the virtual environment
source venv/bin/activate

# Upgrade pip and install dependencies
pip install --upgrade pip
pip install -r requirements.txt


# why is the \n on next line not working? Thanks f
echo -e "\nEnvironment setup complete. Activate it anytime with: source venv/bin/activate" 