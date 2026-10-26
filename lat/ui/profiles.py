"""Configuration profiles: create, switch and delete."""

import copy
import tkinter as tk
from tkinter import simpledialog, messagebox

from lat.notify import generate_push_topic


class ProfilesMixin:
    """Part of App (lat/ui/app.py); App.__init__ creates the state these
    methods use."""

    def _refresh_profile_menu(self):
        if not hasattr(self, "profile_menu"):
            return
        names = sorted(self.profile_store["profiles"], key=str.casefold)
        menu = self.profile_menu_dropdown
        menu.delete(0, "end")
        for name in names:
            menu.add_command(label=name, command=tk._setit(self.profile_var, name))

    def _profile_action_allowed(self):
        if not self.bot_running and not self._resume_bot_on_connect:
            return True
        message = "Stop the bot before changing profiles."
        self.profile_notice_var.set(message)
        self.log(message)
        return False

    def _set_profile_controls_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        self.profile_menu.configure(state=state)
        for button in (self.profile_new_btn, self.profile_use_btn, self.profile_delete_btn):
            button.configure(state=state, cursor="hand2" if enabled else "arrow")

    def _create_profile(self):
        if not self._profile_action_allowed():
            return
        name = simpledialog.askstring(
            "New config profile",
            "Profile name (copies the active settings):",
            parent=self.root,
        )
        if name is not None:
            self._create_profile_named(name)

    def _create_profile_named(self, name):
        if not self._profile_action_allowed():
            return False
        name = str(name).strip()
        if not name or len(name) > 32 or any(ord(char) < 32 for char in name):
            self.profile_notice_var.set("Use a name from 1 to 32 characters.")
            return False
        existing = next(
            (profile for profile in self.profile_store["profiles"] if profile.casefold() == name.casefold()),
            None,
        )
        if existing is not None:
            self.profile_var.set(existing)
            self.profile_notice_var.set("That profile already exists.")
            return False

        self._sync_custom_status_draft()
        self._save_profiles_now()
        self.profile_store["profiles"][name] = copy.deepcopy(self.live_config)
        self.profile_var.set(name)
        self._refresh_profile_menu()
        self._activate_profile(name, save_current=False)
        self.log(f"Created and activated config profile: {name}")
        return True

    def _apply_selected_profile(self):
        if not self._profile_action_allowed():
            return False
        name = self.profile_var.get()
        if name not in self.profile_store["profiles"]:
            self.profile_notice_var.set("Selected profile no longer exists.")
            self._refresh_profile_menu()
            return False
        if name == self.active_profile:
            self.profile_notice_var.set("This profile is already active.")
            return True
        self._activate_profile(name)
        self.log(f"Activated config profile: {name}")
        return True

    def _activate_profile(self, name, save_current=True):
        if save_current:
            self._sync_custom_status_draft()
            self._save_profiles_now()
        target_config = copy.deepcopy(self.profile_store["profiles"][name])
        self.active_profile = name
        self.profile_store["active_profile"] = name
        self.live_config.clear()
        self.live_config.update(target_config)

        self._loading_profile = True
        try:
            self._apply_config_to_ui()
        finally:
            self._loading_profile = False

        self.profile_var.set(name)
        self.active_profile_var.set(f"Active: {name}")
        self.profile_notice_var.set("Profile applied.")
        self._save_profiles_now()

    def _apply_config_to_ui(self):
        cfg = self.live_config
        values = (
            (self.var_accept, bool(cfg.get("auto_accept", False))),
            (self.var_accept_instant, bool(cfg.get("accept_instant", False))),
            (self.var_accept_delay_ms, int(cfg.get("accept_delay_ms", 0))),
            (self.var_auto_requeue, bool(cfg.get("auto_requeue_enabled", False))),
            (self.var_hover, bool(cfg.get("hover_enabled", False))),
            (self.var_ban, bool(cfg.get("auto_ban", False))),
            (self.var_pick, bool(cfg.get("auto_pick", False))),
            (self.var_pick_mode, cfg.get("pick_mode", "normal")),
            (self.var_fav_expanded, bool(cfg.get("favorites_expanded", True))),
            (self.var_push_enabled, bool(cfg.get("push_enabled", False))),
            (self.var_push_topic, cfg.get("push_topic") or generate_push_topic()),
            (self.var_lobby_message_enabled, bool(cfg.get("lobby_message_enabled", False))),
            (self.var_lobby_message_text, cfg.get("lobby_message_text") or ""),
            (self.var_minimize_to_tray, bool(cfg.get("minimize_to_tray", True))),
            (self.var_auto_update, bool(cfg.get("auto_update_enabled", True))),
            (self.var_delay_ms, int(cfg.get("delay_ms", 500))),
        )
        for variable, value in values:
            variable.set(value)
        for key, variable in self.push_event_vars.items():
            variable.set(bool(cfg.get("push_events", {}).get(key, False)))

        self.ban_editor.set_list(cfg.get("ban_priority", []))
        self.pick_editor.set_list(cfg.get("pick_priority", []))
        self.hover_picker.set_value(cfg.get("hover_champion", ""))
        self.favorites_panel.set_list(cfg.get("favorites", []))
        self._set_custom_status_editor(cfg.get("custom_status_text", ""))
        self.custom_status_info_var.set("Not loaded")

    def _delete_selected_profile(self):
        if not self._profile_action_allowed():
            return False
        profiles = self.profile_store["profiles"]
        name = self.profile_var.get()
        if name not in profiles:
            self.profile_notice_var.set("Selected profile no longer exists.")
            return False
        if len(profiles) == 1:
            self.profile_notice_var.set("At least one profile is required.")
            return False
        if not messagebox.askyesno(
            "Delete config profile",
            f'Delete profile "{name}"?',
            parent=self.root,
        ):
            return False

        self._sync_custom_status_draft()
        self._save_profiles_now()
        del profiles[name]
        if name == self.active_profile:
            replacement = sorted(profiles, key=str.casefold)[0]
            self._activate_profile(replacement, save_current=False)
        else:
            self.profile_var.set(self.active_profile)
            self._save_profiles_now()
        self._refresh_profile_menu()
        self.profile_notice_var.set("Profile deleted.")
        self.log(f"Deleted config profile: {name}")
        return True
