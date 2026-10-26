import csv
import io
import json
import os
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog
import unittest

import LeagueArenaTools as app
from lat import config
from lat import http as http_module
from lat import updater
from lat.history import cache


def png_bytes(color=(200, 40, 40, 255)):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGBA", (8, 8), color).save(buffer, "PNG")
    return buffer.getvalue()


class FakeHttpResponse:
    def __init__(self, status_code=200, content=b"", data=None):
        self.status_code = status_code
        self.content = content
        self._data = data

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeHttp:
    """Stands in for the requests module: routes map URL -> response, a
    callable, or a list consumed one answer per call."""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []
        self._lock = threading.Lock()
        self.headers = {}

    def get(self, url, **_kwargs):
        with self._lock:
            self.calls.append(url)
            route = self.routes.get(url)
            if isinstance(route, list):
                route = route.pop(0) if len(route) > 1 else route[0]
        if callable(route):
            route = route()
        if isinstance(route, Exception):
            raise route
        return route or FakeHttpResponse(404)

    def Session(self):
        return self

    def count(self, url):
        with self._lock:
            return self.calls.count(url)


def arena_payload():
    """Six trios like a live 18-player Arena match (queue 1750, CHERRY)."""
    identities, participants = [], []
    pid = 0
    for subteam, placement in ((3, 1), (4, 2), (5, 3), (6, 4), (2, 5), (1, 6)):
        for slot in range(3):
            pid += 1
            identities.append({"participantId": pid, "player": {
                "gameName": f"P{pid}", "tagLine": "eune", "puuid": f"puuid-{pid}",
                "currentAccountId": 1000 + pid}})
            participants.append({"participantId": pid, "championId": 100 + pid, "stats": {
                "playerSubteamId": subteam, "subteamPlacement": placement,
                "win": placement <= 3, "kills": pid, "deaths": 1, "assists": 2,
                "totalDamageDealtToChampions": 1000 * pid, "goldEarned": 500,
                "item0": 3031, "item1": 0, "item2": 6672,
                "playerAugment1": 41, "playerAugment2": 0,
            }})
    return {"gameId": 4016402583, "queueId": 1750, "gameMode": "CHERRY",
            "gameCreation": 1_760_000_000_000, "gameDuration": 1438,
            "participantIdentities": identities, "participants": participants}


class DataTests(unittest.TestCase):
    def test_platform_regions_match_riot_and_arenasweats(self):
        self.assertEqual(app.PLATFORM_TO_AS_REGION["LA1"], "lan")
        self.assertEqual(app.PLATFORM_TO_AS_REGION["LA2"], "las")
        for platform in ("SG2", "PH2", "TH2"):
            self.assertEqual(app.PLATFORM_TO_AS_REGION[platform], "sea")
        self.assertEqual(app.PLATFORM_TO_AS_REGION["TW2"], "tw")
        self.assertEqual(app.PLATFORM_TO_AS_REGION["VN2"], "vn")
        self.assertLessEqual(set(app.PLATFORM_TO_AS_REGION.values()),
                             set(app.ARENASWEATS_REGIONS))

    def test_queue_labels(self):
        self.assertEqual(app.QUEUE_LABELS[1090], "TFT")
        for queue in (1700, 1710, 1740, 1750):
            self.assertEqual(app.QUEUE_LABELS[queue], "Arena")

    def test_long_riot_ids_keep_their_tag(self):
        self.assertEqual(app.shorten_riot_id("Raijin", "UNIZA"), "Raijin#UNIZA")
        self.assertEqual(app.shorten_riot_id("Uranus Destroyer", "GM10"), "Uranus Destr…#GM10")
        self.assertLessEqual(len(app.shorten_riot_id("A" * 16, "ABCDE")), 18)
        self.assertEqual(app.shorten_riot_id("B" * 20, ""), "B" * 17 + "…")

    def test_arena_detection(self):
        self.assertTrue(app.is_arena_game({"gameMode": "CHERRY", "queueId": 9999}))
        self.assertTrue(app.is_arena_game({"game_mode": None, "queue_id": 1750}))
        self.assertFalse(app.is_arena_game({"gameMode": "ARAM", "queueId": 1750}))
        self.assertFalse(app.is_arena_game({"queueId": 450}))
        self.assertFalse(app.is_arena_game(None))
        summary = app.summarize_history_game(arena_payload())
        self.assertTrue(app.is_arena_game(summary))
        self.assertTrue(app.is_arena_game(app.parse_match_detail(arena_payload())))


