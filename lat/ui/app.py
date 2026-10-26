"""Main window: start-up, UI-thread queue, connection polling, bot control,
window state and shutdown. Each page and feature lives in its own mixin
module; App combines them."""

import sys
import copy
import time
import queue
import threading
from datetime import datetime
import tkinter as tk
from tkinter import messagebox

from lat.bot import LeagueArenaToolsBot
from lat.config import (
    DEFAULT_CONFIG, DEFAULT_STATS, load_profile_store, parse_window_geometry, _sanitize_window,
    save_profile_store, STAT_ITEMS, window_title_visible,
)
from lat.constants import APP_TITLE, __version__
from lat.lcu import ChampionData, LCUClient
from lat.notify import generate_push_topic
from lat.paths import get_resource_path
from lat.ui.arena_overview import ArenaOverviewMixin
from lat.ui.custom_status import CustomStatusMixin
from lat.ui.history_page import HistoryPageMixin
from lat.ui.icons import IconTooltipMixin
from lat.ui.live_lobby import LiveLobbyMixin
from lat.ui.match_view import MatchViewMixin
from lat.ui.pages import PagesMixin
from lat.ui.profiles import ProfilesMixin
from lat.ui.ratings import RatingsMixin
from lat.ui.settings_status import SettingsStatusMixin
from lat.ui.theme import (
    ACCENT_ALT, BG, DANGER, PANEL_ALT, SIDEBAR_BG, SUCCESS, TEXT_MUTED, TEXT_PRIMARY, WIN_H,
    WIN_W,
)
from lat.ui.tray import tray_available, TrayMixin
from lat.ui.updates import UpdatesMixin
from lat.updater import consume_update_failed_marker


CONNECTION_POLL_MS = 2500
# Consecutive failed connection checks before the client counts as gone.
# One slow answer (typical while a game is loading) must not stop the bot.
CONNECTION_FAILURES_BEFORE_OFFLINE = 2
MAX_LOG_LINES = 3000
# How often the UI thread runs callables queued by worker threads.
UI_DRAIN_MS = 20


