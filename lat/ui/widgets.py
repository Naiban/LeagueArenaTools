"""Reusable widgets: buttons, toggles, chips, cards and listbox styling."""

import tkinter as tk

from lat.ui.theme import (
    ACCENT, ACCENT_ALT, ACCENT_DARK, Animator, _blend, FIELD_BG, PANEL_ALT, PANEL_BG,
    PANEL_BORDER, TEXT_MUTED, TEXT_PRIMARY,
)


class AeroButton(tk.Canvas):
    """Main action button (Start/Stop bot) - soft gradient, gentle shadow,
    and a smooth animated hover/press transition instead of a hard color jump."""

    def __init__(self, parent, text, command=None, width=220, height=40, **kwargs):
        self._pad_top = 3
        self._pad_bottom = 8  # space for the shadow under the button
        canvas_h = height + self._pad_top + self._pad_bottom
        super().__init__(parent, width=width, height=canvas_h, highlightthickness=0,
                          bg=parent["bg"], cursor="hand2", **kwargs)
        self.command = command
        self.text = text
        self.width = width
        self.height = height
        self._hover_t = 0.0
        self._pressed = False
        self._animator = Animator(self)
        self.bind("<Enter>", lambda e: self._animate_hover(1.0))
        self.bind("<Leave>", lambda e: self._animate_hover(0.0))
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._draw()

    def set_text(self, text):
        self.text = text
        self._draw()

    def _animate_hover(self, target):
        start = self._hover_t

        def frame(p):
            self._hover_t = start + (target - start) * p
            self._draw()

        self._animator.animate(150, frame)

    def _on_press(self, _event):
        self._pressed = True
        self._draw()

    def _on_release(self, _event):
        was_pressed = self._pressed
        self._pressed = False
        self._draw()
        if was_pressed and self.command:
            self.command()

    def _draw(self):
        self.delete("all")
        w, h = self.width, self.height
        top_y = self._pad_top
        r = h / 2
        hover = self._hover_t

        top_c = _blend(ACCENT, ACCENT_ALT, 0.25 * hover)
        bot_c = _blend(ACCENT_DARK, ACCENT, 0.25 * hover)
        if self._pressed:
            top_c, bot_c = ACCENT_DARK, "#3d2ab8"

        # soft shadow under the button, subtly "lifting" on hover
        shadow_color = _blend(str(self["bg"]), "#000000", 0.30 + 0.10 * hover)
        shadow_y = top_y + h - 2 + 3 * hover
        self.create_oval(6, shadow_y - 5, h + 6, shadow_y + 5, fill=shadow_color, outline="")
        self.create_oval(w - h - 6, shadow_y - 5, w - 6, shadow_y + 5, fill=shadow_color, outline="")
        self.create_rectangle(r + 6, shadow_y - 5, w - r - 6, shadow_y + 5, fill=shadow_color, outline="")

        # pill gradient
        for i in range(h):
            ratio = i / h
            color = _blend(top_c, bot_c, ratio)
            self.create_line(r, top_y + i, w - r, top_y + i, fill=color)
        self.create_oval(0, top_y, h, top_y + h, fill=top_c, outline="")
        self.create_oval(w - h, top_y, w, top_y + h, fill=bot_c, outline="")

        # subtle top highlight - gives a "glassy pill" impression
        gloss = _blend(top_c, "#ffffff", 0.35)
        self.create_line(r, top_y + 2, w - r, top_y + 2, fill=gloss, width=1)

        self.create_text(w / 2, top_y + h / 2, text=self.text.upper(),
                          fill="#ffffff", font=("Segoe UI", 10, "bold"))


class NeoToggle(tk.Canvas):
    """Modern on/off pill switch bound to a tk.BooleanVar.
    The knob and track color animate smoothly between states."""

    def __init__(self, parent, variable, command=None, width=42, height=22, **kwargs):
        super().__init__(parent, width=width, height=height, highlightthickness=0,
                          bg=parent["bg"], cursor="hand2", **kwargs)
        self.variable = variable
        self.command = command
        self.width = width
        self.height = height
        self._animator = Animator(self)
        self._t = 1.0 if variable.get() else 0.0  # 0 = off, 1 = on
        self.bind("<Button-1>", self._toggle)
        self.variable.trace_add("write", lambda *_: self._sync())
        self._draw()

    def _toggle(self, event=None):
        self.variable.set(not self.variable.get())
        if self.command:
            self.command()

    def _sync(self):
        target = 1.0 if self.variable.get() else 0.0
        # Cancel a running tween first: after a quick off/on it would
        # otherwise finish at the old state while the variable is on.
        self._animator.stop()
        if abs(target - self._t) < 1e-6:
            self._draw()
            return
        start = self._t

        def frame(p):
            self._t = start + (target - start) * p
            self._draw()

        self._animator.animate(180, frame)

    def _draw(self):
        self.delete("all")
        t = self._t
        track = _blend("#293054", ACCENT, t)
        r = self.height / 2
        self.create_oval(0, 0, self.height, self.height, fill=track, outline="")
        self.create_oval(self.width - self.height, 0, self.width, self.height, fill=track, outline="")
        self.create_rectangle(r, 0, self.width - r, self.height, fill=track, outline="")
        kx = 3 + (self.width - self.height) * t
        self.create_oval(kx, 3, kx + self.height - 6, self.height - 3, fill="#ffffff", outline="")


