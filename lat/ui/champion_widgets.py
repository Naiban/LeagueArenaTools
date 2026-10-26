"""Champion selection widgets (search + list, alphabetical, filtered)."""

import tkinter as tk

from lat.constants import BRAVERY_LABEL
from lat.lcu import ChampionData, _normalize
from lat.ui.theme import (
    ACCENT, ACCENT_ALT, DANGER, FIELD_BG, PANEL_ALT, PANEL_BG, PANEL_BORDER, TEXT_MUTED,
    TEXT_PRIMARY, WIN_W,
)
from lat.ui.widgets import MiniButton, style_listbox


class PriorityListEditor(tk.Frame):
    """Champion search + priority list. Enter/double-click adds,
    dragging between lists adds/reorders/removes."""

    DRAG_THRESHOLD = 6

    def __init__(self, parent, champion_data: ChampionData, on_change, initial=None, bg=PANEL_BG, allow_bravery=False):
        super().__init__(parent, bg=bg)
        self.champion_data = champion_data
        self.on_change = on_change
        self.items = list(initial or [])
        self.allow_bravery = allow_bravery

        self._drag_source = None
        self._drag_item = None
        self._drag_item_index = None
        self._drag_start_xy = (0, 0)

        self.badge_var = tk.StringVar()
        self.badge = tk.Label(self, textvariable=self.badge_var, bg=PANEL_ALT, fg=ACCENT_ALT,
                               font=("Segoe UI", 9, "bold"), anchor="w", padx=10, pady=5,
                               wraplength=WIN_W - 80, justify="left")
        self.badge.pack(fill="x", pady=(0, 6))

        search_row = tk.Frame(self, bg=bg)
        search_row.pack(fill="x", pady=(0, 4))
        self.search_var = tk.StringVar()
        entry = tk.Entry(search_row, textvariable=self.search_var, bg=FIELD_BG, fg=TEXT_PRIMARY,
                          insertbackground=TEXT_PRIMARY, relief="flat", bd=0,
                          highlightthickness=1, highlightbackground=PANEL_BORDER, highlightcolor=ACCENT,
                          font=("Segoe UI", 9))
        entry.pack(side="left", fill="x", expand=True, ipady=4)
        entry.bind("<KeyRelease>", self._refresh_filtered)
        entry.bind("<Return>", self._add_first_filtered)
        entry.bind("<Down>", self._focus_filtered)

        lists_row = tk.Frame(self, bg=bg)
        lists_row.pack(fill="x")

        left_col = tk.Frame(lists_row, bg=bg)
        left_col.pack(side="left", fill="both", expand=True, padx=(0, 4))
        tk.Label(left_col, text="ALL CHAMPIONS", bg=bg, fg=TEXT_MUTED, font=("Segoe UI", 7, "bold")).pack(anchor="w")
        self.filtered_box = tk.Listbox(left_col, height=4, exportselection=False)
        style_listbox(self.filtered_box)
        self.filtered_box.pack(fill="x")
        self.filtered_box.bind("<Double-Button-1>", self._add_selected_filtered)
        self.filtered_box.bind("<Return>", self._add_selected_filtered)
        self.filtered_box.bind("<ButtonPress-1>", lambda e: self._start_drag(e, "filtered"))
        self.filtered_box.bind("<ButtonRelease-1>", self._on_drag_release)

        right_col = tk.Frame(lists_row, bg=bg)
        right_col.pack(side="left", fill="both", expand=True, padx=(4, 0))
        tk.Label(right_col, text="PRIORITY", bg=bg, fg=TEXT_MUTED, font=("Segoe UI", 7, "bold")).pack(anchor="w")
        self.order_box = tk.Listbox(right_col, height=4, exportselection=False)
        style_listbox(self.order_box)
        self.order_box.pack(fill="x")
        self.order_box.bind("<ButtonPress-1>", lambda e: self._start_drag(e, "order"))
        self.order_box.bind("<ButtonRelease-1>", self._on_drag_release)

        btn_row = tk.Frame(self, bg=bg)
        btn_row.pack(fill="x", pady=(4, 0))
        MiniButton(btn_row, "^", command=self._move_up).pack(side="left")
        MiniButton(btn_row, "v", command=self._move_down).pack(side="left", padx=4)
        MiniButton(btn_row, "Remove", width=7, command=self._remove_selected).pack(side="left")
        if self.allow_bravery:
            MiniButton(btn_row, "+ Bravery", width=9, command=lambda: self.add_champion(BRAVERY_LABEL)).pack(side="left", padx=(6, 0))

        self._refresh_filtered()
        self._refresh_order()

    def refresh_champion_source(self):
        self._refresh_filtered()

    def add_champion(self, name, to_front=False):
        self._insert_champion(name, 0 if to_front else len(self.items))

    def set_list(self, names):
        self.items = list(names)
        self._refresh_order()

    def get_list(self):
        return list(self.items)

    def _refresh_filtered(self, event=None):
        query = _normalize(self.search_var.get())
        names = [n for n in self.champion_data.names_sorted if query in _normalize(n)]
        self.filtered_box.delete(0, tk.END)
        for n in names:
            self.filtered_box.insert(tk.END, n)

    def _focus_filtered(self, event=None):
        if self.filtered_box.size() > 0:
            self.filtered_box.selection_clear(0, tk.END)
            self.filtered_box.selection_set(0)
            self.filtered_box.activate(0)
            self.filtered_box.focus_set()
        return "break"

    def _add_first_filtered(self, event=None):
        sel = self.filtered_box.curselection()
        if sel:
            name = self.filtered_box.get(sel[0])
        elif self.filtered_box.size() > 0:
            name = self.filtered_box.get(0)
        else:
            return
        self.add_champion(name)
        self.search_var.set("")
        self._refresh_filtered()

    def _add_selected_filtered(self, event=None):
        sel = self.filtered_box.curselection()
        if sel:
            self.add_champion(self.filtered_box.get(sel[0]))

    def _refresh_order(self):
        self.order_box.delete(0, tk.END)
        for i, n in enumerate(self.items, 1):
            self.order_box.insert(tk.END, f"{i}. {n}")
        self.badge_var.set("> " + " -> ".join(self.items) if self.items else "> random (no priority set)")

    def _remove_selected(self):
        sel = self.order_box.curselection()
        if sel:
            self._remove_index(sel[0])

    def _move_up(self):
        sel = self.order_box.curselection()
        if sel and sel[0] > 0:
            self._reorder_champion(sel[0], sel[0] - 1)

    def _move_down(self):
        sel = self.order_box.curselection()
        if sel and sel[0] < len(self.items) - 1:
            self._reorder_champion(sel[0], sel[0] + 1)

    def _insert_champion(self, name, idx):
        if not name:
            return
        if _normalize(name) == _normalize(BRAVERY_LABEL):
            name = BRAVERY_LABEL
        if name in self.items:
            self.items.remove(name)
        idx = max(0, min(idx, len(self.items)))
        self.items.insert(idx, name)
        self._refresh_order()
        self.on_change(list(self.items))

    def _reorder_champion(self, old_idx, new_idx):
        if old_idx == new_idx or not (0 <= old_idx < len(self.items)):
            return
        name = self.items.pop(old_idx)
        new_idx = max(0, min(new_idx, len(self.items)))
        self.items.insert(new_idx, name)
        self._refresh_order()
        self.order_box.selection_set(new_idx)
        self.on_change(list(self.items))

    def _remove_index(self, idx):
        if 0 <= idx < len(self.items):
            del self.items[idx]
            self._refresh_order()
            self.on_change(list(self.items))

    def _start_drag(self, event, source):
        self._drag_source = source
        self._drag_start_xy = (event.x_root, event.y_root)
        if source == "filtered":
            idx = self.filtered_box.nearest(event.y)
            self._drag_item = self.filtered_box.get(idx) if 0 <= idx < self.filtered_box.size() else None
            self._drag_item_index = None
        else:
            idx = self.order_box.nearest(event.y)
            self._drag_item = self.items[idx] if 0 <= idx < len(self.items) else None
            self._drag_item_index = idx

    def _on_drag_release(self, event):
        source, item, item_index = self._drag_source, self._drag_item, self._drag_item_index
        self._drag_source = self._drag_item = self._drag_item_index = None
        if not item or not source:
            return

        dx = abs(event.x_root - self._drag_start_xy[0])
        dy = abs(event.y_root - self._drag_start_xy[1])
        moved_enough = dx > self.DRAG_THRESHOLD or dy > self.DRAG_THRESHOLD

        target = self.winfo_containing(event.x_root, event.y_root)
        landed_on_order = target is self.order_box

        if source == "filtered":
            if landed_on_order:
                local_y = event.y_root - self.order_box.winfo_rooty()
                idx = self.order_box.nearest(local_y) if self.order_box.size() > 0 else 0
                self._insert_champion(item, idx)
        elif source == "order":
            if landed_on_order and moved_enough:
                local_y = event.y_root - self.order_box.winfo_rooty()
                new_idx = self.order_box.nearest(local_y) if self.order_box.size() > 0 else 0
                self._reorder_champion(item_index, new_idx)
            elif not landed_on_order and moved_enough:
                self._remove_index(item_index)


