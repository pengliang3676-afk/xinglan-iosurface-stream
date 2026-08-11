from __future__ import annotations

import tkinter as tk
from typing import Any


def place_ime_caret(widget: Any, x: int, y: int, height: int = 24) -> bool:
    """Anchor the native Windows IME candidate window to a Tk widget point.

    Tk normally reports a canvas caret at (0, 0), which makes Windows place
    the candidate list in the application's upper-left corner.  ``tk caret``
    updates the native IME caret without creating an Entry, helper process, or
    polling timer.  Coordinates remain widget-relative, so the candidate list
    follows the projection window when it is moved.
    """

    try:
        width = max(1, int(widget.winfo_width()))
        widget_height = max(1, int(widget.winfo_height()))
        caret_x = max(0, min(int(x), width - 1))
        caret_y = max(0, min(int(y), widget_height - 1))
        caret_height = max(1, int(height))
        widget.tk.call(
            "tk",
            "caret",
            widget._w,
            "-x",
            caret_x,
            "-y",
            caret_y,
            "-height",
            caret_height,
        )
        return True
    except (AttributeError, TypeError, ValueError, tk.TclError):
        return False
