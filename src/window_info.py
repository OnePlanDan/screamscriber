"""X11 active-window introspection via xprop.

Returns the active window's title, WM_CLASS, and PID, or None if anything
fails (xprop missing, no active window, parse error, timeout).
"""

import re
import shutil
import subprocess


_ACTIVE_WIN_RE = re.compile(r'window id # (0x[0-9a-fA-F]+)')
_STRING_PROP_RE = re.compile(r'^(\S+) = (.+)$')


def _run_xprop(args, timeout=0.5):
    return subprocess.run(
        ['xprop', *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _parse_quoted_strings(value):
    return re.findall(r'"((?:[^"\\]|\\.)*)"', value)


def capture_active_window():
    """Return {'title', 'wm_class', 'pid'} for the focused X11 window, or None.

    Never raises. On any failure (no xprop, no active window, parse error)
    returns a tuple (None, error_message) so the caller can surface the
    reason in diagnostics.
    """
    if not shutil.which('xprop'):
        return None, 'xprop not found on PATH'

    try:
        root = _run_xprop(['-root', '-notype', '_NET_ACTIVE_WINDOW'])
    except subprocess.TimeoutExpired:
        return None, 'xprop timed out reading _NET_ACTIVE_WINDOW'
    if root.returncode != 0:
        return None, f'xprop -root failed: {root.stderr.strip()}'

    match = _ACTIVE_WIN_RE.search(root.stdout)
    if not match:
        return None, 'could not parse _NET_ACTIVE_WINDOW'
    win_id = match.group(1)
    if win_id == '0x0':
        return None, 'no active window'

    try:
        info = _run_xprop([
            '-id', win_id, '-notype',
            '_NET_WM_NAME', 'WM_NAME', 'WM_CLASS', '_NET_WM_PID',
        ])
    except subprocess.TimeoutExpired:
        return None, 'xprop timed out reading window properties'
    if info.returncode != 0:
        return None, f'xprop -id failed: {info.stderr.strip()}'

    props = {}
    for line in info.stdout.splitlines():
        m = _STRING_PROP_RE.match(line.strip())
        if m:
            props[m.group(1)] = m.group(2).strip()

    title = None
    for key in ('_NET_WM_NAME', 'WM_NAME'):
        if key in props:
            strings = _parse_quoted_strings(props[key])
            if strings:
                title = strings[0]
                break

    wm_class = None
    if 'WM_CLASS' in props:
        strings = _parse_quoted_strings(props['WM_CLASS'])
        if strings:
            wm_class = strings[-1]

    pid = None
    if '_NET_WM_PID' in props:
        try:
            pid = int(props['_NET_WM_PID'])
        except ValueError:
            pid = None

    return {'title': title, 'wm_class': wm_class, 'pid': pid}, None
