from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import QPainter, QBrush, QColor, QFont, QPainterPath, QGuiApplication, QCursor
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QMainWindow


# Cross-platform UI font fallback stack. Qt picks the first family that's
# installed: SF Pro on macOS, Segoe UI on Windows, Cantarell on GNOME,
# generic sans-serif otherwise. No Qt fallback warnings on any platform.
UI_FONT_FAMILIES = ['.AppleSystemUIFont', 'Segoe UI', 'Cantarell', 'sans-serif']


def ui_font(point_size=12, bold=False):
    f = QFont()
    f.setFamilies(UI_FONT_FAMILIES)
    f.setPointSize(point_size)
    if bold:
        f.setBold(True)
    return f


class BaseWindow(QMainWindow):
    def __init__(self, title, width, height):
        """
        Initialize the base window.
        """
        super().__init__()
        self.initUI(title, width, height)
        self.setWindowPosition()
        self.is_dragging = False

    def initUI(self, title, width, height):
        """
        Initialize the user interface.
        """
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(width, height)

        self.main_widget = QWidget(self)
        self.main_layout = QVBoxLayout(self.main_widget)
        self.main_layout.setContentsMargins(10, 10, 10, 10)

        # Create a widget for the title bar
        self.title_bar = QWidget()
        title_bar = self.title_bar
        title_bar_layout = QHBoxLayout(title_bar)
        title_bar_layout.setContentsMargins(0, 0, 0, 0)

        # Add the title label
        title_label = QLabel('Screamscriber')
        title_label.setFont(ui_font(12, bold=True))
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("color: #404040;")

        # Create a widget for the close button
        close_button_widget = QWidget()
        close_button_layout = QHBoxLayout(close_button_widget)
        close_button_layout.setContentsMargins(0, 0, 0, 0)

        self.close_button = QPushButton('×')
        self.close_button.setFixedSize(25, 25)
        self.close_button.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                color: #404040;
            }
            QPushButton:hover {
                color: #000000;
            }
        """)
        self.close_button.clicked.connect(self.handleCloseButton)

        close_button_layout.addWidget(self.close_button, alignment=Qt.AlignRight)

        # Add widgets to the title bar layout
        title_bar_layout.addWidget(QWidget(), 1)  # Left spacer
        title_bar_layout.addWidget(title_label, 3)  # Title (with more width)
        title_bar_layout.addWidget(close_button_widget, 1)  # Close button

        self.main_layout.addWidget(title_bar)
        self.setCentralWidget(self.main_widget)

    def setWindowPosition(self):
        """
        Set the window position to the center of the screen.
        """
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        center_point = screen.availableGeometry().center()
        frame_geometry = self.frameGeometry()
        frame_geometry.moveCenter(center_point)
        self.move(frame_geometry.topLeft())

    def handleCloseButton(self):
        """
        Close the window.
        """
        self.close()

    def mousePressEvent(self, event):
        """
        Allow the window to be moved by clicking and dragging anywhere on the window.
        """
        if event.button() == Qt.LeftButton:
            self.is_dragging = True
            self.start_position = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        """
        Move the window when dragging.
        """
        if Qt.LeftButton and self.is_dragging:
            self.move(event.globalPos() - self.start_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        """
        Stop dragging the window.
        """
        self.is_dragging = False

    def paintEvent(self, event):
        """
        Create a rounded rectangle with a semi-transparent white background.
        """
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 20, 20)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QBrush(QColor(255, 255, 255, 220)))
        painter.setPen(Qt.NoPen)
        painter.drawPath(path)
