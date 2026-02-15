import os
import sys
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication, QLabel
from PyQt5.QtCore import pyqtSignal, Qt, QTimer

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from ui.base_window import BaseWindow

class MainWindow(BaseWindow):
    openSettings = pyqtSignal()
    startListening = pyqtSignal()
    closeApp = pyqtSignal()

    def __init__(self):
        """
        Initialize the main window.
        """
        super().__init__('WhisperWriter', 320, 180)
        self.initMainUI()

    def initMainUI(self):
        """
        Initialize the splash screen UI.
        """
        self.close_button.hide()

        welcome_label = QLabel('Welcome to WhisperWriter')
        welcome_label.setFont(QFont('Segoe UI', 14))
        welcome_label.setAlignment(Qt.AlignCenter)
        welcome_label.setStyleSheet("color: #404040;")

        self.main_layout.addStretch(1)
        self.main_layout.addWidget(welcome_label)
        self.main_layout.addStretch(1)

        self._splash_timer = QTimer(self)
        self._splash_timer.setSingleShot(True)
        self._splash_timer.timeout.connect(self._splash_done)

    def show(self):
        super().show()
        self._splash_timer.start(2000)

    def _splash_done(self):
        self.startListening.emit()
        self.hide()

    def closeEvent(self, event):
        """
        Close the application when the main window is closed.
        """
        self.closeApp.emit()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
