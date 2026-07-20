import _macos_fix  # noqa: F401  -- patch pynput before it loads
import os
import sys
import time

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


def _restore_frontmost_app(app_ref):
    """Re-activate a previously saved macOS app and give it a moment to focus."""
    if app_ref is None:
        return
    try:
        # NSApplicationActivateIgnoringOtherApps = 1 << 1 = 2
        app_ref.activateWithOptions_(2)
        time.sleep(0.05)
    except Exception:
        pass
try:
    from audioplayer import AudioPlayer
except ImportError:
    AudioPlayer = None
from pynput.keyboard import Controller
from PyQt5.QtCore import QObject, QProcess, QFileSystemWatcher, QTimer
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

        self._setup_config_watcher()

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

        # Capture the app the user was focused on at hotkey-press time so we
        # can re-focus it before typing — the StatusWindow appearing would
        # otherwise steal focus and the keystrokes would land nowhere.
        self._saved_frontmost = _save_frontmost_app()

        # Lazy-load the local model on first use
        if self.local_model is None and not ConfigManager.get_config_value('model_options', 'use_api'):
            from transcription import create_local_model
            self.local_model = create_local_model()

        self.result_thread = ResultThread(self.local_model)
        if not ConfigManager.get_config_value('misc', 'hide_status_window'):
            self.result_thread.statusSignal.connect(self.status_window.updateStatus)
            self.result_thread.audioLevelSignal.connect(self.status_window.updateAudioLevel)
            self.status_window.closeSignal.connect(self.stop_result_thread)
        self.result_thread.resultSignal.connect(self.on_transcription_complete)
        self.result_thread.start()

    def stop_result_thread(self):
        """
        Stop the result thread.
        """
        if self.result_thread and self.result_thread.isRunning():
            self.result_thread.stop()

    def on_transcription_complete(self, result):
        """
        When the transcription is complete, type the result and start listening for the activation key again.
        """
        _restore_frontmost_app(getattr(self, '_saved_frontmost', None))
        self.input_simulator.typewrite(result)

        if ConfigManager.get_config_value('misc', 'noise_on_completion') and AudioPlayer:
            AudioPlayer(os.path.join('assets', 'beep.wav')).play(block=True)

        if ConfigManager.get_config_value('recording_options', 'recording_mode') == 'continuous':
            self.start_result_thread()
        else:
            self.key_listener.start()

    def run(self):
        """
        Start the application.
        """
        sys.exit(self.app.exec_())


if __name__ == '__main__':
    app = ScreamScriberApp()
    app.run()
