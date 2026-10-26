"""Match History page: toolbar, game list, loading and paging, and the
item/augment names from CommunityDragon."""

import time
import threading
import tkinter as tk

from lat.history.cache import fetch_cached_json
from lat.history.data import (
    ARENA_DATA_URL, ARENASWEATS_REGIONS, cdragon_icon_candidates, CHERRY_AUGMENTS_URL,
    is_arena_game, ITEMS_DATA_URL, LIVE_GAME_ID, match_history_path, parse_history_games,
    parse_match_detail, PLATFORM_TO_AS_REGION, QUEUE_LABELS, select_local_player,
    summarize_history_game,
)
from lat.history.text import clean_game_text, expand_augment_description
from lat.ui.theme import (
    ACCENT, ACCENT_ALT, BG, FIELD_BG, PANEL_ALT, PANEL_BG, PANEL_BORDER, TEXT_MUTED,
    TEXT_PRIMARY, WARNING,
)
from lat.ui.widgets import MiniButton, NeoToggle


# Match history is loaded in pages ("Load more") up to this many games.
HISTORY_PAGE_SIZE = 50
HISTORY_MAX_GAMES = 500

# Selectable lobby-table statistic (config: history_stat_column).
HISTORY_STAT_COLUMNS = {
    "damage": ("DAMAGE", "damage"),
    "gold": ("GOLD", "gold"),
    "heal": ("HEALING", "heal"),
    "taken": ("TANKED", "taken"),
    "level": ("LEVEL", "level"),
}


