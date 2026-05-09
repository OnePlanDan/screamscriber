#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v uv &>/dev/null; then
  echo "Error: 'uv' not found. Install it with: brew install uv" >&2
  exit 1
fi

# Pin and install Python 3.11 via uv (managed, isolated from system Python)
uv python install 3.11
uv python pin 3.11

# Pick extras based on platform
EXTRAS=()
if [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
  EXTRAS+=(--extra mac)
fi
EXTRAS+=(--extra faster-whisper)

echo "Resolving and installing dependencies..."
uv sync "${EXTRAS[@]}"

cat <<'BANNER'

═══════════════════════════════════════════════════════════════════
 Setup complete.

 Run with:   uv run python run.py

 macOS first-run permissions (grant to YOUR TERMINAL APP, not to
 "screamscriber" — pynput runs inside the Python process and TCC
 attributes the request to the parent process):

   1. System Settings → Privacy & Security → Microphone
        (auto-prompts on first audio capture)
   2. System Settings → Privacy & Security → Input Monitoring
        (manual: add Terminal/iTerm/Ghostty, then quit & relaunch it)
   3. System Settings → Privacy & Security → Accessibility
        (manual: same procedure)

 Default hotkey: Right Option (hold-to-talk).
 If Right Option injects garbled characters, edit src/config.yaml
 and change activation_key to F13.
═══════════════════════════════════════════════════════════════════
BANNER
