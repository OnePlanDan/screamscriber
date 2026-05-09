"""Workaround for pyobjc 12.x lazy-import bug on macOS Tahoe.

pynput's listener thread crashes with `KeyError: 'AXIsProcessTrusted'`
inside pyobjc's `objc._lazyimport.get_constant` when called from a
worker thread *after* PyQt5 has touched pyobjc state. The same call
works fine on its own. Symptom seen during screamscriber startup.

Fix: replace the HIServices reference inside pynput._util.darwin with
a thin shim calling ApplicationServices.AXIsProcessTrusted, which
doesn't have the bug.

Import this module before pynput.keyboard.Listener is ever instantiated.
"""
import sys

if sys.platform == 'darwin':
    try:
        import pynput._util.darwin as _pud

        class _AXShim:
            # IS_TRUSTED is informational in pynput (used only to log a warning
            # if False). Real OS-level permission enforcement happens at the
            # event-tap layer, not here. Always return True to bypass pyobjc's
            # lazy-import bug that surfaces in the listener thread under both
            # PyQt5 and launchd contexts.
            @staticmethod
            def AXIsProcessTrusted():
                return True

        _pud.HIServices = _AXShim
    except Exception:
        import traceback
        traceback.print_exc()
