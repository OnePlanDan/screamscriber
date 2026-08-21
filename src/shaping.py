"""Shaping mode: collect dictated segments and sculpt them with a local LLM.

Toggled by pressing Space while the activation key is held. Segments
accumulate in a ShapingSession instead of being typed; single-letter nudges
and free-text prompts rewrite the assembled text through an OpenAI-compatible
endpoint (config section `shaping`). Enter delivers the result to the window
the session started from; raw segments always reach the transcription log.
"""
import re
import threading
import traceback

from utils import ConfigManager

# Maps a nudge key to its prompt's config name. Lowercase = more of the
# dimension, uppercase = less. Prompt texts live in config.yaml (shaping:)
# and are editable in the settings GUI; both hot-reload.
NUDGE_PROMPTS = {
    'f': 'prompt_formal_more',
    'F': 'prompt_formal_less',
    'l': 'prompt_length_more',
    'L': 'prompt_length_less',
    'e': 'prompt_examples_more',
    'E': 'prompt_examples_less',
    'c': 'prompt_coherent',
}

_THINK_RE = re.compile(r'<think>.*?</think>', re.DOTALL)


class ShapingSession:
    """Raw segments plus an undoable stack of shaped versions of their sum."""

    def __init__(self, origin_app=None):
        # The app that was frontmost when the session started — where Enter
        # will eventually type the shaped text.
        self.origin_app = origin_app
        self.segments = []
        self._history = []  # shaped states, oldest first; [-1] is current

    @property
    def current_text(self):
        if self._history:
            return self._history[-1]
        return ''

    def _raw_joined(self):
        return ' '.join(s.strip() for s in self.segments if s.strip())

    def add_segment(self, text):
        """Append a dictated segment. New speech attaches to the CURRENT
        (possibly shaped) text rather than re-deriving from raw, so earlier
        nudges survive further dictation."""
        self.segments.append(text)
        base = self.current_text
        joined = (base + ' ' + text.strip()).strip() if base else text.strip()
        self._history.append(joined)

    def apply_shaped(self, new_text):
        self._history.append(new_text.strip())

    def undo(self):
        """Drop the latest state. Returns True if something was undone."""
        if len(self._history) > 1:
            self._history.pop()
            return True
        return False

    def reset(self):
        """Back to the plain join of the raw segments (undoable)."""
        self._history.append(self._raw_joined())


def _call_setup(instruction, text):
    """Client + request kwargs from live config (hot-reloads per call)."""
    cfg = ConfigManager.get_config_section('shaping') or {}
    host = cfg.get('llm_host') or '127.0.0.1'
    port = cfg.get('llm_port') or 8080
    from openai import OpenAI
    client = OpenAI(base_url=f'http://{host}:{port}/v1',
                    api_key=cfg.get('llm_api_key') or 'local', timeout=60)
    kwargs = dict(
        model=cfg.get('llm_model') or 'qwen3-8b',
        temperature=0.3,
        messages=[
            {'role': 'system', 'content': cfg.get('system_prompt') or ''},
            {'role': 'user', 'content': f'{instruction}\n\nTEXT:\n{text}'},
        ],
    )
    return client, kwargs


def _clean(out):
    out = _THINK_RE.sub('', out).strip()
    # Models love wrapping rewrites in quotes; strip one matched pair.
    if len(out) > 1 and out[0] == out[-1] and out[0] in '"\'“”':
        out = out[1:-1].strip()
    return out


def shape_text(instruction, text):
    """One blocking LLM call: rewrite `text` per `instruction`. Raises on failure."""
    client, kwargs = _call_setup(instruction, text)
    response = client.chat.completions.create(**kwargs)
    return _clean(response.choices[0].message.content or '')


def shape_stream(instruction, text, on_event):
    """Streaming shaping call on a worker thread.

    on_event(kind, payload) fires on the worker thread with kind one of:
    'thinking' — accumulated reasoning so far (mtplx streams it as separate
    reasoning_content deltas; measured, not guessed),
    'content' — accumulated rewritten text so far,
    'done' — final cleaned text, 'error' — message.
    Route through a Qt signal to reach the GUI.
    """
    def work():
        try:
            client, kwargs = _call_setup(instruction, text)
            stream = client.chat.completions.create(stream=True, **kwargs)
            think, content = [], []
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                reasoning = (getattr(delta, 'reasoning_content', None)
                             or getattr(delta, 'reasoning', None))
                if reasoning:
                    think.append(reasoning)
                    on_event('thinking', ''.join(think))
                if delta.content:
                    content.append(delta.content)
                    on_event('content', _THINK_RE.sub('', ''.join(content)).lstrip())
            on_event('done', _clean(''.join(content)))
        except Exception as e:
            traceback.print_exc()
            on_event('error', str(e))

    t = threading.Thread(target=work, daemon=True)
    t.start()
    return t


def nudge_instruction(key):
    """Config-resolved prompt text for a nudge key, or None if unmapped."""
    name = NUDGE_PROMPTS.get(key)
    if not name:
        return None
    cfg = ConfigManager.get_config_section('shaping') or {}
    return cfg.get(name) or None
