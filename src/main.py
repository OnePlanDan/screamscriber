import _macos_fix  # noqa: F401  -- patch pynput before it loads
import json
import os
import sys
import time
import traceback

# Run as a macOS "accessory" app (no Dock icon, no app menu bar). This is the
# right activation policy for a hotkey-driven background tool — same pattern
# as Bartender, Rectangle, Superwhisper, etc. — and it's the *only* policy
# under which third-party apps can render overlays in fullscreen Spaces.
# Must be set before QApplication is constructed.
if sys.platform == 'darwin':
    try:
        from AppKit import NSApplication
        # NSApplicationActivationPolicyAccessory = 1
        NSApplication.sharedApplication().setActivationPolicy_(1)
    except Exception:
        pass


def _save_frontmost_app():
    """Capture the macOS frontmost app so we can refocus it before typing."""
    if sys.platform != 'darwin':
        return None
    try:
        from AppKit import NSWorkspace
        return NSWorkspace.sharedWorkspace().frontmostApplication()
    except Exception:
        return None


def _frontmost_app():
    """Current macOS frontmost app, or None off-macOS / on failure."""
    if sys.platform != 'darwin':
        return None
    try:
        from AppKit import NSWorkspace
        return NSWorkspace.sharedWorkspace().frontmostApplication()
    except Exception:
        return None


def _activate_and_wait(app_ref):
    """Bring a saved app frontmost and poll until the activation lands."""
    try:
        # NSApplicationActivateIgnoringOtherApps = 1 << 1 = 2
        app_ref.activateWithOptions_(2)
        for _ in range(20):
            time.sleep(0.025)
            front = _frontmost_app()
            if front and front.processIdentifier() == app_ref.processIdentifier():
                break
    except Exception:
        pass
try:
    from audioplayer import AudioPlayer
except ImportError:
    AudioPlayer = None
from pynput.keyboard import Controller
from PyQt5.QtCore import QObject, QProcess, QFileSystemWatcher, QTimer, pyqtSignal
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QAction, QMessageBox

from api_server import APIServer
from key_listener import KeyListener
from result_thread import ResultThread
from ui.main_window import MainWindow
from ui.settings_window import SettingsWindow
from ui.status_window import StatusWindow
from ui.countdown_window import CountdownWindow
from input_simulation import InputSimulator
from typing_gate import TypingGate
from utils import ConfigManager


