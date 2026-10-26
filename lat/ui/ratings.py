"""Lobby ratings from arenasweats.lol, cached per player and region."""

import time
import threading

from lat.constants import APP_TITLE
from lat.history.data import ARENASWEATS_BASE_URL, rating_from_response
from lat.http import _http


class RatingsMixin:
    """Part of App (lat/ui/app.py); App.__init__ creates the state these
    methods use."""

    def _elo_key(self, player, region=None):
        region = region or (self.var_history_region.get() or "euw").strip().lower()
        return ((player.get("name") or "").lower(),
                (player.get("tag") or "").lower(), region)

    def _start_elo_lookup(self, players, token, arena=True):
        region = (self.var_history_region.get() or "euw").strip().lower()
        progress = {"done": 0, "errors": 0}
        total = len(players)
        if not arena:
            # arenasweats only rates Arena; other queues would show bogus data.
            for elo_var, rank_var in self._elo_cell_vars.values():
                elo_var.set("-")
                rank_var.set("")
            self._set_history_status("Ratings are only available for Arena games")
            return

        def apply(participant_id, result):
            if token != self._history_token or self._closing:
                return
            cells = self._elo_cell_vars.get(participant_id)
            if not cells:
                return
            elo_var, rank_var = cells
            if result == "error":
                elo_var.set("err")
                rank_var.set("")
                progress["errors"] += 1
            elif result is None:
                elo_var.set("-")
                rank_var.set("")
            else:
                rating = result.get("rating")
                elo_var.set(str(rating) if rating is not None else "-")
                rank = result.get("rank")
                rank_var.set(f"#{rank}" if isinstance(rank, int) else "")
            if total:
                progress["done"] += 1
                percent = int(100 * progress["done"] / total)
                suffix = f" ({progress['errors']} errors)" if progress["errors"] else ""
                if progress["done"] >= total:
                    self._set_history_status(
                        f"Ratings loaded {progress['done']}/{total} (100%){suffix}")
                else:
                    self._set_history_status(
                        f"Loading ratings... {percent}% ({progress['done']}/{total})")

        def worker():
            for player in players:
                # Stop once the user opened another game or re-rendered.
                if self._closing or token != self._history_token:
                    return
                result = self._fetch_rating(player, region)
                participant_id = player.get("participant_id")
                self._run_on_ui(lambda pid=participant_id, res=result: apply(pid, res))

        threading.Thread(target=worker, daemon=True, name="LAT-EloLookup").start()

    def _fetch_rating(self, player, region):
        """arenasweats rating of one player (worker thread): a dict from
        rating_from_response, None when unrated, "error" when the request
        failed. Answers are cached; errors (timeouts, 5xx) are retried."""
        key = self._elo_key(player, region)
        cached = self._elo_cache.get(key, "miss")
        if cached != "miss":
            return cached
        name, tag = player.get("name") or "", player.get("tag") or ""
        if not name:
            self._elo_cache[key] = None
            return None
        term = f"{name}#{tag}" if tag and tag != name else name
        try:
            response = _http().get(
                f"{ARENASWEATS_BASE_URL}/api/player_rank",
                params={"search_term": term, "region": region, "season": "live"},
                headers={"User-Agent": APP_TITLE},
                timeout=(5, 10))
            if response.status_code == 200:
                result = rating_from_response(response.json())
            elif response.status_code == 404:
                result = None
            else:
                result = "error"
        except Exception:
            result = "error"
        # Cached before the pause, so a second lookup running meanwhile (live
        # lobby prefetch + the open table) does not ask for the same player.
        if result != "error":
            self._elo_cache[key] = result
        time.sleep(0.12)                # stay polite to arenasweats.lol
        return result
