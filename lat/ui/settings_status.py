"""Setting bindings (Tk variable -> live config) and the Live Settings
status: whether a change made while the bot runs is active now, waits for
the next event, or cannot work as configured."""

import tkinter as tk

from lat.config import (
    BOT_SETTING_LABELS, READY_CHECK_SAFE_DELAY_MS, SETTING_CHECK_KEYS, TYPED_SETTING_KEYS,
)
from lat.constants import BRAVERY_LABEL
from lat.lcu import _normalize
from lat.ui.theme import DANGER, SUCCESS, TEXT_MUTED, TEXT_PRIMARY, WARNING


class SettingsStatusMixin:
    """Part of App (lat/ui/app.py); App.__init__ creates the state these
    methods use."""

    def _bind_bool(self, key, variable):
        def callback(*_):
            value = bool(variable.get())
            changed = self.live_config.get(key) != value
            self.live_config[key] = value
            self._schedule_save()
            if changed:
                self._on_setting_changed(key)
        variable.trace_add("write", callback)
        self.live_config[key] = bool(variable.get())

    def _bind_int(self, key, variable):
        def callback(*_):
            try:
                value = int(variable.get())
            except (tk.TclError, ValueError):
                return
            changed = self.live_config.get(key) != value
            self.live_config[key] = value
            self._schedule_save()
            if changed:
                self._on_setting_changed(key)
        variable.trace_add("write", callback)
        try:
            self.live_config[key] = int(variable.get())
        except (tk.TclError, ValueError):
            pass

    def _bind_str(self, key, variable):
        def callback(*_):
            value = variable.get()
            changed = self.live_config.get(key) != value
            self.live_config[key] = value
            self._schedule_save()
            if changed:
                self._on_setting_changed(key)
        variable.trace_add("write", callback)
        self.live_config[key] = variable.get()

    # ------------------------------------------------------------------
    # Live settings status: tells the user whether a change made while the
    # bot runs is already in effect, waits for the next event, or cannot
    # work as configured.
    # ------------------------------------------------------------------

    def _on_setting_changed(self, key):
        if self._closing or self._loading_profile or key not in BOT_SETTING_LABELS:
            return
        previous = self._setting_change_after.pop(key, None)
        if previous is not None:
            try:
                self.root.after_cancel(previous)
            except Exception:
                pass
        delay = 600 if key in TYPED_SETTING_KEYS else 0
        self._setting_change_after[key] = self.root.after(
            delay, lambda: self._report_setting_change(key))

    def _report_setting_change(self, key):
        self._setting_change_after.pop(key, None)
        if self._closing:
            return
        if self.bot_running:
            state, detail = self.bot.describe_setting_change(key)
        elif self._resume_bot_on_connect:
            state, detail = "saved", "applies when the bot resumes"
        else:
            state, detail = "saved", "applies when the bot starts"
        problem = self._setting_problem(key)
        if problem:
            state, detail = "problem", f"not working - {problem}"
        self._setting_states[key] = (state, detail)
        text = f"{BOT_SETTING_LABELS[key]}{self._describe_setting_value(key)}: {detail}"
        self._show_setting_status(state, text)
        if self.bot_running or problem:
            self.log(f"Live settings - {text}")
        self._refresh_settings_summary()

    def _on_settings_applied(self, keys, detail):
        """Bot callback (any thread): deferred settings are now in use."""
        def apply():
            if self._closing:
                return
            for key in keys:
                if self._setting_states.get(key, ("",))[0] == "pending":
                    self._setting_states[key] = ("active", detail)
            labels = ", ".join(BOT_SETTING_LABELS.get(key, key) for key in keys)
            text = f"{labels}: {detail}"
            self._show_setting_status("active", text)
            self.log(f"Live settings - {text}")
            self._refresh_settings_summary()
        self._run_on_ui(apply)

    def _describe_setting_value(self, key):
        value = self.live_config.get(key)
        if isinstance(value, bool):
            return " ON" if value else " OFF"
        if key in ("accept_delay_ms", "delay_ms"):
            return f" {value} ms"
        if key == "pick_mode":
            return " Instant" if value == "instant" else " Normal"
        if key == "hover_champion":
            return f" {value}" if value else " cleared"
        return " updated"

    def _unknown_champions(self, names, allow_bravery=False):
        if not self.champion_data.loaded:
            return []
        unknown = []
        for name in names or []:
            label = (name or "").strip()
            if not label or (allow_bravery and _normalize(label) == _normalize(BRAVERY_LABEL)):
                continue
            if not self.champion_data.resolve(label):
                unknown.append(label)
        return unknown

    def _setting_problem(self, key):
        """Why the feature `key` belongs to cannot work as set, or None."""
        cfg = self.live_config
        needs_champions = self.bot_running and not self.champion_data.loaded
        if key in ("auto_accept", "accept_instant", "accept_delay_ms"):
            delay = int(cfg.get("accept_delay_ms") or 0)
            if (cfg.get("auto_accept") and not cfg.get("accept_instant")
                    and delay >= READY_CHECK_SAFE_DELAY_MS):
                return f"a {delay} ms accept delay can outlast the ready check (about 10 s)"
        elif key in ("auto_ban", "ban_priority"):
            if cfg.get("auto_ban"):
                if needs_champions and cfg.get("ban_priority"):
                    return "champion list not loaded yet - bans would be random"
                unknown = self._unknown_champions(cfg.get("ban_priority"))
                if unknown:
                    return "unknown champion in the ban list: " + ", ".join(unknown)
        elif key in ("auto_pick", "pick_priority", "pick_mode", "delay_ms"):
            if cfg.get("auto_pick"):
                if needs_champions and any(
                        _normalize(n) != _normalize(BRAVERY_LABEL)
                        for n in cfg.get("pick_priority") or []):
                    return "champion list not loaded yet - picks would be random"
                unknown = self._unknown_champions(cfg.get("pick_priority"), allow_bravery=True)
                if unknown:
                    return "unknown champion in the pick list: " + ", ".join(unknown)
        elif key in ("hover_enabled", "hover_champion"):
            if cfg.get("hover_enabled"):
                name = (cfg.get("hover_champion") or "").strip()
                if not name:
                    return "no hover champion selected"
                if needs_champions:
                    return "champion list not loaded yet"
                if self._unknown_champions([name]):
                    return f"unknown hover champion '{name}'"
        elif key in ("lobby_message_enabled", "lobby_message_text"):
            if cfg.get("lobby_message_enabled") and not (cfg.get("lobby_message_text") or "").strip():
                return "the message text is empty"
        elif key in ("push_enabled", "push_topic", "push_events"):
            if cfg.get("push_enabled"):
                if not (cfg.get("push_topic") or "").strip():
                    return "the notification topic is empty"
                if not any((cfg.get("push_events") or {}).values()):
                    return "no notification events are selected"
        return None

    def _setting_problems(self):
        problems = []
        for key in SETTING_CHECK_KEYS:
            problem = self._setting_problem(key)
            if problem:
                problems.append((BOT_SETTING_LABELS[key], problem))
        return problems

    def _show_setting_status(self, state, text):
        color = {
            "active": SUCCESS, "pending": WARNING,
            "problem": DANGER, "saved": TEXT_MUTED,
        }.get(state, TEXT_MUTED)
        self.settings_status_var.set(text)
        try:
            self.settings_status_canvas.itemconfig(self.settings_status_dot, fill=color)
            self.settings_status_label.configure(fg=TEXT_PRIMARY if state != "saved" else TEXT_MUTED)
        except (AttributeError, tk.TclError):
            pass

    def _refresh_settings_summary(self):
        problems = self._setting_problems()
        pending = self.bot.pending_settings() if self.bot_running else {}
        if problems:
            count = len(problems)
            text, color = f"{count} issue{'s' if count != 1 else ''}", DANGER
        elif self._resume_bot_on_connect:
            text, color = "Waiting for League", WARNING
        elif not self.bot_running:
            text, color = "Bot stopped", TEXT_MUTED
        elif pending:
            text, color = f"{len(pending)} pending", WARNING
        else:
            text, color = "All active", SUCCESS
        self.settings_summary_var.set(text)
        try:
            self.settings_summary_label.configure(fg=color)
        except (AttributeError, tk.TclError):
            pass

    def _announce_settings_on_start(self):
        self._setting_states.clear()
        problems = self._setting_problems()
        for label, problem in problems:
            self.log(f"Live settings - {label}: not working - {problem}")
        if problems:
            label, problem = problems[0]
            more = f" (+{len(problems) - 1} more, see Activity)" if len(problems) > 1 else ""
            self._show_setting_status("problem", f"{label}: not working - {problem}{more}")
        else:
            self._show_setting_status(
                "active", "All settings are active. Changes made now apply live.")
        self._refresh_settings_summary()