class NeoChip(tk.Canvas):
    """Pill-shaped selector button (replaces ttk.Radiobutton), grouped
    via a shared tk.StringVar."""

    def __init__(self, parent, text, value, variable, command=None, width=84, height=26, **kwargs):
        super().__init__(parent, width=width, height=height, highlightthickness=0,
                          bg=parent["bg"], cursor="hand2", **kwargs)
        self.text = text
        self.value = value
        self.variable = variable
        self.command = command
        self.width = width
        self.height = height
        self.bind("<Button-1>", self._select)
        self.variable.trace_add("write", lambda *_: self._draw())
        self._draw()

    def _select(self, event=None):
        self.variable.set(self.value)
        if self.command:
            self.command()

    def _draw(self):
        self.delete("all")
        active = self.variable.get() == self.value
        bg = ACCENT if active else PANEL_ALT
        fg = "#ffffff" if active else TEXT_MUTED
        r = self.height / 2
        self.create_oval(0, 0, self.height, self.height, fill=bg, outline="")
        self.create_oval(self.width - self.height, 0, self.width, self.height, fill=bg, outline="")
        self.create_rectangle(r, 0, self.width - r, self.height, fill=bg, outline="")
        self.create_text(self.width / 2, self.height / 2, text=self.text,
                          fill=fg, font=("Segoe UI", 9, "bold"))


class MiniButton(tk.Button):
    """Small helper button for the dark theme."""

    def __init__(self, parent, text, command=None, width=3, fg=TEXT_PRIMARY, **kwargs):
        super().__init__(
            parent, text=text, command=command, width=width, fg=fg,
            bg=PANEL_ALT, activebackground=ACCENT, activeforeground="#ffffff",
            relief="flat", bd=0, highlightthickness=1, highlightbackground=PANEL_BORDER,
            font=("Segoe UI", 8, "bold"), cursor="hand2", **kwargs
        )


def style_listbox(box):
    box.configure(
        bg=FIELD_BG,
        fg=TEXT_PRIMARY,
        selectbackground=ACCENT,
        selectforeground="#ffffff",
        relief="flat",
        bd=0,
        highlightthickness=1,
        highlightbackground=PANEL_BORDER,
        highlightcolor=ACCENT,
        activestyle="none",
        font=("Segoe UI", 9),
    )


class Card(tk.Frame):
    """Compact settings card: a header (title + optional toggle on the
    right) and a body for the actual content. Keeps a group's options close
    together, separating groups with only a thin border."""

    def __init__(self, parent, title, toggle_var=None, on_toggle=None, **kwargs):
        super().__init__(parent, bg=PANEL_BG, highlightthickness=1,
                          highlightbackground=PANEL_BORDER, highlightcolor=PANEL_BORDER, **kwargs)
        header = tk.Frame(self, bg=PANEL_BG)
        header.pack(fill="x", padx=14, pady=(10, 4))
        accent_dot = tk.Canvas(header, width=6, height=6, highlightthickness=0, bg=PANEL_BG)
        accent_dot.pack(side="left", padx=(0, 8))
        accent_dot.create_oval(0, 0, 6, 6, fill=ACCENT_ALT, outline="")
        tk.Label(header, text=title.upper(), bg=PANEL_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        if toggle_var is not None:
            NeoToggle(header, toggle_var, command=on_toggle).pack(side="right")

        self.body = tk.Frame(self, bg=PANEL_BG)
        self._body_pack_opts = {"fill": "x", "padx": 14, "pady": (2, 12)}

        if toggle_var is not None:
            # The card shows/hides its body based on the header toggle's state.
            def _sync_visibility(*_):
                if toggle_var.get():
                    if not self.body.winfo_ismapped():
                        self.body.pack(**self._body_pack_opts)
                else:
                    if self.body.winfo_ismapped():
                        self.body.pack_forget()

            toggle_var.trace_add("write", _sync_visibility)
            _sync_visibility()
        else:
            self.body.pack(**self._body_pack_opts)
