"""Shaping mode window: segments left, assembled text right, prompt below.

Unlike the status overlay this window WANTS keyboard focus — single letters
are commands (see key legend). Two states, vim-style:

- nudge state (default): letters fire LLM nudges, z=undo, r=reset,
  p=edit prompt, Enter=deliver to the origin window, Esc=cancel.
- prompt state: the prompt field has focus and typing is just typing;
  Enter runs the prompt against the whole text, Esc returns to nudge state.
  Dictating while the field is focused inserts the transcription into the
  field instead of adding a segment (routing in main.py).

Nudge keys are dead while an LLM call is in flight — one call at a time.
"""
import html
import sys
import os

from PyQt5.QtCore import Qt, QRectF, pyqtSignal
from PyQt5.QtGui import QPainter, QBrush, QColor, QPainterPath
from PyQt5.QtWidgets import QHBoxLayout, QVBoxLayout, QLabel, QLineEdit, QTextEdit

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from ui.base_window import BaseWindow, ui_font
from shaping import ShapingSession, shape_stream, nudge_instruction


def _esc(text):
    return html.escape(text).replace('\n', '<br>')


class _PromptEdit(QLineEdit):
    """QLineEdit whose Escape returns focus to the window (nudge state)."""

    escaped = pyqtSignal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.escaped.emit()
            return
        super().keyPressEvent(event)


