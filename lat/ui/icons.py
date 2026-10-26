"""Icon downloads and hover tooltips shared by the history views."""

import io
import time
import tkinter as tk

from lat.constants import APP_TITLE
from lat.history.cache import fetch_icon_bytes, ICON_FETCH_WORKERS, ICON_RETRY_AFTER_S
from lat.http import _http
from lat.ui.theme import TEXT_PRIMARY


class IconTooltipMixin:
    """Part of App (lat/ui/app.py); App.__init__ creates the state these
    methods use."""

    def _icon_for(self, urls, size, callback):
        """Fetch an icon trying each candidate URL; callback(photo|None) on UI thread.

        Downloads run on a small shared pool, identical requests share one
        download, icons are kept on disk between sessions, and a failure is
        retried after ICON_RETRY_AFTER_S instead of being remembered forever."""
        if isinstance(urls, str):
            urls = [urls]
        urls = [u for u in (urls or []) if u]
        if not urls:
            self._run_on_ui(lambda: callback(None))
            return
        key = (urls[0], size)
        photo = self._icon_photo_cache.get(key)
        if photo is not None:
            self._run_on_ui(lambda: callback(photo))
            return
        if time.monotonic() < self._icon_retry_at.get(key, 0):
            self._run_on_ui(lambda: callback(None))
            return
        waiters = self._icon_waiters.get(key)
        if waiters is not None:
            waiters.append(callback)
            return
        self._icon_waiters[key] = [callback]
        if self._icon_pool is None:
            from concurrent.futures import ThreadPoolExecutor
            self._icon_pool = ThreadPoolExecutor(
                max_workers=ICON_FETCH_WORKERS, thread_name_prefix="LAT-Icon")
        dead = frozenset(self._icon_dead_urls)

        def worker():
            image = None
            data, newly_dead = fetch_icon_bytes(urls, dead, self._icon_session())
            if data is not None:
                try:
                    from PIL import Image as PILImage
                    image = PILImage.open(io.BytesIO(data)).convert("RGBA")
                    image.thumbnail((size * 2, size * 2))
                except Exception:
                    image = None
            self._run_on_ui(lambda: deliver(image, newly_dead))

        def deliver(image, newly_dead):
            self._icon_dead_urls.update(newly_dead)
            photo = None
            if image is not None and not self._closing:
                try:
                    from PIL import ImageTk
                    photo = ImageTk.PhotoImage(image)
                except Exception:
                    photo = None
            if photo is not None:
                self._icon_photo_cache[key] = photo
                self._icon_retry_at.pop(key, None)
            else:
                self._icon_retry_at[key] = time.monotonic() + ICON_RETRY_AFTER_S
            for waiting in self._icon_waiters.pop(key, []):
                try:
                    waiting(photo)
                except tk.TclError:
                    pass

        try:
            self._icon_pool.submit(worker)
        except RuntimeError:            # pool already shut down (closing)
            self._icon_waiters.pop(key, None)

    def _icon_session(self):
        """One keep-alive HTTP session per pool thread."""
        session = getattr(self._icon_sessions, "session", None)
        if session is None:
            session = _http().Session()
            session.headers["User-Agent"] = APP_TITLE
            self._icon_sessions.session = session
        return session

    def _show_tooltip(self, widget, text):
        """Single delayed tooltip, positioned below-right and screen-clamped."""
        self._hide_tooltip()
        if not text:
            return

        def reveal():
            self._tooltip_after_id = None
            if self._closing:
                return
            try:
                tip = tk.Toplevel(self.root)
                tip.wm_overrideredirect(True)
                tk.Label(tip, text=text, bg="#1c2438", fg=TEXT_PRIMARY,
                         relief="solid", bd=1, font=("Segoe UI", 9),
                         justify="left", wraplength=420).pack(ipadx=8, ipady=5)
                tip.update_idletasks()
                screen_w = widget.winfo_screenwidth()
                screen_h = widget.winfo_screenheight()
                width = tip.winfo_reqwidth()
                height = tip.winfo_reqheight()
                x = widget.winfo_rootx() + 14
                y = widget.winfo_rooty() + max(1, widget.winfo_height()) + 8
                if x + width > screen_w - 10:
                    x = max(10, screen_w - width - 10)
                if y + height > screen_h - 10:
                    # Not enough space below - place above the chip instead.
                    y = max(10, widget.winfo_rooty() - height - 8)
                tip.wm_geometry(f"+{int(x)}+{int(y)}")
                self._tooltip = tip
            except Exception:
                self._tooltip = None

        self._tooltip_after_id = self.root.after(200, reveal)

    def _hide_tooltip(self, _event=None):
        after_id = getattr(self, "_tooltip_after_id", None)
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except Exception:
                pass
            self._tooltip_after_id = None
        tip = getattr(self, "_tooltip", None)
        if tip is not None:
            try:
                tip.destroy()
            except Exception:
                pass
            self._tooltip = None
