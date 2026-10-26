"""Builders of the window frame and the Dashboard, Champions, Automation,
Notifications, Custom Status and Activity pages, with their handlers."""

import tkinter as tk
from tkinter import scrolledtext

from lat.config import MIN_DELAY_MS, STAT_ITEMS
from lat.constants import APP_TITLE, __version__
from lat.notify import generate_push_topic
from lat.ui.champion_widgets import FavoritesPanel, PriorityListEditor, SingleChampionPicker
from lat.ui.theme import (
    ACCENT, ACCENT_ALT, ACCENT_DARK, BG, DANGER, FIELD_BG, PANEL_ALT, PANEL_BG, PANEL_BORDER,
    SIDEBAR_BG, SUCCESS, TEXT_MUTED, TEXT_PRIMARY, WARNING,
)
from lat.ui.tray import tray_available
from lat.ui.widgets import AeroButton, Card, MiniButton, NeoChip, NeoToggle


class PagesMixin:
    """Part of App (lat/ui/app.py); App.__init__ creates the state these
    methods use."""

    def _build_header(self):
        header = tk.Frame(self.root, bg=SIDEBAR_BG, height=76, highlightthickness=1,
                          highlightbackground=PANEL_BORDER)
        header.pack(fill="x")
        header.pack_propagate(False)
        brand = tk.Frame(header, bg=SIDEBAR_BG)
        brand.pack(side="left", padx=22, pady=13)
        logo = tk.Canvas(brand, width=44, height=44, bg=SIDEBAR_BG, highlightthickness=0)
        logo.pack(side="left")
        logo.create_oval(2, 2, 42, 42, fill=ACCENT, outline=ACCENT_ALT, width=2)
        logo.create_text(22, 22, text="L", fill="white", font=("Segoe UI", 18, "bold"))
        titles = tk.Frame(brand, bg=SIDEBAR_BG)
        titles.pack(side="left", padx=(12, 0))
        tk.Label(titles, text=APP_TITLE, bg=SIDEBAR_BG, fg=TEXT_PRIMARY,
                 font=("Segoe UI", 17, "bold")).pack(anchor="w")
        tk.Label(titles, text=f"v{__version__}", bg=SIDEBAR_BG,
                 fg=TEXT_MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.toggle_btn = AeroButton(header, "Start Bot", command=self._toggle_bot, width=176, height=36)
        self.toggle_btn.pack(side="right", padx=(10, 22), pady=12)
        pill = tk.Frame(header, bg=PANEL_ALT, highlightthickness=1, highlightbackground=PANEL_BORDER)
        pill.pack(side="right", padx=8, pady=20)
        self.status_dot_canvas = tk.Canvas(pill, width=18, height=18, bg=PANEL_ALT, highlightthickness=0)
        self.status_dot_canvas.pack(side="left", padx=(9, 2), pady=5)
        self.status_dot = self.status_dot_canvas.create_oval(5, 5, 13, 13, fill=DANGER, outline="")
        self.header_status_var = tk.StringVar(value="League offline")
        tk.Label(pill, textvariable=self.header_status_var, bg=PANEL_ALT, fg=TEXT_MUTED,
                 font=("Segoe UI", 9, "bold")).pack(side="left", padx=(2, 10), pady=5)

    def _build_shell(self):
        shell = tk.Frame(self.root, bg=BG)
        shell.pack(fill="both", expand=True)
        sidebar = tk.Frame(shell, bg=SIDEBAR_BG, width=190)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(sidebar, text="WORKSPACE", bg=SIDEBAR_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=20, pady=(24, 10))
        self.nav_buttons = {}
        for key, label in (
            ("dashboard", "  Dashboard"), ("champions", "  Champions"),
            ("automation", "  Automation"), ("status", "  Custom Status"),
            ("notifications", "  Notifications"), ("history", "  History"),
            ("activity", "  Activity"),
        ):
            button = tk.Button(
                sidebar, text=label, command=lambda page=key: self._show_page(page),
                anchor="w", bg=SIDEBAR_BG, fg=TEXT_MUTED, activebackground=PANEL_ALT,
                activeforeground=TEXT_PRIMARY, relief="flat", bd=0, padx=16, pady=11,
                font=("Segoe UI", 10, "bold"), cursor="hand2",
            )
            button.pack(fill="x", padx=10, pady=2)
            self.nav_buttons[key] = button
        footer = tk.Frame(sidebar, bg=SIDEBAR_BG)
        footer.pack(side="bottom", fill="x", padx=14, pady=14)
        tk.Label(footer, text="CONFIG PROFILES", bg=SIDEBAR_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w")
        tk.Label(footer, textvariable=self.active_profile_var, bg=SIDEBAR_BG, fg=ACCENT_ALT,
                 font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x", pady=(4, 3))
        self.profile_menu = tk.OptionMenu(footer, self.profile_var, self.active_profile)
        self.profile_menu.configure(
            bg=FIELD_BG, fg=TEXT_PRIMARY, activebackground=PANEL_ALT,
            activeforeground=TEXT_PRIMARY, relief="flat", bd=0,
            highlightthickness=1, highlightbackground=PANEL_BORDER,
            anchor="w", font=("Segoe UI", 8), width=18,
        )
        self.profile_menu_dropdown = self.profile_menu.nametowidget(self.profile_menu["menu"])
        self.profile_menu_dropdown.configure(
            bg=FIELD_BG, fg=TEXT_PRIMARY, activebackground=ACCENT,
            activeforeground="#ffffff", font=("Segoe UI", 8),
        )
        self.profile_menu.pack(fill="x")
        profile_buttons = tk.Frame(footer, bg=SIDEBAR_BG)
        profile_buttons.pack(fill="x", pady=(5, 0))
        self.profile_new_btn = MiniButton(profile_buttons, "New", width=5,
                                          command=self._create_profile)
        self.profile_new_btn.pack(side="left")
        self.profile_use_btn = MiniButton(profile_buttons, "Use", width=5,
                                          command=self._apply_selected_profile)
        self.profile_use_btn.pack(side="left", padx=4)
        self.profile_delete_btn = MiniButton(profile_buttons, "Delete", width=6, fg=DANGER,
                                             command=self._delete_selected_profile)
        self.profile_delete_btn.pack(side="left")
        tk.Label(footer, textvariable=self.profile_notice_var, bg=SIDEBAR_BG,
                 fg=TEXT_MUTED, font=("Segoe UI", 7), anchor="w",
                 justify="left", wraplength=155).pack(fill="x", pady=(5, 0))
        self._refresh_profile_menu()

        content = tk.Frame(shell, bg=BG)
        content.pack(side="left", fill="both", expand=True)
        self.page_title_var = tk.StringVar()
        self.page_subtitle_var = tk.StringVar()
        heading = tk.Frame(content, bg=BG)
        heading.pack(fill="x", padx=26, pady=(20, 12))
        tk.Label(heading, textvariable=self.page_title_var, bg=BG, fg=TEXT_PRIMARY,
                 font=("Segoe UI", 20, "bold")).pack(anchor="w")
        tk.Label(heading, textvariable=self.page_subtitle_var, bg=BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 0))
        # Live settings status line, visible on every page so a change made on
        # Champions/Automation shows right away whether it is in effect.
        status_bar = tk.Frame(content, bg=SIDEBAR_BG, highlightthickness=1,
                              highlightbackground=PANEL_BORDER)
        status_bar.pack(side="bottom", fill="x", padx=24, pady=(0, 14))
        self.settings_status_canvas = tk.Canvas(status_bar, width=14, height=14,
                                                bg=SIDEBAR_BG, highlightthickness=0)
        self.settings_status_canvas.pack(side="left", padx=(10, 4), pady=6)
        self.settings_status_dot = self.settings_status_canvas.create_oval(
            3, 3, 11, 11, fill=TEXT_MUTED, outline="")
        tk.Label(status_bar, text="LIVE SETTINGS", bg=SIDEBAR_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 7, "bold")).pack(side="left", padx=(0, 10))
        self.settings_status_label = tk.Label(
            status_bar, textvariable=self.settings_status_var, bg=SIDEBAR_BG,
            fg=TEXT_MUTED, font=("Segoe UI", 9), anchor="w", justify="left")
        self.settings_status_label.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.page_host = tk.Frame(content, bg=BG)
        self.page_host.pack(fill="both", expand=True, padx=24, pady=(0, 10))
        self.page_frames = {name: tk.Frame(self.page_host, bg=BG) for name in self.PAGE_META}
        for page in self.page_frames.values():
            page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._build_dashboard(self.page_frames["dashboard"])
        self._build_champions(self.page_frames["champions"])
        self._build_automation(self.page_frames["automation"])
        self._build_status(self.page_frames["status"])
        self._build_notifications(self.page_frames["notifications"])
        self._build_history(self.page_frames["history"])
        self._build_activity(self.page_frames["activity"])

    def _build_dashboard(self, page):
        page.grid_columnconfigure(0, weight=1, uniform="dashboard")
        page.grid_columnconfigure(1, weight=1, uniform="dashboard")
        page.grid_rowconfigure(1, weight=1)
        metrics = tk.Frame(page, bg=BG)
        metrics.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        for column in range(4):
            metrics.grid_columnconfigure(column, weight=1, uniform="metrics")
        self._metric(metrics, 0, "LEAGUE CLIENT", self.status_var, ACCENT_ALT)
        self._metric(metrics, 1, "BOT ENGINE", self.bot_state_var, ACCENT)
        self._metric(metrics, 2, "CHAMPION DATA", self.champion_count_var, SUCCESS)
        self.settings_summary_label = self._metric(
            metrics, 3, "LIVE SETTINGS", self.settings_summary_var, WARNING, last=True)
        self.settings_summary_label.configure(fg=TEXT_MUTED)
        quick = Card(page, "Quick Controls")
        quick.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        self._toggle_row(quick.body, "Auto Accept", "Accept ready checks automatically", self.var_accept)
        self._toggle_row(quick.body, "Auto Ban", "Use your ordered ban priority", self.var_ban)
        self._toggle_row(quick.body, "Auto Pick", "Lock your selected champion priority", self.var_pick)
        self._toggle_row(quick.body, "Hover", "Show your intent before the pick", self.var_hover)
        self._toggle_row(quick.body, "Auto Re-queue", "Automatically start matchmaking after joining a lobby", self.var_auto_requeue)
        overview = Card(page, "Runtime Overview")
        overview.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        self._info_row(overview.body, "Pick mode", self.var_pick_mode)
        self._info_row(overview.body, "Confirm delay", self.var_delay_ms, " ms")
        self._info_row(overview.body, "Accept delay", self.var_accept_delay_ms, " ms")
        self._build_stats_card(page)

    def _build_stats_card(self, page):
        card = Card(page, "Statistics")
        card.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        strip = tk.Frame(card.body, bg=PANEL_BG)
        strip.pack(fill="x")
        MiniButton(strip, "Reset", width=7, command=self._reset_stats).pack(
            side="right", padx=(12, 0))
        for index, (key, label) in enumerate(STAT_ITEMS):
            if index:
                tk.Frame(strip, width=1, bg=PANEL_BORDER).pack(side="left", fill="y", pady=2)
            block = tk.Frame(strip, bg=PANEL_BG)
            block.pack(side="left", fill="both", expand=True)
            tk.Label(block, text=label, bg=PANEL_BG, fg=TEXT_MUTED,
                     font=("Segoe UI", 7, "bold")).pack(anchor="w")
            tk.Label(block, textvariable=self.stat_total_vars[key], bg=PANEL_BG,
                     fg=TEXT_PRIMARY, font=("Segoe UI", 13, "bold")).pack(anchor="w")
            session_label = tk.Label(block, textvariable=self.stat_session_vars[key],
                                     bg=PANEL_BG, fg=TEXT_MUTED,
                                     font=("Segoe UI", 8))
            session_label.pack(anchor="w")
            self.stat_session_labels[key] = session_label
        self._refresh_stat_labels()

    def _build_champions(self, page):
        page.grid_columnconfigure(0, weight=1, uniform="champions")
        page.grid_columnconfigure(1, weight=1, uniform="champions")
        page.grid_rowconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        ban_card = Card(page, "Auto Ban", toggle_var=self.var_ban)
        ban_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 6))
        self.ban_editor = PriorityListEditor(
            ban_card.body, self.champion_data, self._on_ban_change,
            initial=self.live_config.get("ban_priority", []),
        )
        self.ban_editor.pack(fill="both", expand=True)
        pick_card = Card(page, "Auto Pick", toggle_var=self.var_pick)
        pick_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 6))
        mode = tk.Frame(pick_card.body, bg=PANEL_BG)
        mode.pack(fill="x", pady=(0, 7))
        tk.Label(mode, text="Confirmation", bg=PANEL_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=(0, 8))
        NeoChip(mode, "Normal", "normal", self.var_pick_mode, width=74).pack(side="left", padx=(0, 6))
        NeoChip(mode, "Instant", "instant", self.var_pick_mode, width=74).pack(side="left")
        self.pick_editor = PriorityListEditor(
            pick_card.body, self.champion_data, self._on_pick_change,
            initial=self.live_config.get("pick_priority", []), allow_bravery=True,
        )
        self.pick_editor.pack(fill="both", expand=True)
        hover_card = Card(page, "Hover", toggle_var=self.var_hover)
        hover_card.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=(6, 0))
        self.hover_picker = SingleChampionPicker(
            hover_card.body, self.champion_data, self._on_hover_change,
            initial=self.live_config.get("hover_champion", ""),
        )
        self.hover_picker.pack(fill="x")
        favorites_card = Card(page, "Favorites", toggle_var=self.var_fav_expanded)
        favorites_card.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=(6, 0))
        self.favorites_panel = FavoritesPanel(
            favorites_card.body, self.champion_data, self._on_favorites_change,
            self._on_quick_action, initial=self.live_config.get("favorites", []),
        )
        self.favorites_panel.pack(fill="both", expand=True)

    def _build_automation(self, page):
        page.grid_columnconfigure(0, weight=1, uniform="automation")
        page.grid_columnconfigure(1, weight=1, uniform="automation")
        accept_card = Card(page, "Match Accept", toggle_var=self.var_accept)
        accept_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 6))
        self._toggle_row(accept_card.body, "Instant accept", "Skip the configured delay", self.var_accept_instant)
        accept_delay = self._number_row(
            accept_card.body, "Delay", self.var_accept_delay_ms, 0, 10000, 100, "ms"
        )
        def sync_accept_delay(*_):
            if self.var_accept_instant.get():
                accept_delay.pack_forget()
            elif not accept_delay.winfo_ismapped():
                accept_delay.pack(fill="x", pady=(8, 0))
        self.var_accept_instant.trace_add("write", sync_accept_delay)
        sync_accept_delay()
        requeue_card = Card(page, "Auto Re-queue", toggle_var=self.var_auto_requeue)
        requeue_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 6))
        tk.Label(requeue_card.body,
                 text="Automatically starts matchmaking after joining a lobby.",
                 bg=PANEL_BG, fg=TEXT_MUTED, justify="left", anchor="w", wraplength=360,
                 font=("Segoe UI", 9)).pack(fill="x")
        message_card = Card(page, "Champ Select Message", toggle_var=self.var_lobby_message_enabled)
        message_card.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=6)
        tk.Label(message_card.body, text="Sent once when champ select chat becomes available.",
                 bg=PANEL_BG, fg=TEXT_MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 8))
        self._entry(message_card.body, self.var_lobby_message_text).pack(fill="x", ipady=5)
        timing_card = Card(page, "Pick and Ban Timing")
        timing_card.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=6)
        self._number_row(timing_card.body, "Confirm delay", self.var_delay_ms,
                         MIN_DELAY_MS, 15000, 50, "ms").pack(fill="x")
        tk.Label(timing_card.body,
                 text="Normal mode waits this long while preserving a one-second safety margin before phase end.",
                 bg=PANEL_BG, fg=TEXT_MUTED, justify="left", anchor="w", wraplength=360,
                 font=("Segoe UI", 9)).pack(fill="x", pady=(10, 0))
        background_card = Card(page, "Background Mode", toggle_var=self.var_minimize_to_tray)
        background_card.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        note = (
            f"Closing the window sends {APP_TITLE} to the notification tray. Use Show or Quit from its menu."
            if tray_available() else
            "Tray support is unavailable because pystray or Pillow could not be loaded."
        )
        tk.Label(background_card.body, text=note, bg=PANEL_BG, fg=TEXT_MUTED,
                 justify="left", anchor="w", font=("Segoe UI", 9)).pack(fill="x")

        update_card = Card(
            page, "Updates", toggle_var=self.var_auto_update,
            on_toggle=self._on_auto_update_toggle,
        )
        update_card.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        update_row = tk.Frame(update_card.body, bg=PANEL_BG)
        update_row.pack(fill="x")
        tk.Label(update_row, textvariable=self.update_status_var, bg=PANEL_BG,
                 fg=TEXT_MUTED, justify="left", anchor="w", wraplength=490,
                 font=("Segoe UI", 9)).pack(side="left", fill="x", expand=True)
        self.update_install_button = MiniButton(
            update_row, "Install", command=self._install_available_update,
            width=9, state="disabled",
        )
        self.update_install_button.pack(side="right", padx=(8, 0))
        self.update_check_button = MiniButton(
            update_row, "Check now", command=lambda: self._check_for_updates(manual=True), width=10,
        )
        self.update_check_button.pack(side="right", padx=(8, 0))

    def _build_notifications(self, page):
        card = Card(page, "Phone Notifications", toggle_var=self.var_push_enabled)
        card.pack(fill="both", expand=True)
        tk.Label(card.body,
                 text="Install ntfy on your phone and subscribe to the private topic below. No account is required.",
                 bg=PANEL_BG, fg=TEXT_MUTED, justify="left", anchor="w",
                 font=("Segoe UI", 9)).pack(fill="x", pady=(0, 12))
        tk.Label(card.body, text="Private topic", bg=PANEL_BG, fg=TEXT_PRIMARY,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        controls = tk.Frame(card.body, bg=PANEL_BG)
        controls.pack(fill="x", pady=(6, 16))
        self._entry(controls, self.var_push_topic).pack(side="left", fill="x", expand=True, ipady=5)
        MiniButton(controls, "New", command=self._regenerate_push_topic, width=6).pack(side="left", padx=(8, 4))
        MiniButton(controls, "Copy", command=self._copy_push_topic, width=6).pack(side="left", padx=4)
        MiniButton(controls, "Test", command=self._test_push_notification, width=6).pack(side="left", padx=(4, 0))
        for key, label, description in (
            ("accept", "Match accepted", "Ready check was accepted automatically"),
            ("ban", "Champion banned", "Configured ban action completed"),
            ("dodge", "Lobby dodge", "Champ select ended before the game"),
            ("game_loaded", "Game loaded", "Live Client API confirms the game started"),
            ("game_end", "Game finished", "Your placement, KDA and damage when a game ends"),
        ):
            self._toggle_row(card.body, label, description, self.push_event_vars[key],
                             command=lambda event_key=key: self._on_push_event_change(event_key))

    def _build_status(self, page):
        card = Card(page, "League Custom Status")
        card.pack(fill="both", expand=True)

        state_row = tk.Frame(card.body, bg=PANEL_BG)
        state_row.pack(fill="x", pady=(0, 12))
        tk.Label(state_row, text="LEAGUE VALUE", bg=PANEL_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        tk.Label(state_row, textvariable=self.custom_status_info_var, bg=PANEL_BG,
                 fg=ACCENT_ALT, font=("Segoe UI", 9, "bold")).pack(side="right")

        editor_border = tk.Frame(card.body, bg=PANEL_BORDER)
        editor_border.pack(fill="both", expand=True)
        self.custom_status_editor = scrolledtext.ScrolledText(
            editor_border, wrap="word", undo=True, font=("Segoe UI", 10),
            bg=FIELD_BG, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
            selectbackground=ACCENT_DARK, relief="flat", bd=0, padx=12, pady=10,
        )
        self.custom_status_editor.pack(fill="both", expand=True, padx=1, pady=1)
        self.custom_status_editor.insert("1.0", self.live_config.get("custom_status_text", ""))
        self.custom_status_editor.bind("<KeyRelease>", self._on_custom_status_edit)
        self.custom_status_editor.bind("<<Paste>>", self._defer_custom_status_edit)
        self.custom_status_editor.bind("<<Cut>>", self._defer_custom_status_edit)

        controls = tk.Frame(card.body, bg=PANEL_BG)
        controls.pack(fill="x", pady=(12, 0))
        tk.Label(controls, textvariable=self.custom_status_count_var, bg=PANEL_BG,
                 fg=TEXT_MUTED, font=("Segoe UI", 8)).pack(side="left")
        MiniButton(controls, "Load current", width=12,
                   command=self._load_custom_status).pack(side="right", padx=(6, 0))
        MiniButton(controls, "Clear", width=7,
                   command=self._clear_custom_status).pack(side="right", padx=(6, 0))
        MiniButton(controls, "Set status", width=10,
                   command=self._apply_custom_status).pack(side="right")
        self._sync_custom_status_draft()

    def _build_activity(self, page):
        toolbar = tk.Frame(page, bg=BG)
        toolbar.pack(fill="x", pady=(0, 8))
        tk.Label(toolbar, text="RUNTIME LOG", bg=BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        MiniButton(toolbar, "Clear", width=7, command=self._clear_log).pack(side="right")
        wrapper = tk.Frame(page, bg=PANEL_BORDER)
        wrapper.pack(fill="both", expand=True)
        self.log_box = scrolledtext.ScrolledText(
            wrapper, state="disabled", font=("Cascadia Mono", 9), bg=FIELD_BG,
            fg="#cbd5e1", insertbackground=TEXT_PRIMARY, relief="flat", bd=0,
            padx=12, pady=10,
        )
        self.log_box.pack(fill="both", expand=True, padx=1, pady=1)
        self.log_box.tag_configure("ts", foreground=ACCENT_ALT)

    def _metric(self, parent, column, label, value_var, accent, last=False):
        card = tk.Frame(parent, bg=PANEL_BG, highlightthickness=1, highlightbackground=PANEL_BORDER)
        card.grid(row=0, column=column, sticky="ew",
                  padx=(0 if column == 0 else 6, 0 if last else 6))
        tk.Frame(card, bg=accent, width=4).pack(side="left", fill="y")
        body = tk.Frame(card, bg=PANEL_BG)
        body.pack(side="left", fill="both", expand=True, padx=14, pady=11)
        tk.Label(body, text=label, bg=PANEL_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w")
        value = tk.Label(body, textvariable=value_var, bg=PANEL_BG, fg=TEXT_PRIMARY,
                         font=("Segoe UI", 11, "bold"))
        value.pack(anchor="w", pady=(2, 0))
        return value

    def _toggle_row(self, parent, title, description, variable, command=None):
        row = tk.Frame(parent, bg=PANEL_BG)
        row.pack(fill="x", pady=5)
        text = tk.Frame(row, bg=PANEL_BG)
        text.pack(side="left", fill="x", expand=True)
        tk.Label(text, text=title, bg=PANEL_BG, fg=TEXT_PRIMARY,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Label(text, text=description, bg=PANEL_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 8)).pack(anchor="w")
        NeoToggle(row, variable, command=command).pack(side="right", padx=(12, 0))
        return row

    def _info_row(self, parent, label, variable, suffix=""):
        row = tk.Frame(parent, bg=PANEL_BG)
        row.pack(fill="x", pady=7)
        tk.Label(row, text=label, bg=PANEL_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        value = tk.Label(row, bg=PANEL_ALT, fg=TEXT_PRIMARY, padx=10, pady=4,
                         font=("Segoe UI", 9, "bold"))
        value.pack(side="right")
        def refresh(*_):
            try:
                current = variable.get()
            except tk.TclError:
                current = "-"
            value.configure(text=f"{current}{suffix}")
        variable.trace_add("write", refresh)
        refresh()

    def _number_row(self, parent, label, variable, minimum, maximum, increment, suffix):
        row = tk.Frame(parent, bg=PANEL_BG)
        tk.Label(row, text=label, bg=PANEL_BG, fg=TEXT_PRIMARY,
                 font=("Segoe UI", 9, "bold")).pack(side="left")
        tk.Label(row, text=suffix, bg=PANEL_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 8)).pack(side="right")
        tk.Spinbox(
            row, from_=minimum, to=maximum, increment=increment, textvariable=variable,
            width=8, bg=FIELD_BG, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
            buttonbackground=PANEL_ALT, relief="flat", bd=0, justify="center",
            highlightthickness=1, highlightbackground=PANEL_BORDER, highlightcolor=ACCENT,
            font=("Segoe UI", 9),
        ).pack(side="right", padx=(8, 5), ipady=3)
        return row

    def _entry(self, parent, variable):
        return tk.Entry(
            parent, textvariable=variable, bg=FIELD_BG, fg=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY, relief="flat", bd=0,
            highlightthickness=1, highlightbackground=PANEL_BORDER,
            highlightcolor=ACCENT, font=("Segoe UI", 9),
        )

    def _on_ban_change(self, names):
        self.live_config["ban_priority"] = names
        self._schedule_save()
        self._on_setting_changed("ban_priority")

    def _on_pick_change(self, names):
        self.live_config["pick_priority"] = names
        self._schedule_save()
        self._on_setting_changed("pick_priority")

    def _on_hover_change(self, name):
        self.live_config["hover_champion"] = name
        self._schedule_save()
        self._on_setting_changed("hover_champion")

    def _on_favorites_change(self, names):
        self.live_config["favorites"] = names
        self._schedule_save()

    def _on_quick_action(self, action, name):
        if action == "ban":
            self.ban_editor.add_champion(name, to_front=True)
        elif action == "pick":
            self.pick_editor.add_champion(name, to_front=True)
        elif action == "hover":
            self.hover_picker.choose(name)
        self.log(f"Favorites -> {action.title()}: {name}")

    def _on_push_event_change(self, key):
        events = dict(self.live_config.get("push_events", {}))
        events[key] = bool(self.push_event_vars[key].get())
        self.live_config["push_events"] = events
        self._schedule_save()
        self._on_setting_changed("push_events")

    def _regenerate_push_topic(self):
        topic = generate_push_topic()
        self.var_push_topic.set(topic)
        self.log(f"New notification topic: {topic}")

    def _copy_push_topic(self):
        topic = self.var_push_topic.get().strip()
        if not topic:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(topic)
        self.log("Notification topic copied to clipboard.")

    def _test_push_notification(self):
        if not self.var_push_topic.get().strip():
            self.log("No notification topic configured.")
            return
        if not self.var_push_enabled.get():
            self.log("Enable notifications before sending a test.")
            return
        if self.bot.notifier.send_test():
            self.log("Test notification sent.")

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", tk.END)
        self.log_box.configure(state="disabled")
