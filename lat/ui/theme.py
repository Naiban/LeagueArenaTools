"""Futuristic minimal style - dark theme, purple-cyan accent - and the tween
helper used by the animated widgets."""

import tkinter as tk


BG = "#080b12"
SIDEBAR_BG = "#0d111c"
PANEL_BG = "#121827"
PANEL_ALT = "#192135"
PANEL_BORDER = "#26324b"
FIELD_BG = "#0b101b"
ACCENT = "#7c5cff"
ACCENT_DARK = "#5b3df0"
ACCENT_ALT = "#2dd4bf"
TEXT_PRIMARY = "#f4f7ff"
TEXT_MUTED = "#91a0bd"
SUCCESS = "#34d399"
DANGER = "#fb7185"
WARNING = "#fbbf24"
WIN_W = 1080
WIN_H = 760


def _blend(c1, c2, t):
    c1, c2 = c1.lstrip("#"), c2.lstrip("#")
    r1, g1, b1 = int(c1[0:2], 16), int(c1[2:4], 16), int(c1[4:6], 16)
    r2, g2, b2 = int(c2[0:2], 16), int(c2[2:4], 16), int(c2[4:6], 16)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _ease_out_cubic(t):
    return 1 - (1 - t) ** 3


class Animator:
    """Simple tween helper for Tk widgets - advances frames via .after()."""

    def __init__(self, widget, fps=60):
        self.widget = widget
        self.fps = fps
        self._job = None
        # A frame still pending when the widget goes would run against a
        # deleted Tcl command ("invalid command name ... <lambda>").
        widget.bind("<Destroy>", lambda _event: self.stop(), add="+")

    def stop(self):
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def animate(self, duration_ms, on_frame, on_done=None, ease=_ease_out_cubic):
        self.stop()
        steps = max(1, int(duration_ms / (1000 / self.fps)))

        def _step(i=0):
            t = min(1.0, i / steps)
            try:
                on_frame(ease(t))
            except tk.TclError:
                return  # widget was destroyed during the animation
            if i < steps:
                self._job = self.widget.after(int(1000 / self.fps), lambda: _step(i + 1))
            else:
                self._job = None
                if on_done:
                    on_done()

        _step()
