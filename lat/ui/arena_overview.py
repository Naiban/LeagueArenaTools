"""Arena overview (Stats button): placement statistics of the loaded games."""

import tkinter as tk

from lat.history.data import CHAMPION_ICON_URL, compute_arena_stats, _thousands
from lat.ui.theme import (
    ACCENT_ALT, PANEL_ALT, PANEL_BG, PANEL_BORDER, SUCCESS, TEXT_MUTED, TEXT_PRIMARY, WARNING,
)


class ArenaOverviewMixin:
    """Part of App (lat/ui/app.py); App.__init__ creates the state these
    methods use."""

    def _show_history_stats(self):
        """Toolbar "Stats": Arena overview of all loaded games."""
        self.history_list.selection_clear(0, tk.END)
        self._selected_game_id = None
        if not self._history_loaded_once and not self._history_loading:
            self._load_history()        # the overview renders when it finishes
        self._render_history_stats()

    def _render_history_stats(self):
        host = self.history_detail_inner
        for child in host.winfo_children():
            child.destroy()
        self._hide_tooltip()
        self._elo_cell_vars = {}
        self._current_detail_view = None
        self._history_detail_mode = "stats"
        self._history_token += 1        # a running rating lookup stops updating
        box = tk.Frame(host, bg=PANEL_BG)
        box.pack(fill="both", expand=True, padx=12, pady=14)
        header = tk.Frame(box, bg=PANEL_BG)
        header.pack(fill="x")
        tk.Label(header, text="Arena overview", bg=PANEL_BG, fg=TEXT_PRIMARY,
                 font=("Segoe UI", 17, "bold")).pack(side="left")
        stats = compute_arena_stats(self._all_summaries)
        if stats is None:
            text = ("No Arena games in the loaded history yet - play some Arena "
                    "or load older games." if self._history_loaded_once
                    else "Refresh to load your match history and see Arena statistics.")
            tk.Label(box, text=text, bg=PANEL_BG, fg=TEXT_MUTED, font=("Segoe UI", 10),
                     justify="left", wraplength=480).pack(anchor="w", pady=(12, 0))
            self._refresh_history_scroll()
            return
        tk.Label(header, text=f"last {stats['games']} Arena games", bg=PANEL_BG,
                 fg=TEXT_MUTED, font=("Segoe UI", 9)).pack(side="left", padx=(12, 0),
                                                           pady=(9, 0))

        metrics = tk.Frame(box, bg=PANEL_BG)
        metrics.pack(fill="x", pady=(12, 0))
        first_rate = round(100 * stats["firsts"] / stats["games"])
        tiles = (
            ("AVG PLACE", f"{stats['avg_placement']:.2f}", ACCENT_ALT),
            ("TOP HALF", f"{round(100 * stats['win_rate'])}%", SUCCESS),
            ("1ST PLACES", f"{stats['firsts']}  ({first_rate}%)", WARNING),
            ("KDA", f"{stats['kda']:.2f}", TEXT_PRIMARY),
            ("AVG DAMAGE", _thousands(stats["avg_damage"]) if stats["avg_damage"] else "-",
             TEXT_PRIMARY),
        )
        for column, (label, value, color) in enumerate(tiles):
            metrics.grid_columnconfigure(column, weight=1, uniform="tiles")
            tile = tk.Frame(metrics, bg=PANEL_ALT, highlightthickness=1,
                            highlightbackground=PANEL_BORDER)
            tile.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 6, 0))
            tk.Label(tile, text=label, bg=PANEL_ALT, fg=TEXT_MUTED,
                     font=("Segoe UI", 7, "bold")).pack(anchor="w", padx=8, pady=(6, 0))
            tk.Label(tile, text=value, bg=PANEL_ALT, fg=color,
                     font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=8, pady=(0, 6))

        tk.Label(box, text="RECENT FORM  (newest first)", bg=PANEL_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w", pady=(14, 2))
        form = tk.Frame(box, bg=PANEL_BG)
        form.pack(anchor="w")
        for placement, win in stats["recent"]:
            color = WARNING if placement == 1 else (SUCCESS if win else TEXT_MUTED)
            tk.Label(form, text=f"#{placement}", bg=PANEL_ALT, fg=color, width=3,
                     font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 3))

        champion_rows = []
        for row in stats["champions"][:12]:
            cid = row["champion_id"]
            champion_rows.append((
                {"name": self.champion_data.label(cid) if cid else "Unknown",
                 "urls": [CHAMPION_ICON_URL.format(cid)] if cid else [], "tooltip": ""},
                str(row["games"]), f"{row['avg_placement']:.2f}",
                f"{round(100 * row['win_rate'])}%", str(row["firsts"])))
        self._stats_table(box, "CHAMPIONS", ("CHAMPION", "GAMES", "AVG", "TOP HALF", "1ST"),
                          champion_rows)

        augments = stats["augments"]
        if augments:
            best = augments[:6]
            worst = [row for row in augments[::-1][:4] if row not in best]

            def augment_rows(rows):
                return [(self._resolve_entry(row["augment_id"], "augment"),
                         str(row["games"]), f"{row['avg_placement']:.2f}",
                         f"{round(100 * row['win_rate'])}%", str(row["firsts"]))
                        for row in rows]

            columns = ("AUGMENT", "GAMES", "AVG", "TOP HALF", "1ST")
            self._stats_table(box, "BEST AUGMENTS  (taken 2+ times)", columns,
                              augment_rows(best))
            if worst:
                self._stats_table(box, "WEAKEST AUGMENTS", columns, augment_rows(worst))
        tk.Label(box, text=("Top half = your team finished in the upper half (Arena's win). "
                            "Based on the loaded games - load older games for more."),
                 bg=PANEL_BG, fg=TEXT_MUTED, font=("Segoe UI", 8), justify="left",
                 wraplength=520).pack(anchor="w", pady=(14, 0))
        self._refresh_history_scroll()

    def _stats_table(self, parent, title, columns, rows):
        """Overview table: the first cell of a row is an entry (name, icon
        URLs, tooltip), the others plain text."""
        tk.Label(parent, text=title, bg=PANEL_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w", pady=(14, 2))
        table = tk.Frame(parent, bg=PANEL_BG)
        table.pack(fill="x")
        for column, (heading, width) in enumerate(zip(columns, (170, 50, 50, 64, 34))):
            table.grid_columnconfigure(column, minsize=width, weight=1 if column == 0 else 0)
            tk.Label(table, text=heading, bg=PANEL_BG, fg=TEXT_MUTED, anchor="w",
                     font=("Segoe UI", 7, "bold")).grid(row=0, column=column, sticky="ew")
        for row_index, (entry, *cells) in enumerate(rows, 1):
            name = tk.Label(table, text=" " + entry["name"], bg=PANEL_BG, fg=TEXT_PRIMARY,
                            font=("Segoe UI", 9), anchor="w", compound="left")
            name.grid(row=row_index, column=0, sticky="ew", pady=1)
            if entry.get("tooltip"):
                name.bind("<Enter>", lambda _e, w=name, e=entry: self._show_tooltip(
                    w, f"{e['name']}\n\n{e['tooltip']}"))
                name.bind("<Leave>", self._hide_tooltip)

            def apply_icon(photo, label=name):
                if photo is not None:
                    try:
                        label.configure(image=photo)
                        label.image = photo
                    except tk.TclError:
                        pass            # overview re-rendered meanwhile

            self._icon_for(entry.get("urls"), 11, apply_icon)
            for column, text in enumerate(cells, 1):
                tk.Label(table, text=text, bg=PANEL_BG,
                         fg=TEXT_PRIMARY if column == 2 else TEXT_MUTED,
                         font=("Segoe UI", 9), anchor="w").grid(
                    row=row_index, column=column, sticky="ew")
