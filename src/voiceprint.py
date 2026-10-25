"""Live speaker-embedding "voice map" and voice meter for the status window.

While a recording runs, a worker embeds the most recent few seconds of
audio every ~300 ms with a speaker-verification model (3D-Speaker CAM++
via sherpa-onnx, ~35 ms per pass on CPU) and projects the 192-dim vector
to 2D for drawing as a dot. The same voice lands in the same area; a
whisper, another speaker or music lands elsewhere.

Projection: embeddings seen during this app session (in memory only) are
kept as the reference set. Until there are enough of them a fixed random
orthonormal projection is used; after that the two principal components
of the session set are used, sign-stabilised so the axes do not flip
between passes. Coordinates are auto-scaled to the session's spread.

Voice meter: takes recorded while "Learn my voice" is on are reduced to
one averaged embedding each and saved to PROFILE_PATH, the only thing
written to disk besides the model. During a later take the average of
its voiced windows is scored against the closest enrolled takes, so the
reading firms up the longer you talk.
"""
import os
import threading
import traceback
import urllib.request

import numpy as np

from utils import ConfigManager

# Measured 2026-10-25 against the user's own takes, 20 clips of five real
# speakers mixed into this room's noise, and the room's silence: the
# WeSpeaker CAM++ export used before scored other people (up to 0.74)
# above the user's own takes (down to 0.27), so it could not tell voices
# apart. This model kept other people <= 0.20 and silence <= 0.25 while
# the user's voiced takes scored >= 0.31, at the same speed.
MODEL_URL = ('https://github.com/k2-fsa/sherpa-onnx/releases/download/'
             'speaker-recongition-models/'
             '3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx')
MODEL_DIR = os.path.join(os.path.expanduser('~'), '.cache', 'screamscriber', 'speaker')
MODEL_PATH = os.path.join(MODEL_DIR, os.path.basename(MODEL_URL))
PROFILE_PATH = os.path.join(os.path.expanduser('~'), '.cache', 'screamscriber', 'voice_profile.npz')

WINDOW_S = 2.0        # each dot summarises this much of the most recent audio
MIN_S = 0.6           # first dot appears once this much audio exists
INTERVAL_S = 0.3      # pass interval
SILENCE_RMS = 0.002   # windows quieter than this (peak frame RMS) get no dot
MIN_FOR_PCA = 8       # session embeddings needed before PCA replaces the random basis
HISTORY_CAP = 600     # most recent session embeddings kept for the projection

# Voice meter. Whispers on this mic sit at room-noise level, and enrolling
# them made silence score like the user, so only voiced speech counts: a
# 30 ms frame must pass webrtcvad and clear SPEECH_RMS (the silence gate's
# floor), and a window needs MIN_WINDOW_SPEECH_S of such frames to be used.
SPEECH_RMS = 0.005
MIN_WINDOW_SPEECH_S = 0.3
MIN_LEARN_SPEECH_S = 1.0   # a take must hold this much voiced speech to be learned
MIN_TAKES = 3              # enrolled takes needed before the meter shows a score
TOP_K = 3                  # score = mean similarity to the closest TOP_K enrolled takes
PROFILE_CAP = 200          # most recent enrolled takes kept
DEFAULT_THRESHOLD = 0.45


def enabled():
    value = ConfigManager.get_config_value('misc', 'voiceprint_dots')
    return value is not False


def threshold():
    value = ConfigManager.get_config_value('misc', 'voice_lock_threshold')
    return float(value) if value is not None else DEFAULT_THRESHOLD


def voiced_frames(audio_int16, sample_rate, vad):
    """One bool per 30 ms frame: webrtcvad hears speech and the frame
    clears SPEECH_RMS. Energy alone decides if the VAD rejects the rate."""
    frame = int(sample_rate * 0.03)
    out = []
    for i in range(0, len(audio_int16) - frame + 1, frame):
        f = audio_int16[i:i + frame]
        rms = float(np.sqrt(np.mean((f.astype(np.float32) / 32768.0) ** 2)))
        if rms < SPEECH_RMS:
            out.append(False)
            continue
        try:
            out.append(vad.is_speech(f.tobytes(), sample_rate))
        except Exception:
            out.append(True)
    return out


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
        self._profile = None   # (n_takes, dim) enrolled voice, loaded lazily
        self.learning = False  # tray "Learn my voice"; session only

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

    # -- enrolled voice ------------------------------------------------------

    def _load_profile(self):
        """Enrolled takes from disk, or an empty array. A profile made with
        a different model is ignored: its embeddings are not comparable."""
        if self._profile is None:
            takes = np.zeros((0, 0), np.float32)
            try:
                with np.load(PROFILE_PATH) as data:
                    if str(data['model']) == os.path.basename(MODEL_PATH):
                        takes = data['takes'].astype(np.float32)
            except FileNotFoundError:
                pass
            except Exception:
                traceback.print_exc()
            self._profile = takes
        return self._profile

    def enrolled_takes(self):
        return len(self._load_profile())

    def learn(self, embedding):
        """Append one take's averaged embedding to the profile on disk and
        return the number of enrolled takes."""
        with self._lock:
            takes = self._load_profile().reshape(-1, len(embedding))
            takes = np.vstack([takes, embedding[None, :]])[-PROFILE_CAP:]
            os.makedirs(os.path.dirname(PROFILE_PATH), exist_ok=True)
            tmp = PROFILE_PATH[:-len('.npz')] + '.part.npz'
            np.savez(tmp, model=os.path.basename(MODEL_PATH), takes=takes)
            os.replace(tmp, PROFILE_PATH)
            self._profile = takes
            return len(takes)

    def forget(self):
        with self._lock:
            self._profile = np.zeros((0, 0), np.float32)
            if os.path.isfile(PROFILE_PATH):
                os.remove(PROFILE_PATH)

    def match(self, embedding):
        """Mean cosine similarity of a unit embedding to its TOP_K closest
        enrolled takes (nearest takes, not one centroid, so a voice taught
        in two styles is not averaged into neither), or None until
        MIN_TAKES takes are enrolled."""
        takes = self._load_profile()
        if len(takes) < MIN_TAKES:
            return None
        sims = np.sort(takes @ embedding)
        return float(sims[-TOP_K:].mean())
