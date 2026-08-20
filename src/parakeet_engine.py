"""Parakeet (NVIDIA TDT) engine via parakeet-mlx for Apple Silicon.

Fourth inference path alongside `transcribe_local` (faster-whisper),
`transcribe_api` (OpenAI HTTP API) and the mlx-whisper engine. Feeds audio
through get_logmel + generate rather than model.transcribe(path), which
would require an ffmpeg binary for file decoding we don't need — the mic
pipeline already has samples in memory.
"""
import numpy as np

from utils import ConfigManager

DEFAULT_REPO = 'mlx-community/parakeet-tdt-0.6b-v3'


def create_parakeet_model():
    """Eagerly load the Parakeet model.

    Returned object is plumbed through ResultThread and api_server as
    `local_model`, like the faster-whisper WhisperModel instance.
    """
    import mlx.core as mx
    from parakeet_mlx import from_pretrained

    options = ConfigManager.get_config_section('model_options').get('parakeet', {}) or {}
    repo = options.get('model') or DEFAULT_REPO
    ConfigManager.console_print(f'Parakeet engine: loading {repo}')
    model = from_pretrained(repo)
    # Weights load lazily; force evaluation on the loading thread. Deferring
    # it to the first transcription evaluates them on a worker QThread, where
    # MLX raises "There is no Stream(gpu, 0) in current thread".
    mx.eval(model.parameters())
    ConfigManager.console_print('Parakeet model loaded.')
    return model


_preview_model = None


def warm_preview_model(shared_model=None):
    """Load (or adopt) the Parakeet model that powers the streaming live preview.

    The live preview always streams through Parakeet regardless of which
    engine produces the final text — parakeet-mlx is the only local engine
    with true incremental streaming (transcribe_stream). When the main
    engine IS parakeet, pass its model as `shared_model` to avoid loading
    the weights twice.

    Must be called on the main thread before any preview runs: MLX raises
    "There is no Stream(gpu, 0) in current thread" if weights first
    evaluate on a worker thread (same note as create_parakeet_model).
    """
    global _preview_model
    if shared_model is not None:
        _preview_model = shared_model
    elif _preview_model is None:
        _preview_model = create_parakeet_model()
    return _preview_model


def preview_model_ready():
    return _preview_model is not None


def get_preview_model():
    return _preview_model


def _to_float32(audio_data):
    """Accept int16 or float32 numpy; return float32 in [-1, 1]."""
    if audio_data.dtype == np.int16:
        return audio_data.astype(np.float32) / 32768.0
    return audio_data.astype(np.float32)


def _resample(audio, src_rate, dst_rate):
    if src_rate == dst_rate:
        return audio
    target_length = int(len(audio) * dst_rate / src_rate)
    return np.interp(
        np.linspace(0, len(audio), target_length),
        np.arange(len(audio)),
        audio,
    ).astype(np.float32)


def tokens_to_words(tokens):
    """Merge subword AlignedTokens into word dicts {'word', 'start', 'end'}.

    parakeet-mlx tokens are SentencePiece pieces decoded to text; a piece
    that starts with a space begins a new word.
    """
    words = []
    for t in tokens:
        text = t.text
        if not words or text.startswith(' '):
            words.append({'word': text.strip(), 'start': t.start, 'end': t.end})
        else:
            words[-1]['word'] += text
            words[-1]['end'] = t.end
    return [w for w in words if w['word']]


def transcribe_parakeet_full(audio_data, model=None, sample_rate=None):
    """Transcribe and return the parakeet-mlx AlignedResult.

    Result shape: .text, .sentences[] (AlignedSentence with .text/.start/
    .end) each holding .tokens[] (AlignedToken with .text/.start/.end).

    `sample_rate` is the rate of `audio_data`; defaults to the configured
    recording rate. Parakeet v3 auto-detects language — the `common`
    options (language, initial_prompt, temperature) do not apply.
    """
    import mlx.core as mx
    from parakeet_mlx.audio import get_logmel

    if model is None:
        model = create_parakeet_model()

    if sample_rate is None:
        sample_rate = ConfigManager.get_config_section(
            'recording_options').get('sample_rate') or 16000

    audio = _to_float32(audio_data)
    audio = _resample(audio, sample_rate, model.preprocessor_config.sample_rate)

    mel = get_logmel(mx.array(audio), model.preprocessor_config)
    return model.generate(mel)[0]


def transcribe_parakeet(audio_data, model=None):
    """Plain text transcription — used by ResultThread (hotkey path)."""
    return transcribe_parakeet_full(audio_data, model=model).text
