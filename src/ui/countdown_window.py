"""Countdown window shown before a remote /v1/type request actually types.

Display only — abort detection lives in the TypingGate via KeyListener.
"""

import sys
import os

from PyQt5.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QFont, QCursor
from PyQt5.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from ui.base_window import BaseWindow


def _truncate(text, limit=80):
    if text is None:
        return ''
    text = text.replace('\n', ' ').replace('\r', ' ')
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + '…'


class CountdownWindow(BaseWindow):
    """Floating window that displays a per-second countdown for remote typing."""

    showCountdownSignal = pyqtSignal(dict)
    hideCountdownSignal = pyqtSignal()
    abortFlashSignal = pyqtSignal()

    def __init__(self):
        super().__init__('Remote Typing Request', 420, 170)
        self._remaining = 0
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self.close)

        self._init_ui()

        self.showCountdownSignal.connect(self._on_show)
        self.hideCountdownSignal.connect(self._on_hide)
        self.abortFlashSignal.connect(self._on_abort_flash)

    def _init_ui(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.title_bar.hide()
        self.close_button.hide()

        layout = QVBoxLayout()
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)

        self.headline_label = QLabel('Remote typing in 3…')
        self.headline_label.setFont(QFont('Segoe UI', 14, QFont.Bold))
        self.headline_label.setStyleSheet('color: #202020;')

        self.target_label = QLabel('Target: —')
        self.target_label.setFont(QFont('Segoe UI', 9))
        self.target_label.setStyleSheet('color: #404040;')

        self.source_label = QLabel('From: —')
        self.source_label.setFont(QFont('Segoe UI', 9))
        self.source_label.setStyleSheet('color: #404040;')

        self.preview_label = QLabel('"…"')
        self.preview_label.setFont(QFont('Segoe UI', 9, QFont.StyleItalic))
        self.preview_label.setStyleSheet('color: #606060;')
        self.preview_label.setWordWrap(True)

        self.hint_label = QLabel('Press any key to abort')
        self.hint_label.setFont(QFont('Segoe UI', 8))
        self.hint_label.setStyleSheet('color: #808080;')

        layout.addWidget(self.headline_label)
        layout.addWidget(self.target_label)
        layout.addWidget(self.source_label)
        layout.addWidget(self.preview_label)
        layout.addStretch(1)
        layout.addWidget(self.hint_label)

        self.main_layout.addLayout(layout)

    def _position_center(self):
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        g = screen.geometry()
        x = g.x() + (g.width() - self.width()) // 2
        y = g.y() + (g.height() - self.height()) // 3
        self.move(x, y)

    @pyqtSlot(dict)
    def _on_show(self, info):
        self._flash_timer.stop()
        self._remaining = int(info.get('seconds', 3))
        target = info.get('window') or {}
        wm_class = target.get('wm_class') or 'unknown'
        title = _truncate(target.get('title') or '', 60)
        source_ip = info.get('source_ip') or 'unknown'
        preview = _truncate(info.get('text') or '', 80)

        self.headline_label.setText(f'Remote typing in {self._remaining}…')
        self.headline_label.setStyleSheet('color: #202020;')
        self.target_label.setText(f'Target: {wm_class} — {title}')
        self.source_label.setText(f'From: {source_ip}')
        self.preview_label.setText(f'"{preview}"')
        self.hint_label.setText('Press any key to abort')
        self.hint_label.setStyleSheet('color: #808080;')

        self._position_center()
        self.show()
        self.raise_()
        if self._remaining > 0:
            self._timer.start()

    def _tick(self):
        self._remaining -= 1
        if self._remaining <= 0:
            self._timer.stop()
            self.headline_label.setText('Typing now…')
            return
        self.headline_label.setText(f'Remote typing in {self._remaining}…')

    @pyqtSlot()
    def _on_hide(self):
        self._timer.stop()
        self._flash_timer.stop()
        self.close()

    @pyqtSlot()
    def _on_abort_flash(self):
        self._timer.stop()
        self.headline_label.setText('Remote Incoming API Aborted!')
        self.headline_label.setStyleSheet('color: #b00020;')
        self.hint_label.setText('')
        self._flash_timer.start(3000)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    w = CountdownWindow()
    QTimer.singleShot(300, lambda: w.showCountdownSignal.emit({
        'seconds': 5,
        'source_ip': '127.0.0.1',
        'window': {'wm_class': 'Google-chrome', 'title': 'Some long page title here'},
        'text': 'hello from the api this is a longer string to test truncation',
    }))
    QTimer.singleShot(3000, lambda: w.abortFlashSignal.emit())
    sys.exit(app.exec_())