class SingleChampionPicker(tk.Frame):
    """Search + pick a SINGLE champion (Hover)."""

    def __init__(self, parent, champion_data: ChampionData, on_change, initial="", bg=PANEL_BG):
        super().__init__(parent, bg=bg)
        self.champion_data = champion_data
        self.on_change = on_change
        self.selected = initial

        self.selected_var = tk.StringVar(value=(f"> {initial}" if initial else "> none"))
        self.badge = tk.Label(self, textvariable=self.selected_var, bg=PANEL_ALT, fg=ACCENT_ALT,
                               font=("Segoe UI", 9, "bold"), anchor="w", padx=10, pady=5,
                               wraplength=WIN_W - 80, justify="left")
        self.badge.pack(fill="x", pady=(0, 6))

        row = tk.Frame(self, bg=bg)
        row.pack(fill="x")
        self.search_var = tk.StringVar()
        entry = tk.Entry(row, textvariable=self.search_var, bg=FIELD_BG, fg=TEXT_PRIMARY,
                          insertbackground=TEXT_PRIMARY, relief="flat", bd=0,
                          highlightthickness=1, highlightbackground=PANEL_BORDER, highlightcolor=ACCENT,
                          font=("Segoe UI", 9))
        entry.pack(side="left", fill="x", expand=True, ipady=4)
        entry.bind("<KeyRelease>", self._refresh_filtered)
        entry.bind("<Return>", self._select_first)
        entry.bind("<Down>", self._focus_filtered)
        MiniButton(row, "Clear", width=8, command=self._clear).pack(side="left", padx=(6, 0))

        self.filtered_box = tk.Listbox(self, height=4, exportselection=False)
        style_listbox(self.filtered_box)
        self.filtered_box.pack(fill="x", pady=(4, 0))
        self.filtered_box.bind("<Double-Button-1>", self._select_clicked)
        self.filtered_box.bind("<Return>", self._select_clicked)

        self._refresh_filtered()

    def set_value(self, name):
        self.selected = name
        self.selected_var.set(f"> {name}" if name else "> none")

    def choose(self, name):
        self.set_value(name)
        self.on_change(name)

    def refresh_champion_source(self):
        self._refresh_filtered()

    def _refresh_filtered(self, event=None):
        query = _normalize(self.search_var.get())
        names = [n for n in self.champion_data.names_sorted if query in _normalize(n)]
        self.filtered_box.delete(0, tk.END)
        for n in names:
            self.filtered_box.insert(tk.END, n)

    def _focus_filtered(self, event=None):
        if self.filtered_box.size() > 0:
            self.filtered_box.selection_clear(0, tk.END)
            self.filtered_box.selection_set(0)
            self.filtered_box.activate(0)
            self.filtered_box.focus_set()
        return "break"

    def _select_first(self, event=None):
        sel = self.filtered_box.curselection()
        if sel:
            self.choose(self.filtered_box.get(sel[0]))
        elif self.filtered_box.size() > 0:
            self.choose(self.filtered_box.get(0))

    def _select_clicked(self, event=None):
        sel = self.filtered_box.curselection()
        if sel:
            self.choose(self.filtered_box.get(sel[0]))

    def _clear(self):
        self.choose("")


