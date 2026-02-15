import sys
import os
from PyQt5.QtCore import Qt, QRectF, pyqtSignal, pyqtSlot, QTimer, QObject
from PyQt5.QtGui import QFont, QPixmap, QIcon, QPainter, QBrush, QColor, QPainterPath, QCursor
from PyQt5.QtWidgets import QApplication, QLabel, QHBoxLayout, QWidget

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from ui.base_window import BaseWindow


class SpectrumData(QObject):
    """Holds spectrum level data with smooth decay. No painting — the window draws it."""

    NUM_BANDS = 200
    DECAY_RATE = 0.06

    updated = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._levels = [0.0] * self.NUM_BANDS
        self._display = [0.0] * self.NUM_BANDS
        self._active = False

        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._decay_tick)

    @property
    def active(self):
        return self._active

    @property
    def display(self):
        return self._display

    def set_levels(self, levels):
        for i in range(min(len(levels), self.NUM_BANDS)):
            self._levels[i] = levels[i]
            if levels[i] > self._display[i]:
                self._display[i] = levels[i]
        if not self._timer.isActive():
            self._timer.start()
        self._active = True
        self.updated.emit()

    def reset(self):
        self._levels = [0.0] * self.NUM_BANDS
        self._display = [0.0] * self.NUM_BANDS
        self._active = False
        self._timer.stop()
        self.updated.emit()

    def _decay_tick(self):
        changed = False
        for i in range(self.NUM_BANDS):
            if self._display[i] > self._levels[i]:
                self._display[i] = max(self._levels[i], self._display[i] - self.DECAY_RATE)
                changed = True
        if changed:
            self.updated.emit()
        else:
            self._timer.stop()


class StatusWindow(BaseWindow):
    statusSignal = pyqtSignal(str)
    closeSignal = pyqtSignal()

    def __init__(self):
        """
        Initialize the status window.
        """
        super().__init__('Screamscriber Status', 320, 180)
        self.spectrum = SpectrumData(self)
        self.spectrum.updated.connect(self.update)
        self.initStatusUI()
        self.statusSignal.connect(self.updateStatus)

    def initStatusUI(self):
        """
        Initialize the status user interface.
        """
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.close_button.hide()

        status_layout = QHBoxLayout()
        status_layout.setContentsMargins(0, 0, 0, 0)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(32, 32)
        microphone_path = os.path.join('assets', 'microphone.png')
        pencil_path = os.path.join('assets', 'pencil.png')
        self.microphone_pixmap = QPixmap(microphone_path).scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.pencil_pixmap = QPixmap(pencil_path).scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.icon_label.setPixmap(self.microphone_pixmap)
        self.icon_label.setAlignment(Qt.AlignCenter)

        self.status_label = QLabel('Recording...')
        self.status_label.setFont(QFont('Segoe UI', 12))

        status_layout.addStretch(1)
        status_layout.addWidget(self.icon_label)
        status_layout.addWidget(self.status_label)
        status_layout.addStretch(1)

        self.main_layout.addLayout(status_layout)
        self.main_layout.addStretch(1)

    def show(self):
        """
        Position the window in the center of the screen and show it.
        """
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        screen_geometry = screen.geometry()
        screen_width = screen_geometry.width()
        screen_height = screen_geometry.height()
        window_width = self.width()
        window_height = self.height()

        x = screen_geometry.x() + (screen_width - window_width) // 2
        y = screen_geometry.y() + (screen_height - window_height) // 2

        self.move(x, y)
        super().show()

    def closeEvent(self, event):
        """
        Emit the close signal when the window is closed.
        """
        self.closeSignal.emit()
        super().closeEvent(event)

    def keyPressEvent(self, event):
        """
        Handle key press events. Escape key cancels the recording.
        """
        if event.key() == Qt.Key_Escape:
            self.closeSignal.emit()
            self.close()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Draw rounded white background
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 20, 20)
        painter.setBrush(QBrush(QColor(255, 255, 255, 255)))
        painter.setPen(Qt.NoPen)
        painter.drawPath(path)

        # Draw spectrum bars over the full window, clipped to rounded rect
        if self.spectrum.active:
            painter.setClipPath(path)
            painter.setBrush(QColor(0, 0, 0))
            painter.setPen(Qt.NoPen)

            w = self.width()
            h = self.height()
            n = SpectrumData.NUM_BANDS
            bar_w = w / n
            display = self.spectrum.display

            for i in range(n):
                bar_h = display[i] * h
                if bar_h < 1:
                    continue
                x = i * bar_w
                painter.drawRect(QRectF(x, h - bar_h, bar_w, bar_h))

        painter.end()

    @pyqtSlot(list)
    def updateAudioLevel(self, levels):
        """
        Update the spectrum analyzer with the current frequency band levels.
        """
        self.spectrum.set_levels(levels)

    @pyqtSlot(str)
    def updateStatus(self, status):
        """
        Update the status window based on the given status.
        """
        if status == 'recording':
            self.icon_label.setPixmap(self.microphone_pixmap)
            self.status_label.setText('Recording...')
            self.spectrum.reset()
            self.show()
        elif status == 'transcribing':
            self.icon_label.setPixmap(self.pencil_pixmap)
            self.status_label.setText('Transcribing...')
            self.spectrum.reset()

        if status in ('idle', 'error', 'cancel'):
            self.spectrum.reset()
            self.close()


if __name__ == '__main__':
    app = QApplication(sys.argv)

    status_window = StatusWindow()
    status_window.show()

    # Simulate status updates
    QTimer.singleShot(3000, lambda: status_window.statusSignal.emit('transcribing'))
    QTimer.singleShot(6000, lambda: status_window.statusSignal.emit('idle'))

    sys.exit(app.exec_())
