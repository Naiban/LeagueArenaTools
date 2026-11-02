"""Skin Applier page: the skins you own per champion, with splash art; the
chosen one is selected automatically in champ select (see bot Auto Skin)."""

import threading
import tkinter as tk

from lat.lcu import _normalize
from lat.skins import load_skin_catalog
from lat.ui.theme import (
    ACCENT, ACCENT_ALT, BG, FIELD_BG, PANEL_ALT, PANEL_BG, PANEL_BORDER, TEXT_MUTED,
    TEXT_PRIMARY,
)
from lat.ui.widgets import MiniButton, NeoToggle, style_listbox


SPLASH_W = 190
SPLASH_H = 107
CARD_GAP = 10


class SkinsPageMixin:
    """Part of App (lat/ui/app.py); App.__init__ creates the state these
    methods use."""

    def _build_skins(self, page):
        toolbar = tk.Frame(page, bg=BG)
        toolbar.pack(fill="x", pady=(0, 8))
        tk.Label(toolbar, text="AUTO SKIN", bg=BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=(0, 6))
        NeoToggle(toolbar, self.var_skin_apply, width=34, height=18).pack(side="left")
        tk.Label(toolbar, text="Selected for you once your champion is locked (bot running).",
                 bg=BG, fg=TEXT_MUTED, font=("Segoe UI", 8)).pack(side="left", padx=(10, 0))
        MiniButton(toolbar, "Reload", width=8,
                   command=lambda: self._load_skins(force=True)).pack(side="right")
        tk.Label(toolbar, textvariable=self.skin_status_var, bg=BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 9)).pack(side="right", padx=(0, 10))

        body = tk.Frame(page, bg=BG)
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=BG, width=200)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 10))
        left.pack_propagate(False)
        tk.Label(left, text="CHAMPIONS WITH SKINS", bg=BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w", pady=(0, 4))
        self.skin_search_var = tk.StringVar()
        search = tk.Entry(left, textvariable=self.skin_search_var, bg=FIELD_BG, fg=TEXT_PRIMARY,
                          insertbackground=TEXT_PRIMARY, relief="flat", bd=0,
                          highlightthickness=1, highlightbackground=PANEL_BORDER,
                          highlightcolor=ACCENT, font=("Segoe UI", 9))
        search.pack(fill="x", ipady=4, pady=(0, 6))
        search.bind("<KeyRelease>", lambda _e: self._refresh_skin_champion_list())
        list_wrap = tk.Frame(left, bg=BG)
        list_wrap.pack(fill="both", expand=True)
        self.skin_champion_list = tk.Listbox(list_wrap, exportselection=False)
        style_listbox(self.skin_champion_list)
        scroll = tk.Scrollbar(list_wrap, orient="vertical",
                              command=self.skin_champion_list.yview)
        self.skin_champion_list.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.skin_champion_list.pack(side="left", fill="both", expand=True)
        self.skin_champion_list.bind("<<ListboxSelect>>", self._on_skin_champion_select)

        right = tk.Frame(body, bg=PANEL_BG, highlightthickness=1,
                         highlightbackground=PANEL_BORDER)
        right.grid(row=0, column=1, sticky="nsew")
        header = tk.Frame(right, bg=PANEL_BG)
        header.pack(fill="x", padx=14, pady=(10, 6))
        self.skin_title_var = tk.StringVar(value="Choose a champion")
        self.skin_choice_var = tk.StringVar(value="")
        tk.Label(header, textvariable=self.skin_title_var, bg=PANEL_BG, fg=TEXT_PRIMARY,
                 font=("Segoe UI", 13, "bold")).pack(side="left")
        tk.Label(header, textvariable=self.skin_choice_var, bg=PANEL_BG, fg=ACCENT_ALT,
                 font=("Segoe UI", 9, "bold")).pack(side="left", padx=(12, 0))
        self.skin_clear_button = MiniButton(header, "Clear", width=7,
                                            command=lambda: self._set_skin_choice(None))
        self.skin_clear_button.pack(side="right")

        grid_wrap = tk.Frame(right, bg=PANEL_BG)
        grid_wrap.pack(fill="both", expand=True, padx=(14, 4), pady=(0, 10))
        canvas = tk.Canvas(grid_wrap, bg=PANEL_BG, highlightthickness=0)
        grid_scroll = tk.Scrollbar(grid_wrap, orient="vertical", command=canvas.yview)
        self.skin_grid = tk.Frame(canvas, bg=PANEL_BG)
        self.skin_grid.bind(
            "<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.skin_grid, anchor="nw")
        canvas.configure(yscrollcommand=grid_scroll.set)
        canvas.bind("<Configure>", self._on_skin_canvas_resize)
        grid_scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self.skin_canvas = canvas

        def route_wheel(event):
            try:
                widget = self.root.winfo_containing(event.x_root, event.y_root)
            except Exception:
                return
            while widget is not None:
                if widget is self.skin_canvas:
                    self.skin_canvas.yview_scroll(int(-event.delta / 120), "units")
                    return
                widget = getattr(widget, "master", None)

        self.root.bind_all("<MouseWheel>", route_wheel, add="+")

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _on_skins_page_shown(self):
        if self._skin_catalog is None:
            self._load_skins()

    def _load_skins(self, force=False):
        if self._skins_loading or (self._skin_catalog is not None and not force):
            return
        if not self.client.is_connected:
            self.skin_status_var.set("Start the League client, then Reload")
            return
        self._skins_loading = True
        self.skin_status_var.set("Loading skins...")

        def worker():
            if not self.champion_data.loaded:
                self.champion_data.load(self.client)
            catalog, error = load_skin_catalog(self.client)
            self._run_on_ui(lambda: finish(catalog, error))

        def finish(catalog, error):
            self._skins_loading = False
            if self._closing:
                return
            if not catalog:
                self.skin_status_var.set(f"Could not load skins: {error}")
                self.log(f"Skin Applier: {error}")
                return
            self._skin_catalog = catalog
            owned = sum(1 for skins in catalog.values() for skin in skins if not skin["is_base"])
            champions = sum(1 for skins in catalog.values() if len(skins) > 1)
            text = f"{owned} skins on {champions} champions"
            self.skin_status_var.set(f"{text} - {error}" if error else text)
            self.log(f"Skin Applier: loaded {text}.")
            self._refresh_skin_champion_list()

        threading.Thread(target=worker, name="LeagueArenaTools-Skins", daemon=True).start()

    # ------------------------------------------------------------------
    # Champion list and skin cards
    # ------------------------------------------------------------------

    def _skin_champion_name(self, champion_id):
        return self.champion_data.label(champion_id)

    def _refresh_skin_champion_list(self):
        catalog = self._skin_catalog or {}
        query = _normalize(self.skin_search_var.get())
        choices = self.live_config.get("skin_choices") or {}
        rows = sorted(
            (self._skin_champion_name(cid), cid) for cid, skins in catalog.items()
            if len(skins) > 1)
        self._skin_list_ids = []
        box = self.skin_champion_list
        box.delete(0, "end")
        for name, cid in rows:
            if query and query not in _normalize(name):
                continue
            marker = "+ " if str(cid) in choices else "   "
            box.insert("end", f"{marker}{name}  ({len(catalog[cid]) - 1})")
            self._skin_list_ids.append(cid)
        if self._skin_champion_id in self._skin_list_ids:
            index = self._skin_list_ids.index(self._skin_champion_id)
            box.selection_set(index)
            box.see(index)
        self._render_skin_cards()

    def _on_skin_champion_select(self, _event=None):
        selection = self.skin_champion_list.curselection()
        if not selection:
            return
        champion_id = self._skin_list_ids[selection[0]]
        if champion_id != self._skin_champion_id:
            self._skin_champion_id = champion_id
            self.skin_canvas.yview_moveto(0)
            self._render_skin_cards()

    def _current_skin_choice(self):
        choice = (self.live_config.get("skin_choices") or {}).get(str(self._skin_champion_id))
        return choice.get("skin_id") if choice else None

    def _render_skin_cards(self):
        for child in self.skin_grid.winfo_children():
            child.destroy()
        self._skin_cards = []
        skins = (self._skin_catalog or {}).get(self._skin_champion_id)
        if not skins:
            self.skin_title_var.set("Choose a champion")
            self.skin_choice_var.set("")
            return
        chosen = self._current_skin_choice()
        choice = (self.live_config.get("skin_choices") or {}).get(str(self._skin_champion_id))
        self.skin_title_var.set(self._skin_champion_name(self._skin_champion_id))
        self.skin_choice_var.set(f"Auto: {choice['name']}" if choice else "Auto: not set")
        for skin in skins:
            self._skin_cards.append(self._build_skin_card(skin, chosen))
        self._layout_skin_cards()

    def _build_skin_card(self, skin, chosen):
        chroma_ids = {chroma["id"] for chroma in skin["chromas"]}
        selected = chosen is not None and (chosen == skin["id"] or chosen in chroma_ids)
        card = tk.Frame(self.skin_grid, bg=PANEL_ALT, highlightthickness=2,
                        highlightbackground=ACCENT_ALT if selected else PANEL_BORDER,
                        cursor="hand2")
        splash = tk.Canvas(card, width=SPLASH_W, height=SPLASH_H, bg=FIELD_BG,
                           highlightthickness=0, cursor="hand2")
        splash.pack()
        splash.create_text(SPLASH_W / 2, SPLASH_H / 2, text="Loading...", fill=TEXT_MUTED,
                           font=("Segoe UI", 8))
        name = "Default (no change)" if skin["is_base"] else skin["name"]
        label = tk.Label(card, text=name, bg=PANEL_ALT,
                         fg=ACCENT_ALT if selected else TEXT_PRIMARY,
                         font=("Segoe UI", 9, "bold"), wraplength=SPLASH_W - 6,
                         justify="center", cursor="hand2")
        label.pack(fill="x", padx=4, pady=(5, 2))
        if skin["chromas"]:
            row = tk.Frame(card, bg=PANEL_ALT)
            row.pack(pady=(0, 4))
            for chroma in skin["chromas"]:
                dot = tk.Canvas(row, width=18, height=18, bg=PANEL_ALT, highlightthickness=0,
                                cursor="hand2")
                active = chosen == chroma["id"]
                dot.create_oval(2, 2, 16, 16, fill=chroma["color"],
                                outline="#ffffff" if active else PANEL_BORDER,
                                width=2 if active else 1)
                dot.pack(side="left", padx=1)
                dot.bind("<Button-1>", lambda _e, s=skin, c=chroma: self._on_chroma_click(s, c))
                dot.bind("<Enter>", lambda _e, w=dot, c=chroma: self._show_tooltip(w, c["name"]))
                dot.bind("<Leave>", self._hide_tooltip)
        tk.Frame(card, bg=PANEL_ALT, height=4).pack()
        for widget in (card, splash, label):
            widget.bind("<Button-1>", lambda _e, s=skin: self._on_skin_click(s))

        def show(photo, canvas=splash):
            if photo is None:
                try:
                    canvas.delete("all")
                    canvas.create_text(SPLASH_W / 2, SPLASH_H / 2, text="No image",
                                       fill=TEXT_MUTED, font=("Segoe UI", 8))
                except tk.TclError:
                    pass
                return
            try:
                canvas.delete("all")
                canvas.create_image(SPLASH_W / 2, SPLASH_H / 2, image=photo)
            except tk.TclError:
                pass                    # card was replaced in the meantime

        self._icon_for(skin["image_urls"], SPLASH_W // 2, show)
        return card

    def _on_skin_canvas_resize(self, event):
        columns = max(1, (event.width + CARD_GAP) // (SPLASH_W + 4 + CARD_GAP))
        if columns != self._skin_columns:
            self._skin_columns = columns
            self._layout_skin_cards()

    def _layout_skin_cards(self):
        columns = self._skin_columns or 1
        for index, card in enumerate(self._skin_cards):
            card.grid(row=index // columns, column=index % columns, sticky="n",
                      padx=(0, CARD_GAP), pady=(0, CARD_GAP))

    # ------------------------------------------------------------------
    # Choosing
    # ------------------------------------------------------------------

    def _on_skin_click(self, skin):
        if skin["is_base"] or self._current_skin_choice() == skin["id"]:
            self._set_skin_choice(None)          # click again: back to "no change"
        else:
            self._set_skin_choice(skin["id"], skin["name"])

    def _on_chroma_click(self, skin, chroma):
        if self._current_skin_choice() == chroma["id"]:
            self._set_skin_choice(skin["id"], skin["name"])
        else:
            # Chroma names already include the skin ("High Noon Urgot (Tanzanite)").
            self._set_skin_choice(chroma["id"], chroma["name"])

    def _set_skin_choice(self, skin_id, name=""):
        champion_id = self._skin_champion_id
        if champion_id is None:
            return
        choices = dict(self.live_config.get("skin_choices") or {})
        if skin_id is None:
            if choices.pop(str(champion_id), None) is None:
                return
        else:
            choices[str(champion_id)] = {"skin_id": skin_id, "name": name}
        self.live_config["skin_choices"] = choices
        self._schedule_save()
        self._on_setting_changed("skin_choices")
        champion = self._skin_champion_name(champion_id)
        self.log(f"Skin Applier: {champion} -> {name}" if skin_id else
                 f"Skin Applier: {champion} -> no change")
        self._refresh_skin_champion_list()

    def _refresh_skin_view(self):
        """Profile switched: show that profile's choices."""
        if self._skin_catalog is not None:
            self._refresh_skin_champion_list()
