"""MLX-Whisper engine for native Apple Silicon GPU + Neural Engine inference.

Provides a third inference path alongside `transcribe_local` (faster-whisper /
CTranslate2, CPU-only on Mac) and `transcribe_api` (OpenAI HTTP API).
"""
import numpy as np

from utils import ConfigManager


def create_mlx_model():
    """MLX has no eager-load step; the repo string is the 'model handle'.

    Returned value is plumbed through ResultThread and api_server as
    `local_model` so the existing call sites keep working unchanged.
    """
    mlx_options = ConfigManager.get_config_section('model_options').get('mlx', {}) or {}
    repo = mlx_options.get('model') or 'mlx-community/whisper-large-v3-turbo'
    ConfigManager.console_print(f'MLX engine: using {repo}')
    return repo


def _to_float32(audio_data):
    """Accept int16 or float32 numpy; return float32 in [-1, 1]."""
    if audio_data.dtype == np.int16:
        return audio_data.astype(np.float32) / 32768.0
    return audio_data.astype(np.float32)


def transcribe_mlx_full(audio_data, repo=None, word_timestamps=False):
    """Transcribe and return the full mlx-whisper result dict.

    Result shape (per OpenAI whisper):
        {'text': str, 'language': str, 'segments': [{'id', 'start', 'end',
        'text', 'no_speech_prob', 'words': [...]}, ...]}
    """
    import mlx_whisper

    common = ConfigManager.get_config_section('model_options').get('common') or {}
    if repo is None:
        repo = create_mlx_model()

    kwargs = {
        'audio': _to_float32(audio_data),
        'path_or_hf_repo': repo,
    }
    if common.get('language'):
        kwargs['language'] = common['language']
    if common.get('initial_prompt'):
        kwargs['initial_prompt'] = common['initial_prompt']
    if common.get('temperature') is not None:
        kwargs['temperature'] = common['temperature']
    if word_timestamps:
        kwargs['word_timestamps'] = True

    return mlx_whisper.transcribe(**kwargs)


def transcribe_mlx(audio_data, repo=None):
    """Plain text transcription — used by ResultThread (hotkey path)."""
    return transcribe_mlx_full(audio_data, repo=repo)['text']
