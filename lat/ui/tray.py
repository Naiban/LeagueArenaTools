"""Notification-area (tray) icon. Its menu runs on pystray's own thread."""

import threading
import importlib.util

from lat.constants import APP_TITLE, __version__
from lat.paths import get_resource_path


def _module_available(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


_tray_available = None


def tray_available():
    global _tray_available
    if _tray_available is None:
        _tray_available = _module_available("pystray") and _module_available("PIL")
    return _tray_available


class TrayMixin:
    """Part of App (lat/ui/app.py); App.__init__ creates the state these
    methods use."""

    def _minimize_to_tray(self):
        # Saved now too: a tray app is often ended by a Windows shutdown,
        # which never reaches _final_close.
        self._remember_window_geometry()
        self._schedule_save()
        if self._tray_icon is not None:
            self.root.withdraw()
            return
        self.root.withdraw()
        self._tray_icon = self._build_tray_icon()
        threading.Thread(target=self._tray_icon.run, name="LeagueArenaTools-Tray", daemon=True).start()

    def _build_tray_icon(self):
        import pystray

        specs = [
            ("Auto Accept", "var_accept", "auto_accept"),
            ("Auto Ban", "var_ban", "auto_ban"),
            ("Auto Pick", "var_pick", "auto_pick"),
            ("Hover", "var_hover", "hover_enabled"),
            ("Auto Re-queue", "var_auto_requeue", "auto_requeue_enabled"),
        ]
        items = [
            pystray.MenuItem(label, self._tray_toggle_action(attr),
                             checked=self._tray_toggle_checked(config_key))
            for label, attr, config_key in specs
        ]
        menu = pystray.Menu(
            pystray.MenuItem("Show", self._tray_show, default=True),
            pystray.Menu.SEPARATOR, *items, pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._tray_quit),
        )
        return pystray.Icon("league_arena_tools", self._tray_image(),
                            self._tray_title(), menu)

    def _tray_title(self):
        state = "bot running" if self.bot_running else "bot stopped"
        return f"{APP_TITLE} v{__version__} ({state})"

    def _tray_image(self):
        from PIL import Image, ImageDraw

        try:
            image = Image.open(get_resource_path("league_arena_tools.ico")).convert("RGBA")
        except Exception:
            image = Image.new("RGBA", (64, 64), (124, 92, 255, 255))
        if self.bot_running:
            # Green badge in the corner so the tray mirrors the bot state.
            width, height = image.size
            radius = max(5, width // 5)
            pad = max(2, width // 32)
            draw = ImageDraw.Draw(image)
            cx, cy = width - radius - pad, height - radius - pad
            draw.ellipse(
                (cx - radius, cy - radius, cx + radius, cy + radius),
                fill=(52, 211, 153, 255), outline=(8, 11, 18, 255),
                width=max(1, width // 48),
            )
        return image

    def _sync_tray_state(self):
        icon = self._tray_icon
        if icon is None:
            return
        try:
            icon.icon = self._tray_image()
            icon.title = self._tray_title()
        except Exception:
            pass

    def _tray_toggle_action(self, variable_name):
        def action(icon, item):
            def apply():
                variable = getattr(self, variable_name)
                variable.set(not variable.get())
                # The menu reads its check marks when it is rebuilt, so
                # rebuild it after the change, not before it is applied.
                try:
                    icon.update_menu()
                except Exception:
                    pass
            self._run_on_ui(apply)
        return action

    def _tray_toggle_checked(self, config_key):
        # Runs on the tray thread: read the plain config dict (kept in sync
        # by the variable traces), never a Tk variable.
        return lambda item: bool(self.live_config.get(config_key))

    def _tray_show(self, icon, item):
        self._run_on_ui(self._restore_from_tray)

    def _restore_from_tray(self):
        if self._tray_icon is not None:
            self._tray_icon.stop()
            self._tray_icon = None
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _tray_quit(self, icon, item):
        if self._tray_icon is not None:
            self._tray_icon.stop()
            self._tray_icon = None
        self._run_on_ui(self._final_close)
