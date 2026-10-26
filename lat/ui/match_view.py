"""Details of one game (finished or live): header, builds, lobby table, copy
and export."""

import os
import json
import time
import tkinter as tk
from tkinter import filedialog

from lat.history.data import (
    format_rating_tooltip, is_arena_game, order_players_by_placement, QUEUE_LABELS,
    shorten_riot_id,
)
from lat.history.export import build_match_export, format_match_csv, format_match_text
from lat.ui.history_page import HISTORY_STAT_COLUMNS
from lat.ui.theme import (
    ACCENT_ALT, DANGER, PANEL_ALT, PANEL_BG, PANEL_BORDER, SUCCESS, TEXT_MUTED, TEXT_PRIMARY,
    WARNING,
)
from lat.ui.widgets import MiniButton


# Item/augment icons per line in the details panel; a full Arena build
# (7 items) wraps instead of running past the right edge.
CHIPS_PER_ROW = 6


class MatchViewMixin:
    """Part of App (lat/ui/app.py); App.__init__ creates the state these
    methods use."""

    def _toggle_player_build(self, player, _panel_holder):
        """Accordion behavior: clicking a row shows only that player's build;
        clicking it again collapses. A full re-render guarantees clean layout
        even where Tk shrinks-propagation misbehaves."""
        participant_id = player.get("participant_id")
        if self._open_build_pid == participant_id:
            self._open_build_pid = None
            self.log("History: build collapsed.")
        else:
            self._open_build_pid = participant_id
            self._hide_tooltip()
        if self._current_detail_view is not None:
            try:
                self._render_history_detail(self._current_detail_view)
            except tk.TclError:
                pass

    def _attach_player_build_if_open(self, player, holder):
        if self._open_build_pid != player.get("participant_id"):
            return
        build = tk.Frame(holder, bg="#0e1422",
                         highlightthickness=1,
                         highlightcolor=PANEL_BORDER,
                         highlightbackground=PANEL_BORDER)
        build.pack(fill="x", pady=(2, 4))
        inner = tk.Frame(build, bg="#0e1422")
        inner.pack(fill="x", padx=10, pady=8)
        items = [self._resolve_entry(item_id, "item")
                 for item_id in player.get("items", [])]
        augments = [self._resolve_entry(aug_id, "augment")
                    for aug_id in player.get("augments", [])]
        if not items and not augments:
            tk.Label(inner, text="No build data recorded.",
                     bg="#0e1422", fg=TEXT_MUTED,
                     font=("Segoe UI", 8)).pack(anchor="w")
            return
        if items:
            tk.Label(inner, text="ITEMS", bg="#0e1422", fg=TEXT_MUTED,
                     font=("Segoe UI", 7, "bold")).pack(anchor="w")
            items_row = tk.Frame(inner, bg="#0e1422")
            items_row.pack(anchor="w")
            self._build_chips_into(items_row, items)
        if augments:
            tk.Label(inner, text="AUGMENTS", bg="#0e1422", fg=TEXT_MUTED,
                     font=("Segoe UI", 7, "bold")).pack(anchor="w", pady=(6, 0))
            augments_row = tk.Frame(inner, bg="#0e1422")
            augments_row.pack(anchor="w")
            self._build_chips_into(augments_row, augments)

    def _on_stat_column_change(self, *_args):
        if self._current_detail_view is not None and not self._closing:
            try:
                self._render_history_detail(self._current_detail_view)
            except tk.TclError:
                pass

    def _render_history_detail(self, view):
        host = self.history_detail_inner
        for child in host.winfo_children():
            child.destroy()
        self._hide_tooltip()
        self._elo_cell_vars = {}
        self._history_row_holders = {}
        self._current_detail_view = view
        self._history_detail_mode = "game"
        self._history_token += 1
        token = self._history_token
        live = bool(view.get("live"))

        players = view.get("players") or []
        me = view.get("me") or {}
        box = tk.Frame(host, bg=PANEL_BG)
        box.pack(fill="both", expand=True, padx=12, pady=14)

        header = tk.Frame(box, bg=PANEL_BG)
        header.pack(fill="x")
        champ_name = self.champion_data.label(me.get("champion_id") or 0)
        tk.Label(header, text=champ_name, bg=PANEL_BG, fg=TEXT_PRIMARY,
                 font=("Segoe UI", 17, "bold")).pack(side="left")
        placement = me.get("placement")
        if placement:
            color = WARNING if placement == 1 else (
                ACCENT_ALT if placement <= 3 else TEXT_MUTED)
            tk.Label(header, text=f"#{placement}", bg=PANEL_BG, fg=color,
                     font=("Segoe UI", 17, "bold")).pack(side="left", padx=(12, 0))
        if live:
            tk.Label(header, text="LIVE", bg=PANEL_BG, fg=ACCENT_ALT,
                     font=("Segoe UI", 9, "bold")).pack(side="right")
        win = me.get("win")
        if win is not None:
            tk.Label(header, text="VICTORY" if win else "DEFEAT",
                     bg=PANEL_BG, fg=SUCCESS if win else DANGER,
                     font=("Segoe UI", 9, "bold")).pack(side="right")

        meta_bits = []
        queue_label = QUEUE_LABELS.get(view.get("queue_id")) or (
            "Arena" if live and is_arena_game(view) else None)
        if queue_label:
            meta_bits.append(queue_label)
        duration = view.get("duration_s")
        if live:
            meta_bits.append("game in progress"
                             + (f" ({int(duration // 60)}m in)"
                                if isinstance(duration, (int, float)) and duration >= 60 else ""))
        elif isinstance(duration, (int, float)) and duration > 0:
            meta_bits.append(f"{int(duration // 60)}m {int(duration % 60):02d}s")
        creation = view.get("creation")
        if creation and not live:
            meta_bits.append(time.strftime("%Y-%m-%d %H:%M", time.localtime(creation / 1000)))
        if meta_bits:
            tk.Label(header, text="   ".join(meta_bits), bg=PANEL_BG,
                     fg=TEXT_MUTED, font=("Segoe UI", 9)).pack(anchor="w")

        actions = tk.Frame(box, bg=PANEL_BG)
        actions.pack(fill="x", pady=(6, 0))
        MiniButton(actions, "Copy", width=6,
                   command=lambda: self._copy_match(view)).pack(side="left")
        MiniButton(actions, "Export...", width=8,
                   command=lambda: self._export_match(view)).pack(side="left", padx=(6, 0))

        kda_line = f"{me.get('kills', 0)} / {me.get('deaths', 0)} / {me.get('assists', 0)} KDA"
        extras = []
        if me.get("damage"):
            extras.append(f"{me['damage']:,} dmg".replace(",", " "))
        if me.get("gold"):
            extras.append(f"{me['gold']:,} gold".replace(",", " "))
        if extras:
            kda_line += "   ·   " + "   ".join(extras)
        tk.Label(box, text=kda_line, bg=PANEL_BG, fg=TEXT_PRIMARY,
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 0))

        my_items = [self._resolve_entry(i, "item") for i in me.get("items", [])]
        my_augments = [self._resolve_entry(a, "augment") for a in me.get("augments", [])]
        self._detail_section_chips(box, "MY ITEMS", my_items)
        if not live:                    # the Live Client API has no augments
            self._detail_section_chips(box, "MY AUGMENTS", my_augments)

        hint = tk.Label(box, text=("LIVE LOBBY - ratings of everyone in your game" if live
                                   else "LOBBY - click a player to see their build"),
                        bg=PANEL_BG, fg=TEXT_MUTED,
                        font=("Segoe UI", 7, "bold"))
        hint.pack(anchor="w", pady=(16, 2))

        stat_key = self.var_history_stat.get() if hasattr(self, "var_history_stat") else "damage"
        if stat_key not in HISTORY_STAT_COLUMNS:
            stat_key = "damage"
        if live:
            stat_key = "level"          # the only one of them a running game reports
        column_title, column_field = HISTORY_STAT_COLUMNS[stat_key]

        table = tk.Frame(box, bg=PANEL_BG)
        table.pack(fill="x")
        columns = ("#", "PLAYER", "CHAMPION", "K/D/A",
                   column_title, "ELO", "RANK")
        widths = (26, 116, 80, 54, 56, 40, 44)
        for column, (title, width) in enumerate(zip(columns, widths)):
            table.grid_columnconfigure(column, minsize=width, weight=1 if column == 1 else 0)
            tk.Label(table, text=title, bg=PANEL_BG, fg=TEXT_MUTED,
                     font=("Segoe UI", 7, "bold"),
                     anchor="w").grid(row=0, column=column, sticky="ew")

        # Duo teams ordered by the placement they achieved (winner on top).
        ordered = order_players_by_placement(players)
        grid_row = 1
        previous_subteam = None
        team_ordinal = 0
        for player in ordered:
            subteam = player.get("subteam_id")
            if len(ordered) > 2 and subteam != previous_subteam and any(
                    p.get("subteam_id") is not None for p in players):
                if subteam is not None or previous_subteam is not None:
                    separator = tk.Frame(table, bg=PANEL_BORDER, height=1)
                    separator.grid(row=grid_row, column=0, columnspan=len(columns),
                                   sticky="ew", pady=(3, 1))
                    grid_row += 1
                    if subteam is not None:
                        team_ordinal += 1
                        tk.Label(table, text=f"TEAM {team_ordinal}", bg=PANEL_BG,
                                 fg=TEXT_MUTED, font=("Segoe UI", 7, "bold"),
                                 anchor="w").grid(row=grid_row, column=0,
                                                  columnspan=len(columns), sticky="w")
                        grid_row += 1
            previous_subteam = subteam

            place_text = f"#{player['placement']}" if player.get("placement") else "-"
            place_color = WARNING if player.get("placement") == 1 else (
                ACCENT_ALT if (player.get("placement") or 9) <= 3 else TEXT_PRIMARY)
            full_id = (f"{player['name']}#{player['tag']}"
                       if player.get("tag") else player.get("name", "-"))
            tag_text = shorten_riot_id(player.get("name") or "", player.get("tag") or "")
            elo_var = tk.StringVar(value="...")
            rank_var = tk.StringVar(value="")
            self._elo_cell_vars[player.get("participant_id")] = (elo_var, rank_var)
            stat_value = player.get(column_field)
            if stat_key == "level":
                stat_text = str(stat_value) if stat_value else "-"
            else:
                stat_text = f"{stat_value:,}".replace(",", " ") if stat_value else "-"
            cells = (
                (place_text, place_color),
                (tag_text or "-", TEXT_PRIMARY),
                (self.champion_data.label(player.get("champion_id") or 0), TEXT_MUTED),
                (f"{player.get('kills', 0)}/{player.get('deaths', 0)}/{player.get('assists', 0)}", TEXT_MUTED),
                (stat_text, TEXT_PRIMARY),
                (None, ACCENT_ALT),
                (None, ACCENT_ALT),
            )
            row_widgets = []
            for column, (text_value, color) in enumerate(cells):
                if text_value is None:
                    variable = elo_var if column == 5 else rank_var
                    cell = tk.Label(table, textvariable=variable, bg=PANEL_BG,
                                    fg=color, font=("Segoe UI", 9))
                    cell.bind("<Enter>", lambda _e, w=cell, p=player:
                              self._show_rating_tooltip(w, p))
                    cell.bind("<Leave>", self._hide_tooltip)
                else:
                    cell = tk.Label(table, text=text_value, bg=PANEL_BG, fg=color,
                                    font=("Segoe UI", 9), cursor="hand2")
                cell.grid(row=grid_row, column=column, sticky="w")
                row_widgets.append(cell)
                if column == 1 and tag_text != full_id:
                    cell.bind("<Enter>", lambda _e, w=cell, t=full_id: self._show_tooltip(w, t))
                    cell.bind("<Leave>", self._hide_tooltip)

            holder = tk.Frame(table, bg=PANEL_BG)
            holder.grid(row=grid_row + 1, column=0, columnspan=len(columns), sticky="ew")
            self._history_row_holders[player.get("participant_id")] = holder
            self._attach_player_build_if_open(player, holder)

            def toggle(_event, p=player, h=holder):
                self._toggle_player_build(p, h)

            for widget_cell in row_widgets:
                widget_cell.bind("<Button-1>", toggle)
            if player.get("participant_id") == me.get("participant_id"):
                pass  # own row stays clickable too
            grid_row += 2
        self._refresh_history_scroll()
        self._start_elo_lookup(players, token, arena=is_arena_game(view))

    def _show_rating_tooltip(self, widget, player):
        """League, ladder position, games and best champions on hover."""
        if not is_arena_game(self._current_detail_view):
            return
        rating = self._elo_cache.get(self._elo_key(player), "miss")
        if rating == "miss":
            return                      # still loading
        self._show_tooltip(widget, format_rating_tooltip(rating))

    def _match_export(self, view):
        """Export model for a match, with names and any ratings loaded so far."""
        ratings = {}
        if is_arena_game(view):
            for player in view.get("players") or []:
                rating = self._elo_cache.get(self._elo_key(player))
                if isinstance(rating, dict):
                    ratings[player.get("participant_id")] = rating
        return build_match_export(
            view, self.champion_data.label,
            lambda kind, entry_id: self._resolve_entry(entry_id, kind)["name"],
            ratings)

    def _copy_match(self, view):
        try:
            text = format_match_text(self._match_export(view))
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except Exception as exc:
            self._set_history_status(f"Copy failed ({exc})")
            return
        self._set_history_status("Match copied to the clipboard")
        self.log(f"History: copied game {view.get('game_id')} to the clipboard.")

    def _export_match(self, view):
        export = self._match_export(view)
        stamp = (export.get("date") or "").replace(":", "").replace(" ", "_")
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Export match",
            initialfile=f"match-{export.get('game_id')}{'-' + stamp if stamp else ''}.txt",
            defaultextension=".txt",
            filetypes=(("Text summary", "*.txt"), ("CSV (one row per player)", "*.csv"),
                       ("JSON", "*.json")))
        if not path:
            return
        extension = os.path.splitext(path)[1].lower()
        if extension == ".csv":
            content = format_match_csv(export)
        elif extension == ".json":
            content = json.dumps(export, indent=2, ensure_ascii=False) + "\n"
        else:
            content = format_match_text(export)
        try:
            # utf-8-sig so Excel opens names with accents/CJK correctly.
            encoding = "utf-8-sig" if extension == ".csv" else "utf-8"
            with open(path, "w", encoding=encoding, newline="") as output:
                output.write(content)
        except OSError as exc:
            self._set_history_status(f"Export failed ({exc.strerror or exc})")
            self.log(f"History: export failed - {exc}")
            return
        self._set_history_status(f"Exported to {os.path.basename(path)}")
        self.log(f"History: exported game {view.get('game_id')} to {path}")

    def _detail_section(self, parent, title, content):
        frame = tk.Frame(parent, bg=PANEL_BG)
        frame.pack(fill="x", pady=(12, 0))
        tk.Label(frame, text=title, bg=PANEL_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w")
        tk.Label(frame, text=content, bg=PANEL_ALT, fg=TEXT_PRIMARY,
                 font=("Segoe UI", 9), justify="left", anchor="w",
                 wraplength=560).pack(fill="x", pady=(3, 0))

    def _build_chips_into(self, parent, entries):
        entries = entries[:8]
        rows = [tk.Frame(parent, bg=parent["bg"])
                for _ in range(0, len(entries), CHIPS_PER_ROW)]
        for row in rows:
            row.pack(anchor="w")
        for index, entry in enumerate(entries):
            chip = tk.Frame(rows[index // CHIPS_PER_ROW], bg=PANEL_ALT, highlightthickness=1,
                            highlightbackground=PANEL_BORDER)
            chip.pack(side="left", padx=(0, 5), pady=(2, 3))
            label = tk.Label(chip, bg=PANEL_ALT, fg=TEXT_PRIMARY,
                             font=("Segoe UI", 8), cursor="hand2")
            label.pack(padx=4, pady=3)

            def show(_event, entry=entry, widget=label):
                tooltip_text = entry.get("tooltip") or ""
                text = (f"{entry['name']}\n\n{tooltip_text}" if tooltip_text
                        else entry["name"])
                self._show_tooltip(widget, text)

            label.bind("<Enter>", show)
            label.bind("<Leave>", self._hide_tooltip)

            def apply_icon(photo, lbl=label, entry=entry):
                try:
                    if photo is not None:
                        lbl.configure(image=photo, text="")
                        lbl.image = photo
                    else:
                        short = entry.get("fallback") or entry["name"]
                        lbl.configure(text=str(short)[:14])
                except tk.TclError:
                    pass  # widget already destroyed

            self._icon_for(entry.get("urls"), 22, apply_icon)

    def _detail_section_chips(self, parent, title, entries):
        frame = tk.Frame(parent, bg=PANEL_BG)
        frame.pack(fill="x", pady=(12, 0))
        tk.Label(frame, text=title, bg=PANEL_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w")
        if entries:
            row = tk.Frame(frame, bg=PANEL_BG)
            row.pack(anchor="w")
            self._build_chips_into(row, entries)
        else:
            tk.Label(frame, text="-", bg=PANEL_ALT, fg=TEXT_MUTED,
                     font=("Segoe UI", 9)).pack(fill="x", pady=(3, 0))