class ScreamScriberApp(QObject):
    # KeyListener callbacks fire on the pynput/event-tap thread; route ALL
    # GUI work through signals so it happens on the main thread. (Calling a
    # widget method directly from the listener thread segfaults Qt — learned
    # the hard way with begin_live_segment.)
    shapingToggleSignal = pyqtSignal()
    beginLiveSegmentSignal = pyqtSignal()

    def __init__(self):
        """
        Initialize the application, opening settings window if no configuration file is found.
        """
        super().__init__()
        self.app = QApplication(sys.argv)
        self.app.setWindowIcon(QIcon(os.path.join('assets', 'ww-logo.png')))

        ConfigManager.initialize()

        self.settings_window = SettingsWindow()
        self.settings_window.settings_closed.connect(self.on_settings_closed)
        self.settings_window.settings_saved.connect(self.on_settings_saved)

        if ConfigManager.config_file_exists():
            self.initialize_components()
        else:
            print('No valid configuration file found. Opening settings window...')
            self.settings_window.show()

    def initialize_components(self):
        """
        Initialize the components of the application.
        """
        self.input_simulator = InputSimulator()

        self.key_listener = KeyListener()
        self.key_listener.add_callback("on_activate", self.on_activation)
        self.key_listener.add_callback("on_deactivate", self.on_deactivation)
        self.key_listener.add_callback("on_chord_space", self.shapingToggleSignal.emit)
        self.shapingToggleSignal.connect(self._toggle_shaping)
        self.beginLiveSegmentSignal.connect(self._begin_live_segment)
        self.shaping_window = None
        self._last_shaping_toggle = 0.0

        self.countdown_window = CountdownWindow()
        self.typing_gate = TypingGate(window=self.countdown_window, key_listener=self.key_listener)

        # Lazy-load the model on first use for faster startup
        self.local_model = None

        self.result_thread = None

        self.main_window = MainWindow()
        self.main_window.startListening.connect(self.key_listener.start)
        self.main_window.closeApp.connect(self.exit_app)

        if not ConfigManager.get_config_value('misc', 'hide_status_window'):
            self.status_window = StatusWindow()

        self.create_tray_icon()
        self.main_window.show()

        self.api_server = None
        self.start_api_server()

        self._warm_preview_model()

        self._setup_config_watcher()

    def _warm_preview_model(self):
        """Load the Parakeet model that powers the streaming live preview.

        Only needed in 'streaming' preview mode ('batch' reuses the active
        engine's model). Runs eagerly on the main thread — MLX weight
        evaluation must not happen first on a worker thread (see
        parakeet_engine note). Shares the main model when the parakeet
        engine is selected for final text.
        """
        from result_thread import live_preview_mode
        if live_preview_mode() != 'streaming':
            return
        try:
            from transcription import resolve_engine, create_local_model
            from parakeet_engine import warm_preview_model
            if resolve_engine() == 'parakeet':
                if self.local_model is None:
                    self.local_model = create_local_model()
                warm_preview_model(self.local_model)
            else:
                warm_preview_model()
        except Exception:
            traceback.print_exc()

    def _setup_config_watcher(self):
        """Hot-reload settings when src/config.yaml is edited on disk."""
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.yaml')
        if not os.path.exists(config_path):
            return
        self._config_watcher = QFileSystemWatcher([config_path])
        self._config_path = config_path
        self._reload_timer = QTimer()
        self._reload_timer.setSingleShot(True)
        self._reload_timer.timeout.connect(self._do_config_reload)
        self._config_watcher.fileChanged.connect(self._on_config_file_changed)

    def _on_config_file_changed(self, path):
        # Editors often save by atomic-rename, which removes the watched
        # path; re-add it so subsequent changes still fire. Debounce to
        # ride out multi-event saves.
        if not self._config_watcher.files():
            self._config_watcher.addPath(self._config_path)
        self._reload_timer.start(250)

    def _do_config_reload(self):
        ConfigManager.console_print('config.yaml changed — reloading settings.')
        self.on_settings_saved()

    def start_api_server(self):
        """Start the API server if enabled and local model is available."""
        api_config = ConfigManager.get_config_section('api_server') or {}
        if not api_config.get('enabled', False):
            return

        from transcription import resolve_engine
        if resolve_engine() == 'api':
            ConfigManager.console_print('API server requires a local engine (faster-whisper, mlx, or parakeet)')
            return

        # Eagerly load the model for the API server
        if self.local_model is None:
            from transcription import create_local_model
            self.local_model = create_local_model()

        host = api_config.get('host', '127.0.0.1')
        port = api_config.get('port', 5000)
        self.api_server = APIServer(
            self.local_model,
            host=host,
            port=port,
            input_simulator=self.input_simulator,
            gate=self.typing_gate,
        )
        self.api_server.start()

    def create_tray_icon(self):
        """
        Create the system tray icon and its context menu.
        """
        self.tray_icon = QSystemTrayIcon(QIcon(os.path.join('assets', 'ww-logo.png')), self.app)

        tray_menu = QMenu()

        show_action = QAction('Screamscriber Main Menu', self.app)
        show_action.triggered.connect(self.main_window.show)
        tray_menu.addAction(show_action)

        settings_action = QAction('Open Settings', self.app)
        settings_action.triggered.connect(self.settings_window.show)
        tray_menu.addAction(settings_action)

        exit_action = QAction('Exit', self.app)
        exit_action.triggered.connect(self.exit_app)
        tray_menu.addAction(exit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()

    def cleanup(self):
        if self.api_server:
            self.api_server.stop()
        if self.key_listener:
            self.key_listener.stop()
        if self.input_simulator:
            self.input_simulator.cleanup()
        if hasattr(self, 'countdown_window') and self.countdown_window:
            self.countdown_window.close()

    def exit_app(self):
        """
        Exit the application.
        """
        self.cleanup()
        QApplication.quit()

    def restart_app(self):
        """Restart the application to apply the new settings."""
        self.cleanup()
        QApplication.quit()
        QProcess.startDetached(sys.executable, sys.argv)

    def on_settings_closed(self):
        """
        If settings is closed without saving on first run, initialize the components with default values.
        """
        if not os.path.exists(os.path.join('src', 'config.yaml')):
            QMessageBox.information(
                self.settings_window,
                'Using Default Values',
                'Settings closed without saving. Default values are being used.'
            )
            self.initialize_components()

    def on_settings_saved(self):
        """
        Apply settings without restarting the application.
        """
        ConfigManager.reload_config()

        # Update key listener with new activation keys and backend
        self.key_listener.update_activation_keys()
        self.key_listener.update_backend()

        # Clear model to force reload on next use with new settings
        self.local_model = None

        # Re-warm in case live_preview was just enabled (no-op if already loaded)
        self._warm_preview_model()

    def on_activation(self):
        """
        Called when the activation key combination is pressed.
        """
        if self.result_thread and self.result_thread.isRunning():
            recording_mode = ConfigManager.get_config_value('recording_options', 'recording_mode')
            if recording_mode == 'press_to_toggle':
                self.result_thread.stop_recording()
            elif recording_mode == 'continuous':
                self.stop_result_thread()
            return

        self.start_result_thread()

    def on_deactivation(self):
        """
        Called when the activation key combination is released.
        """
        if ConfigManager.get_config_value('recording_options', 'recording_mode') == 'hold_to_record':
            if self.result_thread and self.result_thread.isRunning():
                self.result_thread.stop_recording()

    def start_result_thread(self):
        """
        Start the result thread to record audio and transcribe it.
        """
        if self.result_thread and self.result_thread.isRunning():
            return

        self._cancel_held_delivery()

        # Capture the app the user was focused on at hotkey-press time — this
        # is the intended typing target that post_processing.focus_policy
        # enforces if focus moves during the recording.
        self._saved_frontmost = _save_frontmost_app()

        # Lazy-load the local model on first use
        if self.local_model is None and not ConfigManager.get_config_value('model_options', 'use_api'):
            from transcription import create_local_model
            self.local_model = create_local_model()

        self.result_thread = ResultThread(self.local_model)
        if not ConfigManager.get_config_value('misc', 'hide_status_window'):
            self.result_thread.statusSignal.connect(self.status_window.updateStatus)
            self.result_thread.audioLevelSignal.connect(self.status_window.updateAudioLevel)
            self.result_thread.partialResultSignal.connect(self.status_window.showPartial)
            self.status_window.closeSignal.connect(self.stop_result_thread)
        self.result_thread.resultSignal.connect(self.on_transcription_complete)
        # Live feedback in the shaping window: the upcoming segment number
        # appears immediately, its text streams in as it is spoken. Signal,
        # not a direct call — this method runs on the key-listener thread.
        self.result_thread.partialResultSignal.connect(self._on_partial_for_shaping)
        self.result_thread.statusSignal.connect(self._on_status_for_shaping)
        if self._shaping_active():
            self.beginLiveSegmentSignal.emit()
        self.result_thread.start()

    def stop_result_thread(self):
        """
        Stop the result thread.
        """
        if self.result_thread and self.result_thread.isRunning():
            self.result_thread.stop()

    def on_transcription_complete(self, result):
        """
        When the transcription is complete, log it, then either stack it in
        the shaping session or deliver it according to the focus policy, and
        start listening for the activation key again.
        """
        self._log_transcription(result)

        if self._shaping_active():
            if result and result.strip():
                self.shaping_window.receive_transcription(result.strip())
        else:
            delivered = self._deliver_transcription(result)
            if delivered and ConfigManager.get_config_value('misc', 'noise_on_completion') and AudioPlayer:
                AudioPlayer(os.path.join('assets', 'beep.wav')).play(block=True)

        if ConfigManager.get_config_value('recording_options', 'recording_mode') == 'continuous':
            self.start_result_thread()
        else:
            self.key_listener.start()

    def _shaping_active(self):
        return self.shaping_window is not None and self.shaping_window.session is not None

    def _begin_live_segment(self):
        if self._shaping_active():
            self.shaping_window.begin_live_segment()

    def _on_partial_for_shaping(self, text):
        if self._shaping_active():
            self.shaping_window.show_live_partial(text)

    def _on_status_for_shaping(self, status):
        # A recording that produced no segment (silence, cancel) leaves no row.
        if status in ('idle', 'error', 'cancel') and self._shaping_active():
            self.shaping_window.clear_live_partial()

    def _toggle_shaping(self):
        """Space pressed while holding the activation key (on the main thread)."""
        now = time.monotonic()
        if now - self._last_shaping_toggle < 0.5:  # key-repeat / double-fire guard
            return
        self._last_shaping_toggle = now

        if self._shaping_active():
            self.on_shaping_cancel()
            return

        if self.shaping_window is None:
            from ui.shaping_window import ShapingWindow
            self.shaping_window = ShapingWindow()
            self.shaping_window.deliverRequested.connect(self.on_shaping_deliver)
            self.shaping_window.cancelled.connect(self.on_shaping_cancel)

        # The recording in progress right now started from the user's real
        # target window — that's where Enter will eventually type.
        origin = getattr(self, '_saved_frontmost', None)
        self.shaping_window.start_session(origin)
        if hasattr(self, 'status_window'):
            # Move the analyzer/preview overlay below the shaping window —
            # immediately, since a recording is showing it right now.
            self.status_window.y_offset = 280
            self.status_window.reposition()
        # The toggle happens mid-recording: that recording is segment 1.
        self.shaping_window.begin_live_segment()
        ConfigManager.console_print('Shaping mode ON — dictations now stack in the shaping box.')

    def on_shaping_deliver(self, text):
        origin = self.shaping_window.session.origin_app if self.shaping_window.session else None
        self._end_shaping()
        if origin is not None:
            _activate_and_wait(origin)
        if ConfigManager.get_config_value('post_processing', 'add_trailing_space'):
            text += ' '
        self.input_simulator.typewrite(text)
        ConfigManager.console_print('Shaping mode OFF — shaped text delivered.')

    def on_shaping_cancel(self):
        origin = self.shaping_window.session.origin_app if self.shaping_window.session else None
        self._end_shaping()
        if origin is not None:
            _activate_and_wait(origin)  # give focus back to where the session began
        ConfigManager.console_print('Shaping mode OFF — cancelled, nothing typed (segments are in the log).')

    def _end_shaping(self):
        self.shaping_window.end_session()
        if hasattr(self, 'status_window'):
            self.status_window.y_offset = 0

    def _log_transcription(self, text):
        """Append the raw transcription to a local, gitignored JSONL log."""
        if not text or not ConfigManager.get_config_value('misc', 'transcription_log'):
            return
        try:
            saved = getattr(self, '_saved_frontmost', None)
            entry = {
                'ts': time.strftime('%Y-%m-%dT%H:%M:%S'),
                'app': saved.localizedName() if saved is not None else None,
                'text': text,
            }
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                'transcription_log.jsonl')
            with open(path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except Exception:
            traceback.print_exc()

    def _deliver_transcription(self, result):
        """Type the transcription according to post_processing.focus_policy.

        Returns True if the text was typed now, False if it is being held
        (or was empty). The press-time frontmost app is the user's intended
        target; the policy decides what to do when focus moved meanwhile.
        """
        if not result:
            return False
        policy = ConfigManager.get_config_value('post_processing', 'focus_policy') or 'return_to_origin'
        saved = getattr(self, '_saved_frontmost', None)
        current = _frontmost_app()
        if saved is None or current is None:
            self.input_simulator.typewrite(result)
            return True
        if current.processIdentifier() == os.getpid():
            # One of our own windows took focus — always give it back first.
            _activate_and_wait(saved)
            self.input_simulator.typewrite(result)
            return True
        if current.processIdentifier() == saved.processIdentifier() or policy == 'follow_focus':
            self.input_simulator.typewrite(result)
            return True
        if policy == 'return_to_origin':
            ConfigManager.console_print(
                f'Focus moved to {current.localizedName()} during recording — '
                f'returning to {saved.localizedName()} before typing.')
            _activate_and_wait(saved)
            self.input_simulator.typewrite(result)
            return True
        # hold_if_changed: wait for the user to refocus the original app.
        self._held_text = result
        self._held_target = saved
        self._hold_deadline = time.monotonic() + 60
        if not hasattr(self, '_hold_timer'):
            self._hold_timer = QTimer(self)
            self._hold_timer.setInterval(500)
            self._hold_timer.timeout.connect(self._check_held_delivery)
        self._hold_timer.start()
        ConfigManager.console_print(
            f'Focus moved to {current.localizedName()} during recording — holding text '
            f'until {saved.localizedName()} is focused again (60 s; text is in the log either way).')
        return False

    def _check_held_delivery(self):
        front = _frontmost_app()
        if front and self._held_target is not None \
                and front.processIdentifier() == self._held_target.processIdentifier():
            self._hold_timer.stop()
            text = self._held_text
            self._held_text = None
            self._held_target = None
            self.input_simulator.typewrite(text)
            return
        if time.monotonic() > self._hold_deadline:
            self._hold_timer.stop()
            self._held_text = None
            self._held_target = None
            ConfigManager.console_print('Held text expired undelivered — it remains in the transcription log.')

    def _cancel_held_delivery(self):
        if getattr(self, '_hold_timer', None) is not None and self._hold_timer.isActive():
            self._hold_timer.stop()
            self._held_text = None
            self._held_target = None
            ConfigManager.console_print('New recording started — canceled held text (still in the transcription log).')

    def run(self):
        """
        Start the application.
        """
        sys.exit(self.app.exec_())


if __name__ == '__main__':
    app = ScreamScriberApp()
    app.run()
