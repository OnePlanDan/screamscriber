import os
import sys
from PyQt5.QtGui import QPixmap
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
        super().__init__('Screamscriber', 353, 287)
        self.initMainUI()

    def initMainUI(self):
        """
        Initialize the splash screen UI.
        """
        self.close_button.hide()

        image_label = QLabel()
        image_label.setAlignment(Qt.AlignCenter)
        pixmap = QPixmap(os.path.join('assets', 'boyandbot.png'))
        pixmap = pixmap.scaled(313, 227, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        image_label.setPixmap(pixmap)

        self.main_layout.addStretch(1)
        self.main_layout.addWidget(image_label)
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