def label_texts(widget):
    """Text of every label below widget."""
    found = []
    for child in widget.winfo_children():
        if isinstance(child, tk.Label):
            found.append(child.cget("text"))
        found.extend(label_texts(child))
    return found


def arena_summary(game_id, placement, champion=103, augments=(), damage=1000):
    return {"game_id": game_id, "game_mode": "CHERRY", "queue_id": 1750,
            "placement": placement, "win": placement <= 3, "champion_id": champion,
            "kills": 4, "deaths": 2, "assists": 6, "damage": damage,
            "augments": list(augments), "creation": 1_760_000_000_000 + game_id,
            "duration_s": 1200, "kda": "4/2/6"}


ARENASWEATS_SAMPLE = {
    "games_played": 16, "league_rank": "Master", "percentile": 99.6,
    "player_name": "Shinkansen#Arena", "player_rank": 592, "rating": 2198,
    "top_champ1": "Ezreal", "top_champ2": "Ahri", "top_champ3": "Ekko",
}


class FeatureDataTests(unittest.TestCase):
    def test_window_geometry(self):
        self.assertEqual(app.parse_window_geometry("1200x800+30+40"), (1200, 800, 30, 40))
        self.assertEqual(app.parse_window_geometry("1080x760+-8+-8"), (1080, 760, -8, -8))
        self.assertEqual(app.parse_window_geometry("600x500+0+0"), (920, 700, 0, 0))
        for bad in ("1x1+0+0", "", None, "big", "1200x800"):
            self.assertIsNone(app.parse_window_geometry(bad))
        self.assertEqual(app._sanitize_window({"geometry": "junk", "maximized": "yes"}),
                         {"geometry": "", "maximized": False})
        if os.name == "nt":
            self.assertFalse(app.window_title_visible(-40000, -40000, 1000))

    def test_window_state_survives_a_save(self):
        with tempfile.TemporaryDirectory() as folder:
            original = config.CONFIG_PATH
            config.CONFIG_PATH = os.path.join(folder, "config.json")
            try:
                store = app.load_profile_store()
                store["window"] = {"geometry": "1000x720+40+50", "maximized": True}
                self.assertTrue(app.save_profile_store(store))
                self.assertEqual(app.load_profile_store()["window"],
                                 {"geometry": "1000x720+40+50", "maximized": True})
            finally:
                config.CONFIG_PATH = original

    def test_arena_stats(self):
        summaries = [
            arena_summary(1, 1, champion=103, augments=[41, 7]),
            arena_summary(2, 4, champion=103, augments=[41]),
            arena_summary(3, 2, champion=105, augments=[7], damage=3000),
            {"game_id": 4, "game_mode": "CLASSIC", "queue_id": 420, "placement": None},
            arena_summary(5, 6, champion=105, augments=[99]),
        ]
        stats = app.compute_arena_stats(summaries)
        self.assertEqual(stats["games"], 4)
        self.assertEqual(stats["avg_placement"], 3.25)
        self.assertEqual(stats["win_rate"], 0.5)
        self.assertEqual(stats["firsts"], 1)
        self.assertEqual(stats["avg_damage"], 1500)
        self.assertEqual(stats["kda"], 5.0)
        self.assertEqual(stats["recent"], [(1, True), (4, False), (2, True), (6, False)])
        self.assertEqual([row["champion_id"] for row in stats["champions"]], [103, 105])
        self.assertEqual(stats["champions"][0]["avg_placement"], 2.5)
        # Augment 99 was taken once: below the 2-game minimum.
        self.assertEqual([row["augment_id"] for row in stats["augments"]], [7, 41])
        self.assertIsNone(app.compute_arena_stats([summaries[3]]))

    def test_summary_carries_build_and_damage(self):
        summary = app.summarize_history_game(arena_payload())
        self.assertEqual(summary["augments"], [41])
        self.assertEqual(summary["items"], [3031, 6672])
        self.assertEqual(summary["damage"], 1000)

    def test_game_end_message(self):
        title, body = app.format_game_end_message(arena_summary(1, 1), "Ahri")
        self.assertEqual(title, "Victory - #1")
        self.assertEqual(body, "Arena · Ahri · 4/2/6 KDA · 1 000 damage · 20m 00s")
        title, body = app.format_game_end_message(
            {"queue_id": 450, "win": False, "kda": "1/9/3", "duration_s": 0}, "Fizz")
        self.assertEqual((title, body), ("Defeat", "ARAM · Fizz · 1/9/3 KDA"))
        title.encode("latin-1")                       # HTTP header safe

    def test_finished_game_matching(self):
        game = {"gameId": 9, "gameCreation": 1_000_000}
        self.assertTrue(app.is_finished_game(game, 9, 8, 0))
        self.assertFalse(app.is_finished_game(game, 10, 8, 0))
        self.assertTrue(app.is_finished_game(game, None, 8, 0))
        self.assertFalse(app.is_finished_game(game, None, 9, 0))
        self.assertTrue(app.is_finished_game(game, None, None, 1_000 + 60))
        self.assertFalse(app.is_finished_game(game, None, None, 1_000 + 3600))

    def test_rating_details_and_tooltip(self):
        rating = app.rating_from_response(ARENASWEATS_SAMPLE)
        self.assertEqual(rating["league"], "Master")
        self.assertEqual(rating["games"], 16)
        self.assertEqual(rating["top_champions"], ["Ezreal", "Ahri", "Ekko"])
        text = app.format_rating_tooltip(rating)
        self.assertEqual(text.splitlines(), [
            "Master · 2198 rating", "Ladder rank #592 (top 0.4%)",
            "16 games this season", "Best champions: Ezreal, Ahri, Ekko"])
        self.assertIn("No Arena rating", app.format_rating_tooltip(None))
        self.assertEqual(app.format_rating_tooltip(app.rating_from_response({})),
                         "Rated on arenasweats.lol")

    def test_live_players(self):
        champions = {"ahri": 103, "monkeyking": 62}
        payload = [
            {"riotId": "Shinkansen#Arena", "championName": "Ahri",
             "rawChampionName": "game_character_displayname_Ahri", "level": 7,
             "scores": {"kills": 3, "deaths": 1, "assists": 2},
             "items": [{"itemID": 3031}, {"itemID": 0}]},
            {"riotIdGameName": "Other", "riotIdTagLine": "euw", "championName": "Wukong",
             "rawChampionName": "game_character_displayname_MonkeyKing"},
            {"riotId": "Bot#1", "isBot": True},
        ]
        players = app.parse_live_players(payload, lambda name: champions.get(app._normalize(name)))
        self.assertEqual([(p["name"], p["tag"], p["champion_id"]) for p in players],
                         [("Shinkansen", "ARENA", 103), ("Other", "EUW", 62)])
        self.assertEqual(players[0]["items"], [3031])
        self.assertEqual(players[0]["kills"], 3)
        view = app.build_live_view(players, "Other#EUW", {"gameMode": "CHERRY", "gameTime": 300})
        self.assertTrue(view["live"])
        self.assertTrue(app.is_arena_game(view))
        self.assertEqual(view["me"]["name"], "Other")


class DataCacheTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        original = cache.DATA_CACHE_DIR
        cache.DATA_CACHE_DIR = self.folder.name
        self.addCleanup(setattr, cache, "DATA_CACHE_DIR", original)

    def test_download_then_disk_then_stale(self):
        http = FakeHttp({"u": FakeHttpResponse(200, data={"v": 1})})
        self.assertEqual(app.fetch_cached_json("u", session=http), ({"v": 1}, "network"))
        offline = FakeHttp({"u": ConnectionError("offline")})
        self.assertEqual(app.fetch_cached_json("u", session=offline), ({"v": 1}, "cache"))
        self.assertEqual(offline.calls, [])
        # Too old: downloaded again; offline, the old copy still works.
        self.assertEqual(app.fetch_cached_json("u", 0, session=offline), ({"v": 1}, "stale"))
        newer = FakeHttp({"u": FakeHttpResponse(200, data={"v": 2})})
        self.assertEqual(app.fetch_cached_json("u", session=newer, force=True),
                         ({"v": 2}, "network"))
        with self.assertRaises(RuntimeError):            # no copy, download fails
            app.fetch_cached_json("other", session=offline)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.view = app.parse_match_detail(arena_payload())
        self.view["me"] = app.select_local_player(self.view["players"], {1007})
        names = {3031: "Infinity Edge", 6672: "Kraken Slayer", 41: "Jeweled Gauntlet"}
        self.export = app.build_match_export(
            self.view, lambda cid: f"Champ{cid}",
            lambda kind, entry_id: names.get(entry_id, f"{kind} {entry_id}"),
            {1: {"rating": 2026, "rank": 1373}})

    def test_model_is_ordered_and_complete(self):
        players = self.export["players"]
        self.assertEqual(len(players), 18)
        self.assertEqual([p["placement"] for p in players],
                         sorted(p["placement"] for p in players))
        self.assertEqual([p["team"] for p in players][::3], [1, 2, 3, 4, 5, 6])
        me = [p for p in players if p["is_me"]]
        self.assertEqual(len(me), 1)
        self.assertEqual(me[0]["riot_id"], "P7#EUNE")
        self.assertEqual(players[0]["items"], ["Infinity Edge", "Kraken Slayer"])
        self.assertEqual(players[0]["augments"], ["Jeweled Gauntlet"])
        self.assertEqual(players[0]["rating"], 2026)
        self.assertEqual(self.export["queue"], "Arena")
        self.assertEqual(self.export["duration"], "23m 58s")
        json.dumps(self.export)                     # JSON-safe

    def test_text(self):
        text = app.format_match_text(self.export)
        self.assertIn("Game ID: 4016402583", text)
        self.assertIn("#1  TEAM 1", text)
        self.assertIn("#6  TEAM 6", text)
        self.assertIn("P7#EUNE (me)", text)
        self.assertIn("ELO 2026 (#1373)", text)
        self.assertIn("Items: Infinity Edge, Kraken Slayer", text)

    def test_csv(self):
        rows = list(csv.DictReader(io.StringIO(app.format_match_csv(self.export))))
        self.assertEqual(len(rows), 18)
        self.assertEqual(rows[0]["items"], "Infinity Edge | Kraken Slayer")
        self.assertEqual(rows[0]["rating"], "2026")
        self.assertEqual(rows[1]["rating"], "")


class IconFetchTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.original = cache.ICON_CACHE_DIR
        cache.ICON_CACHE_DIR = self.folder.name
        self.addCleanup(setattr, cache, "ICON_CACHE_DIR", self.original)

    def test_fallback_dead_urls_and_disk_cache(self):
        image = png_bytes()
        http = FakeHttp({"a": FakeHttpResponse(404), "b": FakeHttpResponse(200, image)})
        data, dead = app.fetch_icon_bytes(["a", "b"], session=http)
        self.assertEqual(data, image)
        self.assertEqual(dead, {"a"})
        # Second time: served from disk, no network at all.
        offline = FakeHttp()
        self.assertEqual(app.fetch_icon_bytes(["a", "b"], {"a"}, offline)[0], image)
        self.assertEqual(offline.calls, [])

    def test_transient_errors_are_not_marked_dead(self):
        http = FakeHttp({"a": FakeHttpResponse(503), "b": ConnectionError("down")})
        self.assertEqual(app.fetch_icon_bytes(["a", "b"], session=http), (None, set()))

    def test_non_image_is_dead_and_not_cached(self):
        http = FakeHttp({"a": FakeHttpResponse(200, b"<html>blocked</html>")})
        self.assertEqual(app.fetch_icon_bytes(["a"], session=http), (None, {"a"}))
        self.assertEqual(os.listdir(self.folder.name), [])


class UpdateScriptTests(unittest.TestCase):
    def test_script_restarts_old_version_on_failure(self):
        script = app.build_update_script(1234, r"C:\Apps\LAT%1\LeagueArenaTools.exe",
                                         r"C:\Apps\new.exe", r"C:\Apps\.failed", "1.04")
        self.assertIn("goto failed", script)
        self.assertIn(r'>"C:\Apps\.failed" echo 1.04', script)
        self.assertLess(script.index(":failed"), script.index(":restart"))
        self.assertIn(r"LAT%%1", script)               # % escaped for cmd
        self.assertNotIn("timeout", script)            # fails without a console
        self.assertIn(r'"%SystemRoot%\System32\find.exe"', script)

    def test_marker_is_consumed_once(self):
        with tempfile.TemporaryDirectory() as folder:
            original = updater.get_base_dir
            updater.get_base_dir = lambda: folder
            try:
                self.assertIsNone(app.consume_update_failed_marker())
                with open(app.update_failed_marker_path(), "w") as marker:
                    marker.write("1.04\r\n")
                self.assertEqual(app.consume_update_failed_marker(), "1.04")
                self.assertIsNone(app.consume_update_failed_marker())
            finally:
                updater.get_base_dir = original


class HistoryAppTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.original_path = config.CONFIG_PATH
        self.original_cache = cache.ICON_CACHE_DIR
        self.original_data_cache = cache.DATA_CACHE_DIR
        config.CONFIG_PATH = os.path.join(self.folder.name, "config.json")
        cache.ICON_CACHE_DIR = os.path.join(self.folder.name, "icons")
        cache.DATA_CACHE_DIR = os.path.join(self.folder.name, "data")
        self.http = FakeHttp()
        self.original_requests = http_module._requests_module
        http_module._requests_module = self.http
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = app.App(self.root)
        self.root.after_cancel(self.app._poll_after_id)
        self.app.bot.start = lambda: True
        self.app.bot.stop = lambda: True

    def tearDown(self):
        # Run callbacks still queued with after(0); left pending they fire
        # against the next test's interpreter as "invalid command name".
        self.root.update()
        self.app._final_close()
        http_module._requests_module = self.original_requests
        config.CONFIG_PATH = self.original_path
        cache.ICON_CACHE_DIR = self.original_cache
        cache.DATA_CACHE_DIR = self.original_data_cache
        self.folder.cleanup()

    def pump(self, predicate, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return True
            time.sleep(0.01)
        return predicate()

    def test_metadata_failure_is_retried(self):
        a = self.app
        self.http.routes = {app.ITEMS_DATA_URL: ConnectionError("offline"),
                            app.ARENA_DATA_URL: ConnectionError("offline"),
                            app.CHERRY_AUGMENTS_URL: ConnectionError("offline")}
        a._ensure_history_metadata()
        self.assertTrue(self.pump(lambda: not a._history_loading_meta))
        self.assertFalse(a._history_meta_loaded)
        self.http.routes = {
            app.ITEMS_DATA_URL: FakeHttpResponse(200, data=[
                {"id": 3031, "name": "Infinity Edge", "iconPath": "/x.png"}]),
            app.ARENA_DATA_URL: FakeHttpResponse(200, data={"augments": [
                {"id": 41, "name": "Jeweled Gauntlet", "desc": "Crit %i:3031:i%"}]}),
            app.CHERRY_AUGMENTS_URL: FakeHttpResponse(200, data=[
                {"id": 41, "nameTRA": "Placeholder"}, {"id": 2010, "nameTRA": "New One"}]),
        }
        a._ensure_history_metadata()
        self.assertTrue(self.pump(lambda: a._history_meta_loaded))
        self.assertEqual(a._history_items_map[3031]["name"], "Infinity Edge")
        self.assertEqual(a._history_augments_map[41]["name"], "Jeweled Gauntlet")
        self.assertIn("Infinity Edge", a._history_augments_map[41]["tooltip"])
        self.assertEqual(a._history_augments_map[2010]["name"], "New One")

    def test_arena_filter(self):
        a = self.app
        games = [{"gameId": 1, "queueId": 1750, "gameMode": "CHERRY"},
                 {"gameId": 2, "queueId": 420, "gameMode": "CLASSIC"},
                 {"gameId": 3, "queueId": 1740, "gameMode": "CHERRY"}]
        a._fill_history_list([app.summarize_history_game(g) for g in games])
        self.assertEqual([s["game_id"] for s in a._match_summaries], [1, 3])
        self.assertEqual(a.history_status_var.get(), "2 Arena games (of 3 loaded)")
        a.history_list.selection_set(1)                 # game 3
        a.var_history_arena_only.set(False)
        self.assertEqual(a.history_list.size(), 3)
        self.assertEqual(a.history_list.curselection(), (2,))   # still game 3
        self.assertFalse(a.live_config["history_arena_only"])

    def test_rating_errors_are_retried_and_non_arena_is_skipped(self):
        a = self.app
        rank_url = f"{app.ARENASWEATS_BASE_URL}/api/player_rank"
        self.http.routes = {rank_url: [FakeHttpResponse(503),
                                       FakeHttpResponse(200, data={"rating": 1800,
                                                                   "player_rank": 7})]}
        payload = arena_payload()
        payload["participants"] = payload["participants"][:1]
        view = app.parse_match_detail(payload)
        a._render_history_detail(view)
        self.assertTrue(self.pump(lambda: a._elo_cell_vars[1][0].get() == "err"))
        self.assertEqual(a._elo_cache, {})              # error not cached
        a._render_history_detail(view)
        self.assertTrue(self.pump(lambda: a._elo_cell_vars[1][0].get() == "1800"))
        self.assertEqual(a._elo_cell_vars[1][1].get(), "#7")
        calls = self.http.count(rank_url)
        a._render_history_detail(view)                  # cached now
        self.assertTrue(self.pump(lambda: a._elo_cell_vars[1][0].get() == "1800"))
        self.assertEqual(self.http.count(rank_url), calls)

        payload["gameMode"], payload["queueId"] = "CLASSIC", 420
        a._render_history_detail(app.parse_match_detail(payload))
        self.root.update()
        self.assertEqual(a._elo_cell_vars[1][0].get(), "-")
        self.assertEqual(self.http.count(rank_url), calls)

    def test_icon_requests_are_shared_and_failures_retried(self):
        a = self.app
        gate = threading.Event()
        image = png_bytes()

        def slow_image():
            gate.wait(2)
            return FakeHttpResponse(200, image)

        self.http.routes = {"u1": slow_image, "u2": [FakeHttpResponse(503),
                                                     FakeHttpResponse(200, image)]}
        results = []
        for _ in range(5):
            a._icon_for(["u1"], 22, results.append)
        gate.set()
        self.assertTrue(self.pump(lambda: len(results) == 5))
        self.assertTrue(all(photo is not None for photo in results))
        self.assertEqual(self.http.count("u1"), 1)

        failed = []
        a._icon_for(["u2"], 22, failed.append)
        self.assertTrue(self.pump(lambda: failed == [None]))
        a._icon_for(["u2"], 22, failed.append)          # inside retry window
        self.assertTrue(self.pump(lambda: len(failed) == 2))
        self.assertEqual(self.http.count("u2"), 1)
        a._icon_retry_at[("u2", 22)] = 0                # window elapsed
        a._icon_for(["u2"], 22, failed.append)
        self.assertTrue(self.pump(lambda: len(failed) == 3 and failed[2] is not None))

    def test_slow_detail_does_not_replace_newer_selection(self):
        a = self.app
        a.client._set_credentials("50000", "tok")
        gate = threading.Event()
        detail = arena_payload()

        class Client:
            is_connected = True

            def get(self, path):
                if path.endswith("/games/1"):
                    gate.wait(2)
                    return FakeHttpResponse(200, data=dict(detail, gameId=1))
                if path.endswith("/games/2"):
                    return FakeHttpResponse(200, data=dict(detail, gameId=2))
                return FakeHttpResponse(404)

        a.client = Client()
        a._fill_history_list([{"game_id": 1, "game_mode": "CHERRY"},
                              {"game_id": 2, "game_mode": "CHERRY"}])
        a.history_list.selection_set(0)
        a._on_history_select()
        a.history_list.selection_clear(0)
        a.history_list.selection_set(1)
        a._on_history_select()
        self.assertTrue(self.pump(lambda: (a._current_detail_view or {}).get("game_id") == 2))
        gate.set()
        self.assertTrue(self.pump(lambda: 1 in a._detail_cache))
        self.root.update()
        self.assertEqual(a._current_detail_view["game_id"], 2)

    def test_toggle_shows_final_state_after_quick_flip(self):
        variable = tk.BooleanVar(value=True)
        toggle = app.NeoToggle(self.root, variable)
        variable.set(False)
        variable.set(True)
        self.pump(lambda: False, 0.4)                   # let any tween finish
        self.assertEqual(toggle._t, 1.0)

    def test_full_build_wraps_onto_two_rows(self):
        parent = tk.Frame(self.root, bg=app.PANEL_BG)
        entries = [{"name": f"Item {i}", "urls": [], "tooltip": "", "fallback": None}
                   for i in range(7)]
        self.app._build_chips_into(parent, entries)
        rows = parent.winfo_children()
        self.assertEqual([len(row.winfo_children()) for row in rows], [6, 1])

    def test_region_is_detected_from_the_client_platform(self):
        a = self.app

        class Client:
            def get(self, path):
                return FakeHttpResponse(200, data={"platformId": "la1"})

        a.client = Client()
        a.var_history_region.set("euw")
        a._detect_arena_region()
        self.assertTrue(self.pump(lambda: a.var_history_region.get() == "lan"))
        self.assertEqual(a.live_config["arenasweats_region"], "lan")

    def history_client(self, pages):
        """Fake LCU answering match-history pages by begIndex."""
        class Client:
            is_connected = True
            calls = []

            def get(self, path):
                self.calls.append(path)
                if "begIndex=" in path:
                    begin = int(path.split("begIndex=")[1].split("&")[0])
                    games = pages.get(begin, [])
                    if games == "busy":
                        return FakeHttpResponse(500)
                    return FakeHttpResponse(200, data={"accountId": 5, "games": {"games": [
                        {"gameId": gid, "queueId": 1750, "gameMode": "CHERRY",
                         "participants": [{"championId": 103, "stats": {
                             "subteamPlacement": 1 + gid % 6, "win": gid % 6 < 3}}]}
                        for gid in games]}})
                return FakeHttpResponse(404)

        self.app.client = Client()
        self.app._connection_state = True
        return self.app.client

    def test_load_more_and_refresh_keep_older_pages(self):
        a = self.app
        pages = {0: list(range(1, 51)), 50: list(range(51, 71))}
        self.history_client(pages)
        a._load_history()
        self.assertTrue(self.pump(lambda: len(a._all_summaries) == 50))
        self.assertEqual(str(a.history_more_button["state"]), "normal")
        pages[50] = "busy"                                  # a failed page keeps the list
        a._load_history(more=True)
        self.assertTrue(self.pump(lambda: not a._history_loading))
        self.assertEqual(len(a._all_summaries), 50)
        self.assertEqual(a.history_list.size(), 50)
        pages[50] = list(range(51, 71))
        a._load_history(more=True)
        self.assertTrue(self.pump(lambda: len(a._all_summaries) == 70))
        self.assertTrue(a._history_exhausted)              # last page was short
        self.assertEqual(str(a.history_more_button["state"]), "disabled")
        pages[0] = [0] + list(range(1, 50))                # one new game played
        a._load_history()
        self.assertTrue(self.pump(lambda: len(a._all_summaries) == 71))
        self.assertEqual([s["game_id"] for s in a._all_summaries[:2]], [0, 1])
        self.assertEqual(len({s["game_id"] for s in a._all_summaries}), 71)

    def test_stats_overview_is_shown_after_loading(self):
        a = self.app
        a._fill_history_list([arena_summary(i, 1 + i % 6, augments=[41]) for i in range(12)])
        self.assertEqual(a._history_detail_mode, "stats")
        shown = label_texts(a.history_detail_inner)
        self.assertIn("Arena overview", shown)
        self.assertIn("last 12 Arena games", shown)
        self.assertIn("BEST AUGMENTS  (taken 2+ times)", shown)
        # Picking a game switches the panel to that game.
        a._render_history_detail(app.parse_match_detail(arena_payload()))
        self.assertEqual(a._history_detail_mode, "game")

    def test_live_lobby_appears_during_the_game_and_goes_after(self):
        a = self.app
        a.client = type("Client", (), {"is_connected": True,
                                       "get": lambda self, path: FakeHttpResponse(404)})()
        rank_url = f"{app.ARENASWEATS_BASE_URL}/api/player_rank"
        self.http.routes = {
            app.LIVE_CLIENT_PLAYERS_URL: [ConnectionError("loading"), FakeHttpResponse(200, data=[
                {"riotId": "Me#EUW", "championName": "Ahri"},
                {"riotId": "Rival#EUW", "championName": "Fizz"}])],
            app.LIVE_CLIENT_STATS_URL: FakeHttpResponse(200, data={"gameMode": "CHERRY",
                                                                   "gameTime": 30}),
            app.LIVE_CLIENT_ACTIVE_NAME_URL: FakeHttpResponse(200, data="Me#EUW"),
            rank_url: FakeHttpResponse(200, data=ARENASWEATS_SAMPLE),
        }
        a._on_gameflow_phase("InProgress")
        self.assertTrue(self.pump(lambda: a._live_view is not None, timeout=6))
        self.assertTrue(a.history_list.get(0).startswith("LIVE"))
        self.assertEqual(a._live_view["me"]["name"], "Me")
        self.assertTrue(self.pump(lambda: self.http.count(rank_url) == 2))   # prefetched
        a.history_list.selection_set(0)
        a._on_history_select()
        self.assertTrue(a._current_detail_view["live"])
        shown = label_texts(a.history_detail_inner)
        self.assertIn("LEVEL", shown)                     # a running game has no damage
        self.assertNotIn("MY AUGMENTS", shown)
        self.assertTrue(self.pump(lambda: a._elo_cell_vars[2][0].get() == "2198"))
        self.assertEqual(self.http.count(rank_url), 2)    # served from the prefetch
        a._on_gameflow_phase("EndOfGame")
        self.assertIsNone(a._live_view)
        self.assertEqual(a.history_list.size(), 0)
        self.assertEqual(a._history_detail_mode, "stats")

    def test_metadata_comes_from_disk_and_refreshes_for_unknown_ids(self):
        a = self.app
        routes = {
            app.ITEMS_DATA_URL: FakeHttpResponse(200, data=[{"id": 3031, "name": "Infinity Edge"}]),
            app.ARENA_DATA_URL: FakeHttpResponse(200, data={"augments": []}),
            app.CHERRY_AUGMENTS_URL: FakeHttpResponse(200, data=[]),
        }
        self.http.routes = dict(routes)
        a._ensure_history_metadata()
        self.assertTrue(self.pump(lambda: a._history_meta_loaded))
        self.assertTrue(a._history_meta_network)
        # Next session: names load from disk, no download.
        a._history_meta_loaded, a._history_meta_network = False, False
        a._history_items_map.clear()
        calls = len(self.http.calls)
        a._ensure_history_metadata()
        self.assertTrue(self.pump(lambda: a._history_meta_loaded))
        self.assertEqual(len(self.http.calls), calls)
        self.assertEqual(a._resolve_entry(3031, "item")["name"], "Infinity Edge")
        # An id the saved copy lacks (new patch) triggers one fresh download.
        self.http.routes[app.ITEMS_DATA_URL] = FakeHttpResponse(200, data=[
            {"id": 3031, "name": "Infinity Edge"}, {"id": 9999, "name": "New Item"}])
        self.assertEqual(a._resolve_entry(9999, "item")["name"], "Item 9999")
        self.assertTrue(self.pump(lambda: 9999 in a._history_items_map))
        self.assertEqual(a._resolve_entry(9999, "item")["name"], "New Item")
        a._resolve_entry(4242, "item")                    # still unknown: no loop
        self.root.update()
        self.assertFalse(a._history_loading_meta)

    def test_tray_check_marks_read_the_config_off_the_ui_thread(self):
        a = self.app
        checked = a._tray_toggle_checked("auto_accept")
        a.var_accept.set(True)
        result = []
        worker = threading.Thread(target=lambda: result.append(checked(None)))
        worker.start()
        worker.join(2)
        self.assertEqual(result, [True])

        seen = []

        class Icon:
            def update_menu(self):
                seen.append(a.live_config["auto_accept"])

        a._tray_toggle_action("var_accept")(Icon(), None)
        self.assertTrue(self.pump(lambda: seen))
        self.assertEqual(seen, [False])                   # menu rebuilt after the change

    def test_window_geometry_is_restored(self):
        a = self.app
        a._window_state = {"geometry": "1000x720+40+50", "maximized": False}
        self.root.update()
        a._final_close()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = app.App(self.root)
        self.root.after_cancel(self.app._poll_after_id)
        self.root.update_idletasks()
        self.assertTrue(self.root.geometry().startswith("1000x720"))

    def test_history_rows_fit_the_list(self):
        a = self.app
        row = a._format_history_row(dict(arena_summary(1, 2), kda="12/10/15"))
        self.assertLessEqual(len(row), int(a.history_list["width"]))
        self.assertTrue(row.startswith("#2 "))
        self.assertRegex(row, r"\d\d-\d\d \d\d:\d\d$")      # date visible at the end
        other = a._format_history_row({"queue_id": 450, "game_mode": "ARAM", "win": True,
                                       "kda": "1/2/3", "duration_s": 900,
                                       "creation": 1_760_000_000_000})
        self.assertTrue(other.startswith("W "))
        self.assertTrue(other.endswith("ARAM"))
        self.assertLessEqual(len(other), int(a.history_list["width"]))

    def test_copy_and_export(self):
        a = self.app
        view = app.parse_match_detail(arena_payload())
        a._copy_match(view)
        self.assertIn("Game ID: 4016402583", self.root.clipboard_get())
        for extension in (".txt", ".csv", ".json"):
            path = os.path.join(self.folder.name, "match" + extension)
            original = filedialog.asksaveasfilename
            filedialog.asksaveasfilename = lambda **_kw: path
            try:
                a._export_match(view)
            finally:
                filedialog.asksaveasfilename = original
            with open(path, encoding="utf-8-sig") as exported:
                content = exported.read()
            if extension == ".json":
                self.assertEqual(len(json.loads(content)["players"]), 18)
            elif extension == ".csv":
                self.assertEqual(len(content.strip().splitlines()), 19)
            else:
                self.assertIn("#1  TEAM 1", content)


if __name__ == "__main__":
    unittest.main()
