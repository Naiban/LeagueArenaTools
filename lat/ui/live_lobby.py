"""Game in progress: lobby ratings from the Live Client Data API, and a
history refresh once the game is over."""

import time
import threading

from lat.constants import GAME_RUNNING_PHASES
from lat.history.data import (
    build_live_view, is_arena_game, LIVE_CLIENT_ACTIVE_NAME_URL, LIVE_CLIENT_PLAYERS_URL,
    LIVE_CLIENT_STATS_URL, LIVE_GAME_ID, parse_live_players,
)
from lat.http import _http


# How long to wait for the Live Client Data API after a game started.
LIVE_LOBBY_WAIT_S = 600


class LiveLobbyMixin:
    """Part of App (lat/ui/app.py); App.__init__ creates the state these
    methods use."""

    def _on_gameflow_phase(self, phase):
        previous, self._gameflow_phase = self._gameflow_phase, phase
        if phase == previous:
            return
        running = phase in GAME_RUNNING_PHASES
        was_running = previous in GAME_RUNNING_PHASES
        if running and not was_running:
            self._start_live_lobby_watch()
        elif was_running and not running:
            self._end_live_lobby()
            if self._history_loaded_once:
                self._refresh_history_after_game()

    def _start_live_lobby_watch(self):
        """Wait for the game to load, then read everyone in it."""
        self._live_token += 1
        token = self._live_token

        def alive():
            return not self._closing and token == self._live_token

        def worker():
            session = _http().Session()
            session.verify = False      # the game serves a self-signed certificate
            deadline = time.monotonic() + LIVE_LOBBY_WAIT_S
            while alive() and time.monotonic() < deadline:
                try:
                    response = session.get(LIVE_CLIENT_PLAYERS_URL, timeout=2)
                    players = (parse_live_players(response.json(), self.champion_data.resolve)
                               if response.status_code == 200 else [])
                except Exception:
                    players = []        # loading screen: the API is not up yet
                if players:
                    stats, active = {}, ""
                    try:
                        stats = session.get(LIVE_CLIENT_STATS_URL, timeout=2).json()
                        active = session.get(LIVE_CLIENT_ACTIVE_NAME_URL, timeout=2).json()
                    except Exception:
                        pass
                    view = build_live_view(players, active if isinstance(active, str) else "",
                                           stats)
                    self._run_on_ui(lambda: self._show_live_lobby(token, view))
                    return
                for _ in range(30):
                    if not alive():
                        return
                    time.sleep(0.1)

        threading.Thread(target=worker, daemon=True, name="LAT-LiveLobby").start()

    def _show_live_lobby(self, token, view):
        if self._closing or token != self._live_token:
            return
        self._live_view = view
        self._show_history_summaries()
        arena = is_arena_game(view)
        self.log(f"Live game: {len(view['players'])} players in the lobby"
                 + (" - loading their Arena ratings." if arena else "."))
        if self._selected_game_id == LIVE_GAME_ID:
            self._render_history_detail(view)
        if arena:
            # The region decides which ladder the names are looked up on.
            self._detect_arena_region(then=lambda: self._prefetch_live_ratings(token, view))

    def _prefetch_live_ratings(self, token, view):
        """Look up every rating now, so the LIVE entry opens instantly, and
        log a one-line summary of the lobby."""
        region = (self.var_history_region.get() or "euw").strip().lower()
        players, me = list(view["players"]), view.get("me")

        def worker():
            results = []
            for player in players:
                if self._closing or token != self._live_token:
                    return
                results.append((player, self._fetch_rating(player, region)))
            self._run_on_ui(lambda: report(results))

        def report(results):
            if self._closing or token != self._live_token:
                return
            rated = [(player, rating) for player, rating in results
                     if isinstance(rating, dict) and isinstance(rating.get("rating"), int)]
            if not rated:
                self.log("Live game: nobody in the lobby has an Arena rating yet.")
                return
            average = round(sum(rating["rating"] for _, rating in rated) / len(rated))
            message = f"Live lobby: average rating {average} ({len(rated)}/{len(results)} rated)"
            others = [(player, rating) for player, rating in rated if player is not me]
            if others:
                player, rating = max(others, key=lambda pair: pair[1]["rating"])
                riot_id = f"{player['name']}#{player['tag']}" if player.get("tag") else player["name"]
                message += f", highest {riot_id} {rating['rating']}"
            self.log(message + ".")

        threading.Thread(target=worker, daemon=True, name="LAT-LiveRatings").start()

    def _end_live_lobby(self):
        self._live_token += 1
        if self._live_view is None:
            return
        self._live_view = None
        if self._selected_game_id == LIVE_GAME_ID:
            self._selected_game_id = None
            self._render_history_stats()
        self._show_history_summaries()

    def _refresh_history_after_game(self):
        """Reload the history until the game that just ended is in it (the
        LCU adds it a few seconds to a minute after the end screen)."""
        self._history_refresh_token += 1
        token = self._history_refresh_token
        newest_before = self._all_summaries[0].get("game_id") if self._all_summaries else None

        def attempt(number):
            if self._closing or token != self._history_refresh_token or number > 6:
                return
            if self._history_loading or self._connection_state is not True:
                self.root.after(5000, lambda: attempt(number + 1))
                return

            def done(summaries):
                newest = summaries[0].get("game_id") if summaries else None
                if newest == newest_before:
                    self.root.after(15000, lambda: attempt(number + 1))

            self._load_history(on_done=done)

        self.root.after(6000, lambda: attempt(1))