class App(PagesMixin, ProfilesMixin, SettingsStatusMixin, CustomStatusMixin, UpdatesMixin,
          HistoryPageMixin, MatchViewMixin, ArenaOverviewMixin, LiveLobbyMixin, RatingsMixin,
          IconTooltipMixin, TrayMixin):
    PAGE_META = {
        "dashboard": ("Dashboard", "Live status and the most important switches"),
        "champions": ("Champions", "Hover, ban, pick and favorite priorities"),
        "automation": ("Automation", "Matchmaking, lobby and background behavior"),
        "status": ("Custom Status", "Set the status shown in the League client"),
        "notifications": ("Notifications", "Private phone alerts delivered through ntfy"),
        "history": ("Match History", "Your games with builds, lobby ratings and Arena stats"),
        "activity": ("Activity", "Runtime events and diagnostics"),
    }

    def __init__(self, root):
        self.root = root
        self._ui_calls = queue.SimpleQueue()   # callables posted by worker threads
        self._ui_drain_after_id = None
        self.profile_store = load_profile_store()
        self.active_profile = self.profile_store["active_profile"]
        self.live_config = copy.deepcopy(self.profile_store["profiles"][self.active_profile])
        self._save_after_id = None
        self._poll_after_id = None
        self._tray_icon = None
        self._closing = False
        self._connection_state = None
        self._active_page = None
        self._status_request_running = False
        self._loading_profile = False
        self._update_check_after_id = None
        self._update_check_running = False
        self._update_installing = False
        self._available_update = None
        self._poll_running = False
        self._poll_failures = 0
        # Set when the bot was stopped by a client disconnect (patch, crash,
        # "close client during game"); it restarts once the client is back.
        self._resume_bot_on_connect = False
        self._setting_change_after = {}
        self._setting_states = {}   # key -> (state, detail) of the last change

        # Match History tab state.
        self.history_status_var = tk.StringVar(value="Not loaded")
        self._history_loading = False
        self._match_summaries = []
        self._detail_cache = {}
        self._elo_cache = {}
        self._history_items_map = {}      # item id -> {"name", "icon_urls", "tooltip"}
        self._history_augments_map = {}   # augment id -> {"name", "icon_urls", "tooltip"}
        self._history_meta_loaded = False
        self._history_loading_meta = False
        self._icon_photo_cache = {}       # (url, size) -> PhotoImage (successes only)
        self._icon_waiters = {}           # (url, size) -> callbacks of an in-flight fetch
        self._icon_retry_at = {}          # (url, size) -> monotonic time of the next try
        self._icon_dead_urls = set()      # candidate URLs that returned 404 / no image
        self._icon_pool = None
        self._icon_sessions = threading.local()
        self._my_identity_cache = None
        self._tooltip = None
        self._tooltip_after_id = None
        self._open_build_pid = None
        self._current_detail_view = None
        self._history_token = 0
        self._elo_cell_vars = {}
        self._all_summaries = []
        self._selected_game_id = None
        self._history_account = None      # account id the loaded games belong to
        self._history_exhausted = False   # "Load more" reached the oldest game
        self._history_loaded_once = False
        self._history_meta_network = False  # metadata was downloaded this session
        self._history_meta_forced = False   # one forced refresh for unknown ids
        self._history_detail_mode = None    # "game" | "stats" | None
        # Game in progress: lobby view built from the Live Client Data API.
        self._live_view = None
        self._live_token = 0
        self._gameflow_phase = None
        self._history_refresh_token = 0

        root.title(f"{APP_TITLE} v{__version__}")
        root.minsize(920, 700)
        self._window_state = dict(self.profile_store.get("window") or {})
        self._restore_window_geometry()
        root.bind("<Configure>", self._on_root_configure, add="+")
        root.configure(bg=BG)
        try:
            root.iconbitmap(get_resource_path("league_arena_tools.ico"))
        except Exception:
            pass
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._drain_ui_calls()

        self.client = LCUClient()
        self.champion_data = ChampionData()
        self.bot_running = False
        self.bot = LeagueArenaToolsBot(self.client, self.log, self.live_config, self.champion_data,
                                       stats_callback=self._on_stat_event,
                                       settings_callback=self._on_settings_applied)
        self._init_variables()
        self._build_header()
        self._build_shell()
        self._show_page("dashboard")
        self.log(f"{APP_TITLE} v{__version__} started. Profile: {self.active_profile}")
        failed_version = consume_update_failed_marker()
        if failed_version:
            self.update_status_var.set(f"Update to {failed_version} could not be installed")
            self.log(f"The update to v{failed_version} could not replace the EXE (file "
                     "locked, e.g. by antivirus). This version was restarted; try again.")
        # The first League check runs once the window is on screen; it loads
        # the HTTP stack and scans processes on a worker thread.
        self._poll_after_id = self.root.after(60, self._poll_connection)
        if self.var_auto_update.get():
            self._update_check_after_id = self.root.after(2500, self._check_for_updates)

    def _init_variables(self):
        cfg = self.live_config
        self.var_accept = tk.BooleanVar(value=cfg.get("auto_accept", False))
        self.var_accept_instant = tk.BooleanVar(value=cfg.get("accept_instant", False))
        self.var_accept_delay_ms = tk.IntVar(value=int(cfg.get("accept_delay_ms", 0)))
        self.var_auto_requeue = tk.BooleanVar(value=cfg.get("auto_requeue_enabled", False))
        self.var_hover = tk.BooleanVar(value=cfg.get("hover_enabled", False))
        self.var_ban = tk.BooleanVar(value=cfg.get("auto_ban", False))
        self.var_pick = tk.BooleanVar(value=cfg.get("auto_pick", False))
        self.var_pick_mode = tk.StringVar(value=cfg.get("pick_mode", "normal"))
        self.var_fav_expanded = tk.BooleanVar(value=cfg.get("favorites_expanded", True))
        self.var_push_enabled = tk.BooleanVar(value=cfg.get("push_enabled", False))
        self.var_push_topic = tk.StringVar(value=cfg.get("push_topic") or generate_push_topic())
        self.var_lobby_message_enabled = tk.BooleanVar(value=cfg.get("lobby_message_enabled", False))
        self.var_lobby_message_text = tk.StringVar(value=cfg.get("lobby_message_text") or "")
        self.custom_status_info_var = tk.StringVar(value="Not loaded")
        self.custom_status_count_var = tk.StringVar(value="0 characters")
        self.profile_var = tk.StringVar(value=self.active_profile)
        self.active_profile_var = tk.StringVar(value=f"Active: {self.active_profile}")
        self.profile_notice_var = tk.StringVar(value="")
        self.var_minimize_to_tray = tk.BooleanVar(value=cfg.get("minimize_to_tray", True))
        self.var_auto_update = tk.BooleanVar(value=cfg.get("auto_update_enabled", True))
        self.var_delay_ms = tk.IntVar(value=int(cfg.get("delay_ms", 500)))
        self.var_history_region = tk.StringVar(
            value=str(cfg.get("arenasweats_region") or "euw"))
        self.var_history_stat = tk.StringVar(
            value=str(cfg.get("history_stat_column") or "damage"))
        self.var_history_arena_only = tk.BooleanVar(
            value=bool(cfg.get("history_arena_only", True)))

        self.session_stats = dict(DEFAULT_STATS)
        self.stat_total_vars = {key: tk.StringVar(value="0") for key, _ in STAT_ITEMS}
        self.stat_session_vars = {
            key: tk.StringVar(value="+0 this run") for key, _ in STAT_ITEMS
        }
        self.stat_session_labels = {}

        bindings = (
            ("auto_accept", self.var_accept, self._bind_bool),
            ("accept_instant", self.var_accept_instant, self._bind_bool),
            ("accept_delay_ms", self.var_accept_delay_ms, self._bind_int),
            ("auto_requeue_enabled", self.var_auto_requeue, self._bind_bool),
            ("hover_enabled", self.var_hover, self._bind_bool),
            ("auto_ban", self.var_ban, self._bind_bool),
            ("auto_pick", self.var_pick, self._bind_bool),
            ("pick_mode", self.var_pick_mode, self._bind_str),
            ("favorites_expanded", self.var_fav_expanded, self._bind_bool),
            ("push_enabled", self.var_push_enabled, self._bind_bool),
            ("push_topic", self.var_push_topic, self._bind_str),
            ("lobby_message_enabled", self.var_lobby_message_enabled, self._bind_bool),
            ("lobby_message_text", self.var_lobby_message_text, self._bind_str),
            ("minimize_to_tray", self.var_minimize_to_tray, self._bind_bool),
            ("auto_update_enabled", self.var_auto_update, self._bind_bool),
            ("delay_ms", self.var_delay_ms, self._bind_int),
            ("arenasweats_region", self.var_history_region, self._bind_str),
            ("history_stat_column", self.var_history_stat, self._bind_str),
            ("history_arena_only", self.var_history_arena_only, self._bind_bool),
        )
        for key, variable, binder in bindings:
            binder(key, variable)

        events = cfg.get("push_events", {})
        self.push_event_vars = {
            key: tk.BooleanVar(value=events.get(key, False))
            for key in DEFAULT_CONFIG["push_events"]
        }
        self.status_var = tk.StringVar(value="Searching...")
        self.bot_state_var = tk.StringVar(value="Stopped")
        self.champion_count_var = tk.StringVar(value="Not loaded")
        self.update_status_var = tk.StringVar(value="Update check pending")
        self.settings_summary_var = tk.StringVar(value="Bot stopped")
        self.settings_status_var = tk.StringVar(
            value="Changes made while the bot runs are reported here.")

    def _on_stat_event(self, key):
        if key not in DEFAULT_STATS:
            return

        def apply():
            if self._closing:
                return
            self.session_stats[key] += 1
            self.profile_store["stats"][key] += 1
            self._refresh_stat_labels()
            self._schedule_save()

        self._run_on_ui(apply)

    def _refresh_stat_labels(self):
        totals = self.profile_store["stats"]
        for key, variable in self.stat_total_vars.items():
            variable.set(str(totals.get(key, 0)))
            count = self.session_stats.get(key, 0)
            self.stat_session_vars[key].set(f"+{count} this run")
            try:
                # Highlight the live counter only once something happened.
                self.stat_session_labels[key].configure(
                    fg=ACCENT_ALT if count else TEXT_MUTED)
            except (KeyError, tk.TclError):
                pass

    def _reset_stats(self):
        if not messagebox.askyesno(
            "Session stats",
            "Reset the all-time statistics?\n\nSession counters restart with every app start anyway.",
            parent=self.root,
        ):
            return
        for key in DEFAULT_STATS:
            self.session_stats[key] = 0
            self.profile_store["stats"][key] = 0
        self._refresh_stat_labels()
        self._save_profiles_now()
        self.log("Statistics reset.")

    def _run_on_ui(self, function):
        """Marshal a callable onto the Tk thread; safe from any worker thread.

        Worker threads only enqueue: calling Tk from them blocks until the
        main loop answers and raises "main thread is not in main loop" during
        start-up and shutdown."""
        if threading.current_thread() is threading.main_thread():
            try:
                self.root.after(0, function)
            except (tk.TclError, RuntimeError):
                pass
        else:
            self._ui_calls.put(function)

    def _drain_ui_calls(self):
        self._ui_drain_after_id = None
        deadline = time.monotonic() + 0.03
        while not self._closing and time.monotonic() < deadline:
            try:
                function = self._ui_calls.get_nowait()
            except queue.Empty:
                break
            try:
                function()
            except Exception:
                self.root.report_callback_exception(*sys.exc_info())
        if not self._closing:
            self._ui_drain_after_id = self.root.after(UI_DRAIN_MS, self._drain_ui_calls)

    def _show_page(self, name):
        if name not in self.page_frames:
            return
        self._active_page = name
        title, subtitle = self.PAGE_META[name]
        self.page_title_var.set(title)
        self.page_subtitle_var.set(subtitle)
        self.page_frames[name].tkraise()
        for key, button in self.nav_buttons.items():
            active = key == name
            button.configure(bg=PANEL_ALT if active else SIDEBAR_BG,
                             fg=TEXT_PRIMARY if active else TEXT_MUTED)

    def _set_connection_status(self, connected):
        self.status_dot_canvas.itemconfig(self.status_dot, fill=SUCCESS if connected else DANGER)
        self.header_status_var.set("League connected" if connected else "League offline")
        self.status_var.set("Connected" if connected else "Not running")

    def _schedule_save(self):
        if self._closing or self._loading_profile:
            return
        if self._save_after_id is not None:
            try:
                self.root.after_cancel(self._save_after_id)
            except Exception:
                pass
        def persist():
            self._save_after_id = None
            self._save_profiles_now()
        self._save_after_id = self.root.after(500, persist)

    def _save_profiles_now(self):
        self.profile_store["active_profile"] = self.active_profile
        self.profile_store["profiles"][self.active_profile] = copy.deepcopy(self.live_config)
        return save_profile_store(self.profile_store)

    def log(self, message):
        if self._closing:
            return
        def write():
            if self._closing or not self.log_box.winfo_exists():
                return
            now = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            self.log_box.configure(state="normal")
            self.log_box.insert("end", f"[{now}] ", "ts")
            self.log_box.insert("end", f"{message}\n")
            # Cap the log so a bot left running for days stays responsive.
            excess = int(self.log_box.index("end-1c").split(".")[0]) - MAX_LOG_LINES
            if excess > 200:
                self.log_box.delete("1.0", f"{excess + 1}.0")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self._run_on_ui(write)

    def _poll_connection(self):
        """Connection check on a worker thread: process scans and LCU calls
        (5 s timeout) never freeze the window."""
        self._poll_after_id = None
        if self._closing or self._poll_running:
            return
        self._poll_running = True
        threading.Thread(target=self._poll_connection_worker,
                         name="LeagueArenaTools-Connection", daemon=True).start()

    def _poll_connection_worker(self):
        try:
            connected = self.client.refresh()
        except Exception:
            connected = False
        champions_loaded = False
        if connected and not self.champion_data.loaded:
            champions_loaded = self.champion_data.load(self.client)
        phase = None
        if connected:
            try:
                response = self.client.get("/lol-gameflow/v1/gameflow-phase")
                if response.status_code == 200:
                    phase = response.json()
            except Exception:
                pass
        self._run_on_ui(lambda: self._finish_poll_connection(connected, champions_loaded, phase))

    def _finish_poll_connection(self, connected, champions_loaded, phase=None):
        self._poll_running = False
        if self._closing:
            return
        if connected:
            self._poll_failures = 0
        else:
            self._poll_failures += 1
        # Stay "connected" through one failed check: a single slow answer
        # while a game loads must not stop the bot.
        online = connected or (
            self._connection_state is True
            and self.client.is_connected
            and self._poll_failures < CONNECTION_FAILURES_BEFORE_OFFLINE
        )
        if online != self._connection_state:
            first_check = self._connection_state is None
            self._connection_state = online
            self._set_connection_status(online)
            if online:
                self.log("Connected to the League of Legends client.")
                if self._resume_bot_on_connect:
                    self._resume_bot_on_connect = False
                    self._start_bot("League client is back - bot resumed.")
            elif first_check:
                self.log("League client is not running - waiting for it.")
            else:
                self.log("League client disconnected.")
                if self.bot_running:
                    self._stop_bot()
                    self._enter_resume_wait()
        if champions_loaded:
            count = len(self.champion_data.names_sorted)
            self.champion_count_var.set(f"{count} loaded")
            self.log(f"Loaded {count} champions.")
            self._refresh_all_champion_widgets()
            self._refresh_settings_summary()
        # Only a phase the client answered with changes the live lobby: a
        # missed answer (busy client) or a closed client mid-game does not.
        if isinstance(phase, str):
            self._on_gameflow_phase(phase)
        self._poll_after_id = self.root.after(CONNECTION_POLL_MS, self._poll_connection)

    def _refresh_all_champion_widgets(self):
        self.ban_editor.refresh_champion_source()
        self.pick_editor.refresh_champion_source()
        self.hover_picker.refresh_champion_source()
        self.favorites_panel.refresh_champion_source()

    def _toggle_bot(self):
        if self.bot_running:
            self._stop_bot("Bot stopped. All pending automation was cancelled.")
            return
        if self._resume_bot_on_connect:
            self._resume_bot_on_connect = False
            self._stop_bot("Auto-resume cancelled. The bot stays stopped.")
            return
        if not self.client.is_connected or self._connection_state is not True:
            self.log("Cannot start - League client is not connected.")
            self._show_page("activity")
            return
        self._start_bot("Bot started.")

    def _start_bot(self, message):
        if not self.bot.start():
            return
        self.bot_running = True
        self.bot_state_var.set("Running")
        self.toggle_btn.set_text("Stop Bot")
        self._set_profile_controls_enabled(False)
        self.profile_notice_var.set("Stop the bot to change profiles.")
        self._sync_tray_state()
        self.log(message)
        self._announce_settings_on_start()

    def _enter_resume_wait(self):
        """Bot was stopped by a disconnect: keep it switched on and restart
        it as soon as the client answers again."""
        self._resume_bot_on_connect = True
        self.bot_state_var.set("Waiting for League")
        self.toggle_btn.set_text("Stop Bot")
        self._set_profile_controls_enabled(False)
        self.profile_notice_var.set("Stop the bot to change profiles.")
        self.log("Bot paused - the League client disconnected. "
                 "It resumes automatically when the client is back.")
        self._show_setting_status("saved", "Bot paused until the League client is back.")
        self._refresh_settings_summary()

    def _stop_bot(self, message=None):
        self.bot.stop()
        self.bot_running = False
        self.bot_state_var.set("Stopped")
        self.toggle_btn.set_text("Start Bot")
        self._set_profile_controls_enabled(True)
        self.profile_notice_var.set("")
        self._sync_tray_state()
        self._setting_states.clear()
        if not self._closing:
            self._show_setting_status(
                "saved", "Bot stopped. Changes are saved and apply when it starts.")
            self._refresh_settings_summary()
        if message:
            self.log(message)

    def _restore_window_geometry(self):
        """Last window size/position, unless it would open off-screen."""
        geometry = parse_window_geometry(self._window_state.get("geometry"))
        if geometry and window_title_visible(geometry[2], geometry[3], geometry[0]):
            width, height, x, y = geometry
            self.root.geometry(f"{width}x{height}+{x}+{y}")
        else:
            self.root.geometry(f"{WIN_W}x{WIN_H}")
        if self._window_state.get("maximized"):
            try:
                self.root.state("zoomed")
            except tk.TclError:
                pass

    def _on_root_configure(self, event):
        # Child widgets share the root's bindings: only the window itself.
        if event.widget is not self.root or self._closing:
            return
        try:
            state = self.root.state()
            if state == "zoomed":
                self._window_state["maximized"] = True
            elif state == "normal":
                # The normal size is kept while maximized, so un-maximizing
                # after a restart returns to it.
                if parse_window_geometry(self.root.geometry()):
                    self._window_state["geometry"] = self.root.geometry()
                self._window_state["maximized"] = False
        except tk.TclError:
            pass

    def _remember_window_geometry(self):
        self.profile_store["window"] = _sanitize_window(self._window_state)

    def _on_close(self):
        if self.live_config.get("minimize_to_tray") and tray_available():
            self._minimize_to_tray()
            return
        self._final_close()

    def _final_close(self):
        if self._closing:
            return
        self._closing = True
        self._sync_custom_status_draft()
        self.bot.stop()
        self.bot_running = False
        pending_jobs = (self._poll_after_id, self._save_after_id, self._update_check_after_id,
                        self._ui_drain_after_id,
                        *self._setting_change_after.values())
        for after_id in pending_jobs:
            if after_id is not None:
                try:
                    self.root.after_cancel(after_id)
                except Exception:
                    pass
        self._live_token += 1               # ends the live lobby watcher
        self._remember_window_geometry()
        self._save_profiles_now()
        if self._icon_pool is not None:
            self._icon_pool.shutdown(wait=False, cancel_futures=True)
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
            self._tray_icon = None
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()