class FavoritesPanel(tk.Frame):
    """Favorite champions with quick Ban / Pick / Hover buttons."""

    def __init__(self, parent, champion_data: ChampionData, on_change, on_quick_action, initial=None, bg=PANEL_BG):
        super().__init__(parent, bg=bg)
        self.champion_data = champion_data
        self.on_change = on_change
        self.on_quick_action = on_quick_action
        self.favorites = list(initial or [])
        self.bg = bg

        add_row = tk.Frame(self, bg=bg)
        add_row.pack(fill="x")
        self.search_var = tk.StringVar()
        entry = tk.Entry(add_row, textvariable=self.search_var, bg=FIELD_BG, fg=TEXT_PRIMARY,
                          insertbackground=TEXT_PRIMARY, relief="flat", bd=0,
                          highlightthickness=1, highlightbackground=PANEL_BORDER, highlightcolor=ACCENT,
                          font=("Segoe UI", 9))
        entry.pack(side="left", fill="x", expand=True, ipady=4)
        entry.bind("<KeyRelease>", self._refresh_filtered)
        entry.bind("<Return>", self._add_first)
        MiniButton(add_row, "+ Add", width=6, command=self._add_first).pack(side="left", padx=(6, 0))

        self.filtered_box = tk.Listbox(self, height=3, exportselection=False)
        style_listbox(self.filtered_box)
        self.filtered_box.pack(fill="x", pady=(4, 6))
        self.filtered_box.bind("<Double-Button-1>", self._add_clicked)

        self.rows_frame = tk.Frame(self, bg=bg)
        self.rows_frame.pack(fill="x")

        self._refresh_filtered()
        self._render_rows()

    def refresh_champion_source(self):
        self._refresh_filtered()

    def set_list(self, names):
        self.favorites = list(names)
        self._render_rows()
        self._refresh_filtered()

    def _refresh_filtered(self, event=None):
        query = _normalize(self.search_var.get())
        names = [n for n in self.champion_data.names_sorted
                 if query in _normalize(n) and n not in self.favorites]
        self.filtered_box.delete(0, tk.END)
        for n in names:
            self.filtered_box.insert(tk.END, n)

    def _add_first(self, event=None):
        if self.filtered_box.size() > 0:
            self._add(self.filtered_box.get(0))

    def _add_clicked(self, event=None):
        sel = self.filtered_box.curselection()
        if sel:
            self._add(self.filtered_box.get(sel[0]))

    def _add(self, name):
        if name and name not in self.favorites:
            self.favorites.append(name)
            self.search_var.set("")
            self._refresh_filtered()
            self._render_rows()
            self.on_change(list(self.favorites))

    def _remove(self, name):
        if name in self.favorites:
            self.favorites.remove(name)
            self._refresh_filtered()
            self._render_rows()
            self.on_change(list(self.favorites))

    def _render_rows(self):
        for w in self.rows_frame.winfo_children():
            w.destroy()
        if not self.favorites:
            tk.Label(self.rows_frame, text="No favorites yet.",
                     bg=self.bg, fg=TEXT_MUTED, font=("Segoe UI", 8)).pack(anchor="w")
            return
        for name in self.favorites:
            row = tk.Frame(self.rows_frame, bg=PANEL_ALT)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=name, bg=PANEL_ALT, fg=TEXT_PRIMARY, font=("Segoe UI", 9),
                     width=14, anchor="w").pack(side="left", padx=(6, 0))
            MiniButton(row, "Ban", width=4, command=lambda n=name: self.on_quick_action("ban", n)).pack(side="left", padx=2)
            MiniButton(row, "Pick", width=4, command=lambda n=name: self.on_quick_action("pick", n)).pack(side="left", padx=2)
            MiniButton(row, "Hov", width=4, command=lambda n=name: self.on_quick_action("hover", n)).pack(side="left", padx=2)
            MiniButton(row, "x", width=2, fg=DANGER, command=lambda n=name: self._remove(n)).pack(side="left", padx=(6, 0))
