#!/usr/bin/env python3
"""Headless API-only server for Screamscriber.

Loads the local Whisper model and serves the OpenAI-compatible
transcription API without any Qt/GUI/keyboard dependencies.
"""
import os
import sys
import signal
import time

# Ensure src/ is on the path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))

from dotenv import load_dotenv
load_dotenv()

from utils import ConfigManager
from transcription import create_local_model
from api_server import APIServer


def main():
    ConfigManager.initialize()

    if ConfigManager.get_config_value('model_options', 'use_api'):
        print('ERROR: use_api must be false for API server mode (need a local model)')
        sys.exit(1)

    api_config = ConfigManager.get_config_section('api_server') or {}
    if not api_config.get('enabled', False):
        print('ERROR: api_server.enabled is false in config. Enable it first.')
        sys.exit(1)

    host = api_config.get('host', '0.0.0.0')
    port = api_config.get('port', 5000)

    print(f'Loading Whisper model...')
    local_model = create_local_model()

    server = APIServer(local_model, host=host, port=port)
    server.start()

    print(f'Screamscriber API server running on http://{host}:{port}')
    print('Press Ctrl+C to stop.')

    def shutdown(signum, frame):
        print('\nShutting down...')
        server.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Keep main thread alive
    while True:
        time.sleep(1)


if __name__ == '__main__':
    main()
