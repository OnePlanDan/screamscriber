"""
OpenAI-compatible HTTP API server for WhisperWriter.

Implements POST /v1/audio/transcriptions to allow external tools to use
the local Whisper model by setting base_url to http://localhost:5000/v1
"""

import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import soundfile as sf

from utils import ConfigManager


class TranscriptionHandler(BaseHTTPRequestHandler):
    """HTTP request handler for OpenAI-compatible transcription API."""

    def __init__(self, *args, local_model=None, **kwargs):
        self.local_model = local_model
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        """Override to use ConfigManager's console print."""
        ConfigManager.console_print(f"API: {args[0]}")

    def send_json_response(self, data, status=200):
        """Send a JSON response."""
        response = json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(response))
        self.end_headers()
        self.wfile.write(response)

    def send_error_response(self, message, status=400):
        """Send an error response in OpenAI's format."""
        self.send_json_response({
            'error': {
                'message': message,
                'type': 'invalid_request_error',
                'code': None
            }
        }, status)

    def do_GET(self):
        """Handle GET requests."""
        if self.path == '/v1/models' or self.path == '/v1/models/':
            self.handle_list_models()
        else:
            self.send_error_response('Not found', 404)

    def do_POST(self):
        """Handle POST requests."""
        if self.path == '/v1/audio/transcriptions' or self.path == '/v1/audio/transcriptions/':
            self.handle_transcription()
        else:
            self.send_error_response('Not found', 404)

    def handle_list_models(self):
        """Handle GET /v1/models - list available models."""
        local_options = ConfigManager.get_config_section('model_options').get('local', {})
        model_name = local_options.get('model', 'whisper-local')

        self.send_json_response({
            'object': 'list',
            'data': [{
                'id': model_name,
                'object': 'model',
                'owned_by': 'local'
            }]
        })

    def handle_transcription(self):
        """Handle POST /v1/audio/transcriptions - transcribe audio."""
        if not self.local_model:
            self.send_error_response('Local model not available', 503)
            return

        content_type = self.headers.get('Content-Type', '')
        if 'multipart/form-data' not in content_type:
            self.send_error_response('Content-Type must be multipart/form-data')
            return

        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)

            form_data = self.parse_multipart(body, content_type)

            if 'file' not in form_data:
                self.send_error_response('Missing required field: file')
                return

            audio_bytes = form_data['file']
            language = form_data.get('language')
            prompt = form_data.get('prompt')
            temperature = form_data.get('temperature')

            if temperature is not None:
                try:
                    temperature = float(temperature)
                except ValueError:
                    temperature = None

            audio_data, sample_rate = sf.read(io.BytesIO(audio_bytes))

            if len(audio_data.shape) > 1:
                audio_data = audio_data.mean(axis=1)

            # Resample to 16kHz if needed (Whisper expects 16kHz)
            if sample_rate != 16000:
                duration = len(audio_data) / sample_rate
                target_length = int(duration * 16000)
                audio_data = np.interp(
                    np.linspace(0, len(audio_data), target_length),
                    np.arange(len(audio_data)),
                    audio_data
                )

            audio_data = audio_data.astype(np.float32)

            local_options = ConfigManager.get_config_section('model_options').get('local', {})

            transcribe_kwargs = {
                'audio': audio_data,
                'language': language,
                'initial_prompt': prompt,
                'condition_on_previous_text': local_options.get('condition_on_previous_text', True),
                'vad_filter': local_options.get('vad_filter', False),
            }

            if temperature is not None:
                transcribe_kwargs['temperature'] = temperature

            duration = len(audio_data) / 16000
            ConfigManager.console_print(f'API: Received audio. Duration: {duration:.2f} seconds')
            ConfigManager.console_print('Transcribing...')

            start_time = time.time()
            segments, info = self.local_model.transcribe(**transcribe_kwargs)
            text = ''.join([segment.text for segment in segments])
            elapsed = time.time() - start_time

            ConfigManager.console_print(f'Transcription completed in {elapsed:.2f} seconds. Result: {text.strip()}')

            self.send_json_response({'text': text.strip()})

        except Exception as e:
            ConfigManager.console_print(f'API transcription error: {e}')
            self.send_error_response(f'Transcription failed: {str(e)}', 500)

    def parse_multipart(self, body, content_type):
        """Parse multipart/form-data manually (no cgi module needed)."""
        boundary = None
        for part in content_type.split(';'):
            part = part.strip()
            if part.startswith('boundary='):
                boundary = part[9:].strip('"')
                break

        if not boundary:
            raise ValueError('No boundary found in Content-Type')

        boundary_bytes = ('--' + boundary).encode()
        end_boundary = ('--' + boundary + '--').encode()

        parts = body.split(boundary_bytes)
        result = {}

        for part in parts:
            if not part or part == b'--\r\n' or part.strip() == b'--':
                continue

            if part.startswith(b'\r\n'):
                part = part[2:]
            if part.endswith(b'\r\n'):
                part = part[:-2]

            if b'\r\n\r\n' not in part:
                continue

            header_section, content = part.split(b'\r\n\r\n', 1)
            headers = header_section.decode('utf-8', errors='replace')

            name = None
            is_file = False

            for line in headers.split('\r\n'):
                if line.lower().startswith('content-disposition:'):
                    for item in line.split(';'):
                        item = item.strip()
                        if item.startswith('name='):
                            name = item[5:].strip('"')
                        if item.startswith('filename='):
                            is_file = True

            if name:
                if is_file:
                    result[name] = content
                else:
                    result[name] = content.decode('utf-8', errors='replace').strip()

        return result


class APIServer:
    """Manages the HTTP API server lifecycle."""

    def __init__(self, local_model, host='127.0.0.1', port=5000):
        self.local_model = local_model
        self.host = host
        self.port = port
        self.server = None
        self.thread = None

    def start(self):
        """Start the API server in a daemon thread."""
        if self.server:
            return

        def handler(*args, **kwargs):
            return TranscriptionHandler(*args, local_model=self.local_model, **kwargs)

        try:
            self.server = ThreadingHTTPServer((self.host, self.port), handler)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            ConfigManager.console_print(f'API server started at http://{self.host}:{self.port}')
        except OSError as e:
            ConfigManager.console_print(f'Failed to start API server: {e}')
            self.server = None

    def stop(self):
        """Stop the API server."""
        if self.server:
            self.server.shutdown()
            self.server = None
            self.thread = None
            ConfigManager.console_print('API server stopped')
