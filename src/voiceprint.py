"""Live speaker-embedding "voice map" for the status window.

While a recording runs, a worker embeds the most recent few seconds of
audio every ~300 ms with a speaker-verification model (WeSpeaker CAM++
via sherpa-onnx, ~30 ms per pass on CPU) and projects the 512-dim vector
to 2D for drawing as a dot. The same voice lands in the same area; a
whisper, another speaker or music lands elsewhere. Nothing is written to
disk except the one-time model download.

Projection: embeddings seen during this app session (in memory only) are
kept as the reference set. Until there are enough of them a fixed random
orthonormal projection is used; after that the two principal components
of the session set are used, sign-stabilised so the axes do not flip
between passes. Coordinates are auto-scaled to the session's spread.
"""
import os
import threading
import traceback
import urllib.request

import numpy as np

from utils import ConfigManager

MODEL_URL = ('https://github.com/k2-fsa/sherpa-onnx/releases/download/'
             'speaker-recongition-models/wespeaker_en_voxceleb_CAM++.onnx')
MODEL_DIR = os.path.join(os.path.expanduser('~'), '.cache', 'screamscriber', 'speaker')
MODEL_PATH = os.path.join(MODEL_DIR, os.path.basename(MODEL_URL))

WINDOW_S = 2.0        # each dot summarises this much of the most recent audio
MIN_S = 0.6           # first dot appears once this much audio exists
INTERVAL_S = 0.3      # pass interval
SILENCE_RMS = 0.002   # windows quieter than this (peak frame RMS) get no dot
MIN_FOR_PCA = 8       # session embeddings needed before PCA replaces the random basis
HISTORY_CAP = 600     # most recent session embeddings kept for the projection


def enabled():
    value = ConfigManager.get_config_value('misc', 'voiceprint_dots')
    return value is not False


class Voiceprint:
    _instance = None
    _instance_lock = threading.Lock()

    @classmethod
    def get(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self):
        self._extractor = None
        self._failed = False
        self._lock = threading.Lock()
        self._history = []
        self._axes = None
        self._random_axes = None

    # -- model -----------------------------------------------------------

    def available(self):
        """Load the model on first use (downloading it if missing)."""
        if self._extractor is not None:
            return True
        if self._failed:
            return False
        try:
            import sherpa_onnx
        except ImportError:
            self._failed = True
            ConfigManager.console_print('Voice map off: sherpa-onnx not installed '
                                        '(uv sync --extra voiceprint).')
            return False
        try:
            if not os.path.isfile(MODEL_PATH):
                os.makedirs(MODEL_DIR, exist_ok=True)
                ConfigManager.console_print(f'Voice map: downloading model to {MODEL_PATH}')
                tmp = MODEL_PATH + '.part'
                urllib.request.urlretrieve(MODEL_URL, tmp)
                os.replace(tmp, MODEL_PATH)
            cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=MODEL_PATH, num_threads=2, provider='cpu')
            self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
            ConfigManager.console_print(f'Voice map: model ready (dim {self._extractor.dim}).')
            return True
        except Exception:
            traceback.print_exc()
            self._failed = True
            return False

    def embed(self, audio_int16, sample_rate):
        """Unit-norm speaker embedding of an int16 buffer, or None if the
        buffer is effectively silent."""
        if len(audio_int16) == 0:
            return None
        samples = audio_int16.astype(np.float32) / 32768.0
        frame = max(1, int(sample_rate * 0.03))
        n_frames = len(samples) // frame
        if n_frames:
            frames = samples[:n_frames * frame].reshape(n_frames, frame)
            if float(np.sqrt(np.mean(frames ** 2, axis=1)).max()) < SILENCE_RMS:
                return None
        stream = self._extractor.create_stream()
        stream.accept_waveform(sample_rate, samples)
        stream.input_finished()
        vec = np.asarray(self._extractor.compute(stream), dtype=np.float32)
        norm = float(np.linalg.norm(vec))
        if not np.isfinite(norm) or norm == 0.0:
            return None
        return vec / norm

    # -- projection --------------------------------------------------------

    def observe(self, embedding):
        """Add an embedding to the session reference set."""
        with self._lock:
            self._history.append(embedding)
            if len(self._history) > HISTORY_CAP:
                del self._history[:len(self._history) - HISTORY_CAP]

    def project(self, embeddings):
        """Map a list of embeddings to (x, y) pairs in [-1, 1]."""
        if not embeddings:
            return []
        with self._lock:
            history = np.array(self._history, dtype=np.float32)
        dim = len(embeddings[0])
        if len(history) >= MIN_FOR_PCA:
            mean = history.mean(axis=0)
            _, _, vt = np.linalg.svd(history - mean, full_matrices=False)
            axes = vt[:2].T.copy()
            if self._axes is not None:
                for i in range(2):
                    if float(axes[:, i] @ self._axes[:, i]) < 0:
                        axes[:, i] *= -1
            self._axes = axes
        else:
            if self._random_axes is None:
                rng = np.random.default_rng(7)
                q, _ = np.linalg.qr(rng.standard_normal((dim, 2)).astype(np.float32))
                self._random_axes = q
            axes = self._random_axes
            mean = history.mean(axis=0) if len(history) else np.zeros(dim, np.float32)

        ref = (history - mean) @ axes if len(history) else np.zeros((1, 2), np.float32)
        scale = max(float(np.abs(ref).max()), 0.05)
        pts = (np.array(embeddings, dtype=np.float32) - mean) @ axes / scale * 0.9
        pts = np.clip(pts, -1.0, 1.0)
        return [(float(x), float(y)) for x, y in pts]
