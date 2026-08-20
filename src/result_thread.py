import time
import traceback
import numpy as np
import sounddevice as sd
import tempfile
import wave
import webrtcvad
from PyQt5.QtCore import QThread, QMutex, pyqtSignal
from collections import deque
from threading import Event, Thread

from transcription import transcribe, resolve_engine
from utils import ConfigManager

# Live preview modes (misc.live_preview): 'batch' re-transcribes the whole
# buffer with the active engine on an interval — preview matches the model
# that types the final text, but the periodic GPU spikes can make laptop
# power circuitry coil-whine audibly. 'streaming' feeds new samples straight
# into parakeet-mlx transcribe_stream as they arrive — near-continuous light
# GPU load with no rhythm, but the preview model is always Parakeet.
PARTIAL_MIN_S = 0.3   # batch: don't transcribe until this much audio exists


def live_preview_mode():
    """Resolve misc.live_preview to 'off' | 'batch' | 'streaming'.

    Accepts legacy booleans from old configs: true was the original
    batch behavior, false was off.
    """
    value = ConfigManager.get_config_value('misc', 'live_preview')
    if value is True:
        return 'batch'
    if value in ('off', 'batch', 'streaming'):
        return value
    return 'off'


class ResultThread(QThread):
    """
    A thread class for handling audio recording, transcription, and result processing.

    This class manages the entire process of:
    1. Recording audio from the microphone
    2. Detecting speech and silence
    3. Saving the recorded audio as numpy array
    4. Transcribing the audio
    5. Emitting the transcription result

    Signals:
        statusSignal: Emits the current status of the thread (e.g., 'recording', 'transcribing', 'idle')
        resultSignal: Emits the transcription result
    """

    statusSignal = pyqtSignal(str)
    resultSignal = pyqtSignal(str)
    audioLevelSignal = pyqtSignal(list)
    partialResultSignal = pyqtSignal(str)

    def __init__(self, local_model=None):
        """
        Initialize the ResultThread.

        :param local_model: Local transcription model (if applicable)
        """
        super().__init__()
        self.local_model = local_model
        self.is_recording = False
        self.is_running = True
        self.sample_rate = None
        self.mutex = QMutex()
        self._partial_stop = Event()

    def stop_recording(self):
        """Stop the current recording session."""
        self.mutex.lock()
        self.is_recording = False
        self.mutex.unlock()

    def stop(self):
        """Stop the entire thread execution."""
        self.mutex.lock()
        self.is_running = False
        self.mutex.unlock()
        self.statusSignal.emit('idle')
        self.wait()

    def run(self):
        """Main execution method for the thread."""
        try:
            if not self.is_running:
                return

            self.mutex.lock()
            self.is_recording = True
            self.mutex.unlock()

            self.statusSignal.emit('recording')
            ConfigManager.console_print('Recording...')
            audio_data = self._record_audio()

            if not self.is_running:
                return

            if audio_data is None:
                self.statusSignal.emit('idle')
                return

            # Whisper hallucinates on silence ("Thank you.", "you", ...), so
            # refuse to transcribe recordings that contain no actual speech.
            if not self._has_speech(audio_data):
                ConfigManager.console_print('Discarded: no speech detected in recording.')
                self.statusSignal.emit('idle')
                return

            self.statusSignal.emit('transcribing')
            ConfigManager.console_print('Transcribing...')

            # Time the transcription process
            start_time = time.time()
            result = transcribe(audio_data, self.local_model)
            end_time = time.time()

            transcription_time = end_time - start_time
            ConfigManager.console_print(f'Transcription completed in {transcription_time:.2f} seconds. Post-processed line: {result}')

            if not self.is_running:
                return

            self.statusSignal.emit('idle')
            self.resultSignal.emit(result)

        except Exception as e:
            traceback.print_exc()
            self.statusSignal.emit('error')
            self.resultSignal.emit('')
        finally:
            self.stop_recording()

    def _live_preview_enabled(self):
        """Live text needs a mode selected and that mode's model available."""
        mode = live_preview_mode()
        if mode == 'batch':
            # Batch re-transcribes with the active engine — only fast local
            # engines can keep up with the refresh interval.
            return self.local_model is not None and resolve_engine() in ('parakeet', 'mlx')
        if mode == 'streaming':
            try:
                from parakeet_engine import preview_model_ready
                return preview_model_ready()
            except ImportError:
                return False
        return False

    def _partial_loop(self, recording):
        """Emit partial transcription text while recording.

        Dispatches on the configured preview mode. Both variants run on
        their own thread so the mic loop and inference never block each
        other, read `recording` (the same list the record loop appends to)
        via slice-copies taken under the GIL, and stop before the final
        transcription runs, so the two never hit the GPU at once.
        """
        if live_preview_mode() == 'streaming':
            self._partial_loop_streaming(recording)
        else:
            self._partial_loop_batch(recording)

    def _partial_loop_batch(self, recording):
        """Re-transcribe the growing buffer with the active engine on an interval."""
        interval = float(ConfigManager.get_config_value('misc', 'live_preview_interval') or 0.35)
        min_samples = int(PARTIAL_MIN_S * self.sample_rate)
        last_len = 0
        while not self._partial_stop.wait(interval):
            if not (self.is_running and self.is_recording):
                break
            snapshot = recording[:]  # atomic slice-copy; record loop keeps extending
            if len(snapshot) < min_samples or len(snapshot) == last_len:
                continue
            last_len = len(snapshot)
            try:
                text = transcribe(np.array(snapshot, dtype=np.int16), self.local_model)
            except Exception:
                traceback.print_exc()
                continue
            if text and text.strip():
                self.partialResultSignal.emit(text.strip())

    def _partial_loop_streaming(self, recording):
        """Feed new samples straight into Parakeet's rolling context.

        No timer: whatever audio arrived since the last pass is fed as soon
        as the previous inference finishes, so the pass rate is set by the
        inference time itself and the GPU load stays near-continuous
        (~60-100 ms per pass) instead of pulsing rhythmically.
        """
        import mlx.core as mx
        from parakeet_engine import get_preview_model, _to_float32, _resample

        model = get_preview_model()
        if model is None:
            return
        dst_rate = model.preprocessor_config.sample_rate
        # parakeet-mlx normalizes mel features PER add_audio CALL (get_logmel
        # normalize='per_feature' uses the chunk's own mean/std), so small
        # chunks feed the encoder statistically-distorted features and the
        # text comes out garbled. Chunks of ~1 s and up are indistinguishable
        # from offline transcription, so accumulate at least that much before
        # each feed. (An absolute floor of ~105 ms also exists: fewer mel
        # frames than one subsampling window crashes the encoder.)
        min_src = max(1, int(1.0 * self.sample_rate))
        fed = 0
        last_text = ''
        try:
            # The context manager switches the encoder to local attention on
            # entry and restores it on exit — when the preview shares the
            # main engine's model, the final pass must not start before this
            # block exits (the record loop joins this thread first).
            with model.transcribe_stream() as stream:
                while self.is_running and self.is_recording and not self._partial_stop.is_set():
                    chunk = recording[fed:]  # atomic slice-copy; record loop keeps extending
                    if len(chunk) < min_src:
                        if self._partial_stop.wait(0.02):
                            break
                        continue
                    fed += len(chunk)
                    audio = _resample(_to_float32(np.array(chunk, dtype=np.int16)),
                                      self.sample_rate, dst_rate)
                    stream.add_audio(mx.array(audio))
                    text = stream.result.text.strip()
                    if text and text != last_text:
                        last_text = text
                        self.partialResultSignal.emit(text)
        except Exception:
            traceback.print_exc()

    def _has_speech(self, audio_data):
        """True if the recording contains at least min_speech_duration ms of
        VAD-detected speech. Threshold 0 disables the gate."""
        min_speech_ms = ConfigManager.get_config_value(
            'recording_options', 'min_speech_duration')
        if not min_speech_ms:
            return True
        # webrtcvad alone is too permissive — it flags breath and room noise
        # as speech — so a frame only counts when it also clears an energy
        # floor. Calibrated on real captures: true silence scores ~150 ms,
        # normal dictation ~1400 ms, so the default 200 ms threshold splits
        # them cleanly. (Whisper's own no_speech_prob is 0.0 even on breath
        # it hallucinates "Thank you." for — measured, not usable.)
        rms_floor = 0.015
        vad = webrtcvad.Vad(3)
        frame = int(self.sample_rate * 0.03)
        speech_ms = 0
        for i in range(0, len(audio_data) - frame + 1, frame):
            f = audio_data[i:i + frame]
            rms = np.sqrt(np.mean((f.astype(np.float32) / 32768.0) ** 2))
            if rms < rms_floor:
                continue
            try:
                if vad.is_speech(f.tobytes(), self.sample_rate):
                    speech_ms += 30
                    if speech_ms >= min_speech_ms:
                        return True
            except Exception:
                # Unsupported rate/frame for webrtcvad — never block typing.
                return True
        return False

    def _record_audio(self):
        """
        Record audio from the microphone and save it to a temporary file.

        :return: numpy array of audio data, or None if the recording is too short
        """
        recording_options = ConfigManager.get_config_section('recording_options')
        self.sample_rate = recording_options.get('sample_rate') or 16000
        frame_duration_ms = 30  # 30ms frame duration for WebRTC VAD
        frame_size = int(self.sample_rate * (frame_duration_ms / 1000.0))
        silence_duration_ms = recording_options.get('silence_duration') or 900
        silence_frames = int(silence_duration_ms / frame_duration_ms)

        # Create VAD only for recording modes that use it
        recording_mode = recording_options.get('recording_mode') or 'continuous'

        # 150ms delay before starting VAD to avoid mistaking the sound of key pressing for voice
        # Skip the delay for hold_to_record mode since the key is still being held
        if recording_mode == 'hold_to_record':
            initial_frames_to_skip = 0
        else:
            initial_frames_to_skip = int(0.15 * self.sample_rate / frame_size)
        vad = None
        if recording_mode in ('voice_activity_detection', 'continuous'):
            vad = webrtcvad.Vad(2)  # VAD aggressiveness: 0 to 3, 3 being the most aggressive
            speech_detected = False
            silent_frame_count = 0

        audio_buffer = deque(maxlen=frame_size)
        recording = []

        data_ready = Event()

        # Precompute mel-scale filterbank for voice-focused spectrum visualization
        sr = self.sample_rate
        n_fft_bins = frame_size // 2 + 1
        n_bands = 160
        f_min, f_max = 60.0, 8000.0  # Voice range: fundamentals through sibilants
        mel_min = 2595.0 * np.log10(1.0 + f_min / 700.0)
        mel_max = 2595.0 * np.log10(1.0 + f_max / 700.0)
        mel_points = np.linspace(mel_min, mel_max, n_bands + 1)
        freq_points = 700.0 * (10.0 ** (mel_points / 2595.0) - 1.0)
        mel_bin_edges = np.round(freq_points * frame_size / sr).astype(int)
        mel_bin_edges = np.clip(mel_bin_edges, 0, n_fft_bins - 1)
        db_floor = -50.0
        db_ceil = 10.0

        def audio_callback(indata, frames, time, status):
            if status:
                ConfigManager.console_print(f"Audio callback status: {status}")
            audio_buffer.extend(indata[:, 0])
            data_ready.set()
            # Mel-scale spectrum: voice-focused frequency bands with dB amplitude
            samples = indata[:, 0].astype(np.float32) / 32768.0
            power = np.abs(np.fft.rfft(samples)) ** 2
            levels = []
            for i in range(n_bands):
                lo = mel_bin_edges[i]
                hi = max(lo + 1, mel_bin_edges[i + 1])
                band_power = np.mean(power[lo:hi])
                db = 10.0 * np.log10(max(band_power, 1e-10))
                level = (db - db_floor) / (db_ceil - db_floor)
                levels.append(float(max(0.0, min(1.0, level))))
            self.audioLevelSignal.emit(levels)

        # Start live-preview worker (re-transcribes the buffer during recording)
        self._partial_stop.clear()
        partial_thread = None
        if self._live_preview_enabled():
            partial_thread = Thread(target=self._partial_loop, args=(recording,), daemon=True)
            partial_thread.start()

        with sd.InputStream(samplerate=self.sample_rate, channels=1, dtype='int16',
                            blocksize=frame_size, device=recording_options.get('sound_device'),
                            callback=audio_callback):
            while self.is_running and self.is_recording:
                data_ready.wait()
                data_ready.clear()

                if len(audio_buffer) < frame_size:
                    continue

                # Save frame
                frame = np.array(list(audio_buffer), dtype=np.int16)
                audio_buffer.clear()
                recording.extend(frame)

                # Avoid trying to detect voice in initial frames
                if initial_frames_to_skip > 0:
                    initial_frames_to_skip -= 1
                    continue

                if vad:
                    if vad.is_speech(frame.tobytes(), self.sample_rate):
                        silent_frame_count = 0
                        if not speech_detected:
                            ConfigManager.console_print("Speech detected.")
                            speech_detected = True
                    else:
                        silent_frame_count += 1

                    if speech_detected and silent_frame_count > silence_frames:
                        break

        # Stop the live-preview worker before the final transcription so the
        # two never contend for the GPU — and, when the preview shares the
        # main model, so transcribe_stream's context manager restores the
        # encoder attention mode first. join waits out any in-flight pass.
        self._partial_stop.set()
        if partial_thread is not None:
            partial_thread.join(timeout=3.0)

        audio_data = np.array(recording, dtype=np.int16)
        duration = len(audio_data) / self.sample_rate

        ConfigManager.console_print(f'Recording finished. Size: {audio_data.size} samples, Duration: {duration:.2f} seconds')

        min_duration_ms = recording_options.get('min_duration') or 100

        if (duration * 1000) < min_duration_ms:
            ConfigManager.console_print(f'Discarded due to being too short.')
            return None

        return audio_data
