import io
import os
import numpy as np
import soundfile as sf
from openai import OpenAI

from utils import ConfigManager
from mlx_engine import create_mlx_model, transcribe_mlx
from parakeet_engine import create_parakeet_model, transcribe_parakeet


def resolve_engine():
    """Resolve which transcription engine to use.

    New configs set `model_options.engine` directly. Legacy configs only have
    `use_api`; honor that as a fallback so old configs keep working without
    migration.
    """
    model_options = ConfigManager.get_config_section('model_options') or {}
    engine = model_options.get('engine')
    if engine in {'api', 'faster-whisper', 'mlx', 'parakeet'}:
        return engine
    return 'api' if model_options.get('use_api') else 'faster-whisper'


def create_local_model():
    """
    Create a local model handle for the active engine.

    For faster-whisper this is an eager WhisperModel load.
    For mlx this is just the repo string (MLX loads lazily).
    For parakeet this is an eager parakeet-mlx model load.
    """
    engine = resolve_engine()
    if engine == 'mlx':
        return create_mlx_model()
    if engine == 'parakeet':
        return create_parakeet_model()
    if engine == 'api':
        return None

    # faster-whisper path — eager load
    from faster_whisper import WhisperModel

    ConfigManager.console_print('Creating local model...')
    local_model_options = ConfigManager.get_config_section('model_options')['local']
    compute_type = local_model_options['compute_type']
    model_path = local_model_options.get('model_path')

    if compute_type == 'int8':
        device = 'cpu'
        ConfigManager.console_print('Using int8 quantization, forcing CPU usage.')
    else:
        device = local_model_options['device']

    try:
        if model_path:
            ConfigManager.console_print(f'Loading model from: {model_path}')
            model = WhisperModel(model_path,
                                 device=device,
                                 compute_type=compute_type,
                                 download_root=None)  # Prevent automatic download
        else:
            model = WhisperModel(local_model_options['model'],
                                 device=device,
                                 compute_type=compute_type)
    except Exception as e:
        ConfigManager.console_print(f'Error initializing WhisperModel: {e}')
        ConfigManager.console_print('Falling back to CPU.')
        model = WhisperModel(model_path or local_model_options['model'],
                             device='cpu',
                             compute_type=compute_type,
                             download_root=None if model_path else None)

    ConfigManager.console_print('Local model created.')
    return model

def transcribe_local(audio_data, local_model=None):
    """
    Transcribe an audio file using a local faster-whisper model.
    """
    if not local_model:
        local_model = create_local_model()
    model_options = ConfigManager.get_config_section('model_options')

    # Convert int16 to float32
    audio_data_float = audio_data.astype(np.float32) / 32768.0

    response = local_model.transcribe(audio=audio_data_float,
                                      language=model_options['common']['language'],
                                      initial_prompt=model_options['common']['initial_prompt'],
                                      condition_on_previous_text=model_options['local']['condition_on_previous_text'],
                                      temperature=model_options['common']['temperature'],
                                      vad_filter=model_options['local']['vad_filter'],)
    return ''.join([segment.text for segment in list(response[0])])

def transcribe_api(audio_data):
    """
    Transcribe an audio file using the OpenAI API.
    """
    model_options = ConfigManager.get_config_section('model_options')
    client = OpenAI(
        api_key=os.getenv('OPENAI_API_KEY') or None,
        base_url=model_options['api']['base_url'] or 'https://api.openai.com/v1'
    )

    # Convert numpy array to WAV file
    byte_io = io.BytesIO()
    sample_rate = ConfigManager.get_config_section('recording_options').get('sample_rate') or 16000
    sf.write(byte_io, audio_data, sample_rate, format='wav')
    byte_io.seek(0)

    response = client.audio.transcriptions.create(
        model=model_options['api']['model'],
        file=('audio.wav', byte_io, 'audio/wav'),
        language=model_options['common']['language'],
        prompt=model_options['common']['initial_prompt'],
        temperature=model_options['common']['temperature'],
    )
    return response.text

def post_process_transcription(transcription):
    """
    Apply post-processing to the transcription.
    """
    transcription = transcription.strip()
    post_processing = ConfigManager.get_config_section('post_processing')
    if post_processing['remove_trailing_period'] and transcription.endswith('.'):
        transcription = transcription[:-1]
    if transcription and post_processing['add_trailing_space']:
        transcription += ' '
    if post_processing['remove_capitalization']:
        transcription = transcription.lower()

    return transcription

def transcribe(audio_data, local_model=None):
    """
    Transcribe audio using the active engine: api, faster-whisper, mlx, or parakeet.
    """
    if audio_data is None:
        return ''

    engine = resolve_engine()
    if engine == 'api':
        transcription = transcribe_api(audio_data)
    elif engine == 'mlx':
        transcription = transcribe_mlx(audio_data, repo=local_model)
    elif engine == 'parakeet':
        transcription = transcribe_parakeet(audio_data, model=local_model)
    else:
        transcription = transcribe_local(audio_data, local_model)

    return post_process_transcription(transcription)

