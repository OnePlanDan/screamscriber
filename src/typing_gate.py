"""Confirmation gate for the POST /v1/type endpoint.

Blocks the HTTP handler thread until the user lets the countdown expire
(grant) or presses any key (abort). GUI-only: relies on a Qt CountdownWindow
and the existing KeyListener for global keypress detection.
"""

import threading
import time
from dataclasses import dataclass

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QApplication

from utils import ConfigManager


@dataclass
class GateResult:
    granted: bool
    reason: str  # 'granted' | 'grace_cached' | 'user_aborted' | 'gate_busy' | 'disabled' | 'no_gui'


class TypingGate(QObject):
    """Single-flight gate that mediates remote-typing requests.

    Lives in the GUI thread but is called from API handler threads.
    Cross-thread display uses Qt signals; cross-thread blocking uses
    threading.Event.
    """

    _beepSignal = pyqtSignal()

    def __init__(self, window, key_listener):
        super().__init__()
        self._window = window
        self._key_listener = key_listener
        self._lock = threading.Lock()  # only one active countdown at a time
        self._busy = False
        self._event = threading.Event()
        self._abort_requested = False
        self._last_granted_at = 0.0
        self._beepSignal.connect(self._do_beep)

        # KeyListener fires callbacks with no args; we route through a single
        # bound method and gate it on whether a countdown is active.
        self._key_listener.add_callback('on_any_key_press', self._on_any_key)

    def await_permission(self, source_ip, window_info, text):
        """Block until grant or abort. Returns GateResult.

        Thread: called from an HTTP handler thread, NOT the GUI thread.
        """
        confirm = bool(ConfigManager.get_config_value('api_server', 'confirm_remote_typing'))
        grace_minutes = int(ConfigManager.get_config_value('api_server', 'confirm_grace_minutes') or 0)
        seconds = int(ConfigManager.get_config_value('api_server', 'confirm_countdown_seconds') or 0)
        beep_enabled = bool(ConfigManager.get_config_value('api_server', 'confirm_beep'))

        if not confirm or seconds <= 0:
            self._last_granted_at = time.monotonic()
            return GateResult(True, 'granted')

        # Grace cache check (only meaningful if grace_minutes > 0)
        if grace_minutes > 0 and self._last_granted_at > 0:
            elapsed = time.monotonic() - self._last_granted_at
            if elapsed < grace_minutes * 60:
                self._last_granted_at = time.monotonic()
                return GateResult(True, 'grace_cached')

        # Single-flight: refuse if another countdown is already in flight.
        if not self._lock.acquire(blocking=False):
            return GateResult(False, 'gate_busy')

        try:
            self._busy = True
            self._abort_requested = False
            self._event.clear()

            self._window.showCountdownSignal.emit({
                'seconds': seconds,
                'source_ip': source_ip,
                'window': window_info or {},
                'text': text,
            })
            if beep_enabled:
                self._beepSignal.emit()

            # Block here until countdown ticks down OR a key press flips abort.
            wait_seconds = max(seconds, 0)
            # Pad slightly so the GUI tick to "Typing now…" is visible.
            self._event.wait(timeout=wait_seconds + 0.05)

            if self._abort_requested:
                self._window.abortFlashSignal.emit()
                return GateResult(False, 'user_aborted')

            self._last_granted_at = time.monotonic()
            self._window.hideCountdownSignal.emit()
            return GateResult(True, 'granted')
        finally:
            self._busy = False
            self._lock.release()

    def _on_any_key(self):
        if not self._busy:
            return
        self._abort_requested = True
        self._event.set()

    def _do_beep(self):
        QApplication.beep()