class HistoryPageMixin:
    """Part of App (lat/ui/app.py); App.__init__ creates the state these
    methods use."""

    def _build_history(self, page):
        toolbar = tk.Frame(page, bg=BG)
        toolbar.pack(fill="x", pady=(0, 8))
        MiniButton(toolbar, "Refresh", width=8, command=self._load_history).pack(side="left")
        MiniButton(toolbar, "Stats", width=6,
                   command=self._show_history_stats).pack(side="left", padx=(6, 0))
        tk.Label(toolbar, text="RATINGS REGION", bg=BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=(16, 6))
        region_menu = tk.OptionMenu(toolbar, self.var_history_region, *ARENASWEATS_REGIONS)
        region_menu.configure(
            bg=FIELD_BG, fg=TEXT_PRIMARY, activebackground=PANEL_ALT,
            activeforeground=TEXT_PRIMARY, relief="flat", bd=0,
            highlightthickness=1, highlightbackground=PANEL_BORDER,
            font=("Segoe UI", 8))
        region_dropdown = region_menu.nametowidget(region_menu["menu"])
        region_dropdown.configure(bg=FIELD_BG, fg=TEXT_PRIMARY,
                                  activebackground=ACCENT,
                                  activeforeground="#ffffff", font=("Segoe UI", 8))
        region_menu.pack(side="left")
        tk.Label(toolbar, text="STAT COLUMN", bg=BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=(14, 6))
        stat_menu = tk.OptionMenu(toolbar, self.var_history_stat,
                                  *HISTORY_STAT_COLUMNS.keys())
        stat_menu.configure(
            bg=FIELD_BG, fg=TEXT_PRIMARY, activebackground=PANEL_ALT,
            activeforeground=TEXT_PRIMARY, relief="flat", bd=0,
            highlightthickness=1, highlightbackground=PANEL_BORDER,
            font=("Segoe UI", 8))
        stat_dropdown = stat_menu.nametowidget(stat_menu["menu"])
        stat_dropdown.configure(bg=FIELD_BG, fg=TEXT_PRIMARY,
                                activebackground=ACCENT,
                                activeforeground="#ffffff", font=("Segoe UI", 8))
        stat_menu.pack(side="left")
        self.var_history_stat.trace_add("write", self._on_stat_column_change)
        self.var_history_region.trace_add("write", self._on_stat_column_change)
        tk.Label(toolbar, text="ARENA ONLY", bg=BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=(14, 6))
        NeoToggle(toolbar, self.var_history_arena_only, width=34, height=18).pack(side="left")
        self.var_history_arena_only.trace_add(
            "write", lambda *_: self._show_history_summaries())
        tk.Label(toolbar, textvariable=self.history_status_var, bg=BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 9)).pack(side="right")

        # Wheel scrolling anywhere over the details panel.
        def route_wheel(event):
            try:
                under = self.root.winfo_containing(event.x_root, event.y_root)
            except Exception:
                return
            widget = under
            while widget is not None:
                if widget is self.history_detail_canvas:
                    self.history_detail_canvas.yview_scroll(
                        int(-event.delta / 120), "units")
                    return
                widget = getattr(widget, "master", None)

        self.root.bind_all("<MouseWheel>", route_wheel, add="+")

        body = tk.Frame(page, bg=BG)
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=0)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=BG)
        left.grid(row=0, column=0, sticky="nsw")
        tk.Label(left, text="RECENT GAMES", bg=BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w", pady=(0, 4))
        list_wrap = tk.Frame(left, bg=PANEL_BORDER)
        list_wrap.pack(fill="both", expand=True)
        self.history_list = tk.Listbox(
            list_wrap, width=50, exportselection=False, activestyle="none",
            font=("Cascadia Mono", 9), bg=FIELD_BG, fg=TEXT_PRIMARY,
            selectbackground=ACCENT, selectforeground="#ffffff",
            relief="flat", bd=0, highlightthickness=1,
            highlightbackground=PANEL_BORDER, highlightcolor=ACCENT)
        history_scroll = tk.Scrollbar(list_wrap, orient="vertical",
                                      command=self.history_list.yview)
        self.history_list.configure(yscrollcommand=history_scroll.set)
        self.history_list.pack(side="left", fill="both", expand=True)
        history_scroll.pack(side="right", fill="y")
        self.history_list.bind("<<ListboxSelect>>", self._on_history_select)
        self.history_more_button = MiniButton(left, "Load older games", width=16,
                                              command=lambda: self._load_history(more=True))
        self.history_more_button.pack(anchor="w", pady=(6, 0))

        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        tk.Label(right, text="GAME DETAILS", bg=BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w", pady=(0, 4))
        detail_wrap = tk.Frame(right, bg=PANEL_BORDER)
        detail_wrap.pack(fill="both", expand=True)
        canvas = tk.Canvas(detail_wrap, bg=PANEL_BG, highlightthickness=0)
        detail_scroll = tk.Scrollbar(detail_wrap, orient="vertical",
                                     command=canvas.yview)
        self.history_detail_inner = tk.Frame(canvas, bg=PANEL_BG)
        self.history_detail_inner.bind(
            "<Configure>",
            lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        inner_id = canvas.create_window((0, 0), window=self.history_detail_inner,
                                        anchor="nw")
        # Fit the details to the visible width so wide columns shrink
        # instead of disappearing past the right edge.
        canvas.bind("<Configure>",
                    lambda event: canvas.itemconfigure(inner_id, width=event.width))
        canvas.configure(yscrollcommand=detail_scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        detail_scroll.pack(side="right", fill="y")
        self.history_detail_canvas = canvas
        tk.Label(self.history_detail_inner,
                 text="Select a game to see items,\naugments and lobby ratings.",
                 bg=PANEL_BG, fg=TEXT_MUTED, font=("Segoe UI", 10),
                 justify="center").pack(padx=24, pady=48)

    def _set_history_status(self, text):
        if not self._closing:
            self.history_status_var.set(text)

    def _detect_arena_region(self, then=None):
        """Map the connected League platform to an arenasweats region.
        then() runs on the UI thread afterwards, region found or not."""

        def worker():
            platform = ""
            try:
                response = self.client.get("/lol-chat/v1/me")
                if response.status_code == 200:
                    platform = str(response.json().get("platformId") or "").upper()
            except Exception:
                pass
            if not platform:
                try:
                    response = self.client.get(
                        "/lol-match-history/v1/products/lol/current-summoner/matches"
                        "?begIndex=0&endIndex=0")
                    if response.status_code == 200:
                        games = parse_history_games(response.json())
                        if games:
                            platform = str(games[0].get("platformId") or "").upper()
                except Exception:
                    pass
            region = PLATFORM_TO_AS_REGION.get(platform)

            # Tk variables are only touched on the UI thread.
            def apply():
                if self._closing:
                    return
                if region and region != self.var_history_region.get():
                    # Re-renders the open match, so ratings reload for the region.
                    self.var_history_region.set(region)
                    self.log(f"History: League region detected ({platform}) - "
                             f"ratings region set to '{region}'.")
                if then is not None:
                    then()

            if region or then is not None:
                self._run_on_ui(apply)

        threading.Thread(target=worker, daemon=True, name="LAT-RegionDetect").start()

    def _ensure_history_metadata(self, force=False):
        """Load item/augment names + icon URLs from CommunityDragon.

        The JSON is kept on disk (see fetch_cached_json), so a normal start
        reads it from there. force downloads again: used once per session
        when a game shows ids the cached copy does not know (a new patch).
        Each source that fails (offline, timeout, bad JSON) is retried on
        the next Refresh or game selection; what did load is kept."""
        if (self._history_meta_loaded and not force) or self._history_loading_meta:
            return
        self._history_loading_meta = True
        known_items = dict(self._history_items_map)

        def worker():
            items, augments, failed, sources = {}, {}, [], []

            def fetch_json(url):
                data, source = fetch_cached_json(url, force=force)
                sources.append(source)
                return data

            try:
                for entry in fetch_json(ITEMS_DATA_URL):
                    item_id, name = entry.get("id"), entry.get("name")
                    if isinstance(item_id, int) and isinstance(name, str) and name:
                        tooltip = clean_game_text(entry.get("plaintext")
                                                  or entry.get("description"))
                        items[item_id] = {
                            "name": name,
                            "icon_urls": cdragon_icon_candidates(
                                entry.get("iconPath")),
                            "tooltip": tooltip,
                        }
            except Exception as exc:
                failed.append(f"items ({exc.__class__.__name__})")
            item_names = {iid: info.get("name")
                          for iid, info in (items or known_items).items()}
            try:
                for entry in fetch_json(ARENA_DATA_URL).get("augments") or []:
                    aug_id, name = entry.get("id"), entry.get("name")
                    if isinstance(aug_id, int) and isinstance(name, str) and name:
                        icon = entry.get("iconLarge") or entry.get("iconSmall") or ""
                        description = expand_augment_description(
                            entry.get("desc"), entry.get("dataValues"), item_names)
                        augments[aug_id] = {
                            "name": name,
                            "icon_urls": cdragon_icon_candidates(icon),
                            "tooltip": clean_game_text(description),
                        }
            except Exception as exc:
                failed.append(f"arena augments ({exc.__class__.__name__})")
            # Secondary source: newer Arena augments land here first.
            try:
                for entry in fetch_json(CHERRY_AUGMENTS_URL):
                    try:
                        aug_id = int(entry.get("id"))
                    except (TypeError, ValueError):
                        continue
                    if aug_id in augments:
                        continue
                    name = entry.get("nameTRA") or entry.get("augmentNameId")
                    if not isinstance(name, str) or not name.strip():
                        continue
                    icon = (entry.get("augmentLargeIconPath")
                            or entry.get("augmentSmallIconPath") or "")
                    augments[aug_id] = {
                        "name": name.strip(),
                        "icon_urls": cdragon_icon_candidates(icon),
                        "tooltip": "",
                    }
            except Exception as exc:
                failed.append(f"cherry augments ({exc.__class__.__name__})")

            def apply():
                self._history_loading_meta = False
                if self._closing:
                    return
                self._history_items_map.update(items)
                # A full augment load replaces placeholders from the
                # secondary source; a partial one only adds to what we have.
                for aug_id, info in augments.items():
                    current = self._history_augments_map.get(aug_id)
                    if current is None or info.get("tooltip") or not current.get("tooltip"):
                        self._history_augments_map[aug_id] = info
                if "network" in sources:
                    self._history_meta_network = True
                # An old copy used because the download failed is retried too.
                self._history_meta_loaded = not failed and "stale" not in sources
                if failed:
                    self.log("History: could not load " + ", ".join(failed)
                             + " from CommunityDragon - will retry on Refresh.")
                elif "stale" in sources:
                    self.log("History: CommunityDragon is unreachable - using the "
                             "saved item/augment names.")
                # Re-render so names/icons appear without a re-click.
                if items or augments:
                    self._rerender_history_detail()

            self._run_on_ui(apply)

        threading.Thread(target=worker, daemon=True, name="LAT-HistoryMeta").start()

    def _rerender_history_detail(self):
        try:
            if self._history_detail_mode == "stats":
                self._render_history_stats()
            elif self._current_detail_view is not None:
                self._render_history_detail(self._current_detail_view)
        except tk.TclError:
            pass

    def _load_history(self, more=False, on_done=None):
        """Load the newest page of games (Refresh) or the next older page
        (more=True). on_done(summaries) runs on the UI thread afterwards."""
        if self._closing or self._history_loading:
            return
        if not self.client.is_connected or self._connection_state is not True:
            self._set_history_status("League client is offline")
            self.log("History: League client is not connected.")
            return
        begin = len(self._all_summaries) if more else 0
        if more and (self._history_exhausted or begin >= HISTORY_MAX_GAMES):
            self._set_history_status("No older games available")
            return
        self._history_loading = True
        self._set_history_status("Loading older games..." if more else "Loading recent games...")
        self._update_more_button()
        if not more:
            # The account may have changed since the last load (log out / in).
            self._my_identity_cache = None
            self._detect_arena_region()
        self._ensure_history_metadata()

        def worker():
            games, error, account = [], None, None
            try:
                response = self.client.get(
                    match_history_path(begin, begin + HISTORY_PAGE_SIZE - 1))
                if response.status_code == 200:
                    payload = response.json()
                    games = parse_history_games(payload)
                    account = (payload or {}).get("accountId")
                else:
                    error = f"HTTP {response.status_code}"
            except Exception as exc:
                error = str(exc)
            # The LCU returns the newest game first.
            summaries = [summarize_history_game(game) for game in games]

            def finish():
                if self._closing:
                    return
                self._history_loading = False
                if error and self._all_summaries:
                    # A failed refresh or "load more" keeps the games shown.
                    self._set_history_status(f"Loading failed ({error})")
                    self.log(f"History: request failed ({error}).")
                elif more and not error:
                    self._append_history_page(summaries)
                elif not more and not error and self._history_account == account \
                        and self._all_summaries:
                    self._merge_newest_history_page(summaries)
                else:
                    self._history_exhausted = len(summaries) < HISTORY_PAGE_SIZE
                    self._fill_history_list(summaries, error)
                if not error:
                    self._history_account = account
                    self._history_loaded_once = True
                self._update_more_button()
                if on_done is not None:
                    on_done(self._all_summaries)

            self._run_on_ui(finish)

        threading.Thread(target=worker, daemon=True, name="LAT-HistoryList").start()

    def _merge_newest_history_page(self, summaries):
        """Refresh keeps the older pages already loaded: new games go on
        top, everything already in the list stays below them."""
        fresh_ids = {s.get("game_id") for s in summaries}
        older = [s for s in self._all_summaries if s.get("game_id") not in fresh_ids]
        added = sum(1 for s in summaries
                    if s.get("game_id") not in {o.get("game_id") for o in self._all_summaries})
        self._all_summaries = summaries + older
        self._show_history_summaries()
        if self._history_detail_mode in (None, "stats"):
            self._render_history_stats()
        if added:
            self.log(f"History: {added} new game{'s' if added != 1 else ''} loaded.")

    def _append_history_page(self, summaries):
        known = {s.get("game_id") for s in self._all_summaries}
        new = [s for s in summaries if s.get("game_id") not in known]
        self._history_exhausted = len(summaries) < HISTORY_PAGE_SIZE or not new
        self._all_summaries = self._all_summaries + new
        self._show_history_summaries()
        if new:
            arena = sum(1 for summary in new if is_arena_game(summary))
            self.log(f"History: loaded {len(new)} older games ({arena} Arena), "
                     f"{len(self._all_summaries)} in total.")
        else:
            self._set_history_status("No older games available")
        if self._history_detail_mode == "stats":
            self._render_history_stats()

    def _update_more_button(self):
        button = getattr(self, "history_more_button", None)
        if button is None:
            return
        available = (self._history_loaded_once and not self._history_exhausted
                     and len(self._all_summaries) < HISTORY_MAX_GAMES)
        try:
            button.configure(state="normal" if available and not self._history_loading
                             else "disabled")
        except tk.TclError:
            pass

    def _fill_history_list(self, summaries, error=None):
        if error:
            self._all_summaries = []
            self._show_history_summaries()
            self._set_history_status(f"Failed to load ({error})")
            self.log(f"History: request failed ({error}).")
            return
        self._all_summaries = summaries
        self._show_history_summaries()
        if summaries:
            arena = sum(1 for summary in summaries if is_arena_game(summary))
            self.log(f"History: loaded {len(summaries)} recent games ({arena} Arena).")
        # Nothing picked yet: the details panel shows the Arena overview.
        if self._history_detail_mode in (None, "stats"):
            self._render_history_stats()

    def _history_list_entries(self):
        """Rows of the game list: the game in progress first, then the
        loaded games (Arena only when the filter is on)."""
        summaries = self._all_summaries
        arena_only = bool(self.var_history_arena_only.get())
        shown = [s for s in summaries if is_arena_game(s)] if arena_only else list(summaries)
        live = self._live_view
        if live is not None and (is_arena_game(live) or not arena_only):
            me = live.get("me") or {}
            shown.insert(0, {"game_id": LIVE_GAME_ID, "live": True,
                             "game_mode": live.get("game_mode"),
                             "champion_id": me.get("champion_id"),
                             "players": len(live.get("players") or [])})
        return shown

    def _show_history_summaries(self):
        """(Re)fill the game list from the loaded games and the Arena filter."""
        summaries = self._all_summaries
        selected = self._selected_game_id
        selection = self.history_list.curselection()
        if selection and selection[0] < len(self._match_summaries):
            selected = self._match_summaries[selection[0]].get("game_id")
        arena_only = bool(self.var_history_arena_only.get())
        shown = self._history_list_entries()
        self._match_summaries = shown
        self.history_list.delete(0, tk.END)
        for index, summary in enumerate(shown):
            self.history_list.insert(tk.END, self._format_history_row(summary))
            self.history_list.itemconfigure(index, foreground=self._history_row_color(summary))
            if selected is not None and summary.get("game_id") == selected:
                self.history_list.selection_set(index)
                self.history_list.see(index)
        loaded = len(summaries)
        if not summaries:
            self._set_history_status("No games found")
        elif arena_only:
            arena = sum(1 for s in summaries if is_arena_game(s))
            self._set_history_status(f"{arena} Arena games (of {loaded} loaded)")
        else:
            self._set_history_status(f"{loaded} games loaded")

    @staticmethod
    def _history_row_color(summary):
        if summary.get("live"):
            return ACCENT_ALT
        if summary.get("placement") == 1:
            return WARNING
        if summary.get("win") is False:
            return TEXT_MUTED
        return TEXT_PRIMARY

    def _format_history_row(self, summary):
        champ = self.champion_data.label(summary.get("champion_id") or 0)
        if summary.get("live"):
            players = summary.get("players")
            return (f"LIVE {champ[:12]:<13}in progress"
                    + (f" · {players} players" if players else ""))
        # Arena shows the placement, other queues the result.
        if summary.get("placement"):
            result = f"#{summary['placement']}"
        else:
            result = "?" if summary.get("win") is None else ("W" if summary["win"] else "L")
        creation = summary.get("creation") or 0
        when = time.strftime("%m-%d %H:%M", time.localtime(creation / 1000)) if creation else ""
        minutes = max(1, round((summary.get("duration_s") or 0) / 60))
        row = (f"{result:<3}{champ[:12]:<13}{summary.get('kda', ''):<9}"
               f"{minutes:>3}m  {when:<11}")
        if not is_arena_game(summary):
            row += "  " + QUEUE_LABELS.get(summary.get("queue_id"), "")[:9]
        return row.rstrip()

    def _on_history_select(self, _event=None):
        selection = self.history_list.curselection()
        if not selection or self._closing:
            return
        index = selection[0]
        if index >= len(self._match_summaries):
            return
        game_id = self._match_summaries[index].get("game_id")
        if game_id is None:
            return
        self._selected_game_id = game_id
        self._ensure_history_metadata()
        if game_id == LIVE_GAME_ID:
            if self._live_view is not None:
                self._render_history_detail(self._live_view)
            return
        cached = self._detail_cache.get(game_id)
        if cached is not None:
            self._render_history_detail(cached)
            return
        self._open_build_pid = None
        if not self.client.is_connected:
            self._set_history_status("League client is offline")
            return
        self._set_history_status("Loading match details...")

        def worker():
            payload, error = None, None
            identity = self._fetch_my_identity()
            try:
                response = self.client.get(f"/lol-match-history/v1/games/{game_id}")
                if response.status_code == 200:
                    payload = response.json()
                else:
                    error = f"HTTP {response.status_code}"
            except Exception as exc:
                error = str(exc)
            view = parse_match_detail(payload)
            if view is not None:
                view["me"] = select_local_player(
                    view["players"], identity["ids"], identity["puuid"])

            def finish():
                if self._closing:
                    return
                if view is None:
                    if self._selected_game_id == game_id:
                        self._set_history_status(f"Details failed ({error})")
                    return
                self._detail_cache[game_id] = view
                # A slow answer for a game the user has already left must
                # not replace the game they are looking at now.
                if self._selected_game_id == game_id:
                    self._render_history_detail(view)

            self._run_on_ui(finish)

        threading.Thread(target=worker, daemon=True, name="LAT-HistoryDetail").start()

    def _resolve_entry(self, entry_id, kind):
        """entry id -> {"name", "urls", "tooltip", "fallback"}."""
        mapping = (self._history_items_map if kind == "item"
                   else self._history_augments_map)
        info = mapping.get(entry_id)
        if isinstance(info, dict):
            return {
                "name": info.get("name") or str(entry_id),
                "urls": info.get("icon_urls") or [],
                "tooltip": info.get("tooltip") or "",
                "fallback": None,
            }
        # Unknown id with names read from the disk copy: a new patch may
        # have added it, so download fresh data once this session.
        if (self._history_meta_loaded and not self._history_meta_network
                and not self._history_meta_forced and isinstance(entry_id, int)):
            self._history_meta_forced = True
            self.log(f"History: {kind} {entry_id} is not in the saved names - "
                     "downloading fresh data from CommunityDragon.")
            self._run_on_ui(lambda: self._ensure_history_metadata(force=True))
        prefix = "Item" if kind == "item" else "Augment"
        return {"name": f"{prefix} {entry_id}", "urls": [], "tooltip": "",
                "fallback": f"{prefix[0]}{entry_id}"}

    def _fetch_my_identity(self):
        cached = self._my_identity_cache
        if cached:
            return cached
        account_ids, puuid = set(), ""
        try:
            response = self.client.get("/lol-summoner/v1/current-summoner")
            if response.status_code == 200:
                data = response.json()
                for key in ("accountId", "currentAccountId"):
                    value = data.get(key)
                    if isinstance(value, int):
                        account_ids.add(value)
                puuid = str(data.get("puuid") or "")
        except Exception:
            pass
        identity = {"ids": account_ids, "puuid": puuid}
        # Only a successful answer is cached; a failed one is retried.
        if account_ids or puuid:
            self._my_identity_cache = identity
        return identity

    def _refresh_history_scroll(self):
        """Recompute the details scrollregion (after expand/collapse)."""
        def do():
            canvas = getattr(self, "history_detail_canvas", None)
            if canvas is None:
                return
            try:
                canvas.update_idletasks()
                canvas.configure(scrollregion=canvas.bbox("all"))
            except Exception:
                pass
        self._run_on_ui(do)