class ShapingWindow(BaseWindow):
    deliverRequested = pyqtSignal(str)
    cancelled = pyqtSignal()
    _streamSignal = pyqtSignal(str, str)  # (kind, payload) worker -> GUI thread

    HINT_IDLE = 'f/F formal   l/L length   e/E examples   c clean up   p prompt   z undo   r reset   ⏎ deliver   esc cancel'
    HINT_BUSY = '… shaping …'

    INK = '#202020'
    FADED = '#c4c4c4'
    THINK = '#a8a8a8'

    def __init__(self):
        super().__init__('Shaping mode', 720, 400)
        self.session = None
        self._busy = False
        self._old_text = ''       # text being overwritten while streaming
        self._live_partial = None  # in-progress dictation preview (left pane)
        self._streamSignal.connect(self._on_stream_event)
        self._init_ui()

    def _init_ui(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.title_bar.hide()

        header = QLabel('Shaping mode')
        header.setFont(ui_font(10, bold=True))
        header.setStyleSheet('color: #404040;')
        self.main_layout.addWidget(header)

        panes = QHBoxLayout()
        panes.setSpacing(8)

        self.segments_label = QLabel('')
        self.segments_label.setWordWrap(True)
        self.segments_label.setTextFormat(Qt.RichText)
        self.segments_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.segments_label.setFont(ui_font(9))
        self.segments_label.setStyleSheet('color: #808080; padding: 2px;')
        self.segments_label.setFixedWidth(200)

        self.text_view = QTextEdit()
        self.text_view.setReadOnly(True)
        self.text_view.setFocusPolicy(Qt.NoFocus)  # letters belong to the window
        self.text_view.setFont(ui_font(11))
        self.text_view.setStyleSheet(
            'QTextEdit { color: #202020; background: #f5f5f5; border: none; border-radius: 8px; padding: 6px; }')

        panes.addWidget(self.segments_label)
        panes.addWidget(self.text_view, 1)
        self.main_layout.addLayout(panes, 1)

        self.prompt_edit = _PromptEdit()
        # Click or 'p' only — never auto-focused on show, or the first
        # dictated segment would land in the prompt field.
        self.prompt_edit.setFocusPolicy(Qt.ClickFocus)
        self.prompt_edit.setPlaceholderText('p — prompt for the whole text (⏎ runs it, esc leaves, hold hotkey to dictate into it)')
        self.prompt_edit.setFont(ui_font(10))
        self.prompt_edit.setStyleSheet(
            'QLineEdit { color: #202020; background: #ffffff; border: 1px solid #d0d0d0; border-radius: 6px; padding: 5px; }')
        self.prompt_edit.returnPressed.connect(self._run_prompt)
        self.prompt_edit.escaped.connect(self._leave_prompt)
        self.main_layout.addWidget(self.prompt_edit)

        self.hint_label = QLabel(self.HINT_IDLE)
        self.hint_label.setFont(ui_font(8))
        self.hint_label.setStyleSheet('color: #a0a0a0;')
        self.hint_label.setAlignment(Qt.AlignCenter)
        self.main_layout.addWidget(self.hint_label)

    # -- session lifecycle -------------------------------------------------

    def start_session(self, origin_app):
        self.session = ShapingSession(origin_app)
        self._busy = False
        self.prompt_edit.clear()
        self._render()
        self.setWindowPosition()
        self.show()
        self.raise_()
        self.activateWindow()
        self.prompt_edit.clearFocus()
        self.setFocus()  # nudge state: keys belong to the window

    def end_session(self):
        self.session = None
        self.hide()

    def prompt_focused(self):
        return self.prompt_edit.hasFocus()

    def receive_transcription(self, text):
        """Dictation router: prompt field if focused, else a new segment."""
        if self.session is None:
            return
        if self.prompt_focused():
            self.prompt_edit.insert(text if not self.prompt_edit.text()
                                    or self.prompt_edit.text().endswith(' ')
                                    else ' ' + text)
        else:
            self._live_partial = None
            self.session.add_segment(text)
            self._render()

    # -- live feedback while a segment is being dictated ---------------------

    def begin_live_segment(self):
        """A recording just started: show the next segment number right away."""
        if self.session is None or self.prompt_focused():
            return
        self._live_partial = ''
        self._render()

    def show_live_partial(self, text):
        if self.session is None or self._live_partial is None:
            return
        self._live_partial = text
        self._render()

    def clear_live_partial(self):
        """The recording ended without producing a segment (silence/cancel)."""
        if self._live_partial is not None:
            self._live_partial = None
            if self.session is not None:
                self._render()

    # -- shaping actions ---------------------------------------------------

    def _nudge(self, key):
        instruction = nudge_instruction(key)
        if not instruction or not self.session or not self.session.current_text:
            return
        self._start_shape(instruction)

    def _run_prompt(self):
        instruction = self.prompt_edit.text().strip()
        if self._busy or not instruction or not self.session or not self.session.current_text:
            return
        self.prompt_edit.clear()
        self._leave_prompt()
        self._start_shape(instruction)

    def _start_shape(self, instruction):
        self._old_text = self.session.current_text
        self._set_busy(True)
        shape_stream(instruction, self._old_text,
                     lambda kind, payload: self._streamSignal.emit(kind, payload))

    def _on_stream_event(self, kind, payload):
        if self.session is None:
            return
        if kind == 'thinking':
            # Thinking streams above the faded old text while the model works.
            self.text_view.setHtml(
                f'<i><span style="color:{self.THINK}">{_esc(payload)}</span></i>'
                f'<br>·····<br>'
                f'<span style="color:{self.FADED}">{_esc(self._old_text)}</span>')
            bar = self.text_view.verticalScrollBar()
            bar.setValue(bar.maximum())  # follow the thinking
        elif kind == 'content':
            # The rewrite overwrites the faded old text as it streams in.
            remainder = self._old_text[len(payload):] if len(payload) < len(self._old_text) else ''
            self.text_view.setHtml(
                f'<span style="color:{self.INK}">{_esc(payload)}</span>'
                f'<span style="color:{self.FADED}">{_esc(remainder)}</span>')
        elif kind == 'done':
            self._set_busy(False)
            if payload:
                self.session.apply_shaped(payload)
            self._render()
        elif kind == 'error':
            self._set_busy(False)
            self.hint_label.setText(f'LLM error: {payload[:90]} — check shaping settings')
            self._render()

    def _set_busy(self, busy):
        self._busy = busy
        self.hint_label.setText(self.HINT_BUSY if busy else self.HINT_IDLE)
        if busy:
            # The instant a shape is requested the old text fades — it is
            # about to be overwritten by the streamed rewrite.
            self.text_view.setHtml(
                f'<span style="color:{self.FADED}">{_esc(self._old_text)}</span>')

    @staticmethod
    def _snippet(text, limit=46):
        text = text.strip()
        return text[:limit] + '…' if len(text) > limit else text

    def _render(self):
        if self.session is None:
            return
        rows = [f'{i}. {_esc(self._snippet(seg))}'
                for i, seg in enumerate(self.session.segments, 1)]
        if self._live_partial is not None:
            live = _esc(self._snippet(self._live_partial)) if self._live_partial else '🎙 …'
            rows.append(f'<i><span style="color:#b0b0b0">'
                        f'{len(self.session.segments) + 1}. {live}</span></i>')
        self.segments_label.setText(
            '<br>'.join(rows) or 'Hold the hotkey and speak —<br>segments stack up here.')
        self.text_view.setHtml(
            f'<span style="color:{self.INK}">{_esc(self.session.current_text)}</span>')

    def _leave_prompt(self):
        self.prompt_edit.clearFocus()
        self.setFocus()

    # -- keys ----------------------------------------------------------------

    def keyPressEvent(self, event):
        if self.session is None:
            return
        key = event.key()
        if key == Qt.Key_Escape:
            self.cancelled.emit()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if self.session.current_text:
                self.deliverRequested.emit(self.session.current_text)
            return
        if self._busy:
            return  # nudges are dead while a call is in flight
        text = event.text()
        if text in ('p', '/'):
            self.prompt_edit.setFocus()
            return
        if text == 'z':
            if self.session.undo():
                self._render()
            return
        if text == 'r':
            self.session.reset()
            self._render()
            return
        if text in ('f', 'F', 'l', 'L', 'e', 'E', 'c'):
            self._nudge(text)
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 16, 16)
        painter.setBrush(QBrush(QColor(255, 255, 255, 250)))
        painter.setPen(Qt.NoPen)
        painter.drawPath(path)
        painter.end()
