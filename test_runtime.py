import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest

import LeagueArenaTools as app
from lat import config


class FakeResponse:
    def __init__(self, status_code=204, data=None):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


class FakeClient:
    """Records LCU calls; routes map (METHOD, path) to a response."""

    def __init__(self):
        self.calls = []
        self.routes = {}
        self._lock = threading.Lock()

    def _do(self, method, path, body=None):
        with self._lock:
            self.calls.append((method, path, body))
        response = self.routes.get((method, path))
        if callable(response):
            response = response(body)
        return response or FakeResponse(204)

    def get(self, path):
        return self._do("GET", path)

    def post(self, path, json=None):
        return self._do("POST", path, json)

    def put(self, path, json=None):
        return self._do("PUT", path, json)

    def patch(self, path, json=None):
        return self._do("PATCH", path, json)

    def websocket_target(self):
        return None, None

    def sent(self, method, path):
        with self._lock:
            return [body for m, p, body in self.calls if m == method and p == path]


def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class BotTestCase(unittest.TestCase):
    def make_bot(self, **overrides):
        self.client = FakeClient()
        self.logs = []
        self.applied = []
        champions = app.ChampionData()
        champions.name_to_id = {"ahri": 103, "fizz": 105}
        champions.id_to_name = {103: "Ahri", 105: "Fizz"}
        champions.names_sorted = ["Ahri", "Fizz"]
        champions.loaded = True
        config = app._merge_config(overrides)
        bot = app.LeagueArenaToolsBot(
            self.client, self.logs.append, config, champions,
            settings_callback=lambda keys, detail: self.applied.append((keys, detail)),
        )
        # Activate without starting the websocket thread.
        bot._running = True
        bot._stop_event = threading.Event()
        self.addCleanup(bot.stop)
        return bot


class SettingsWhileRunningTests(BotTestCase):
    def test_stopped_bot_reports_saved(self):
        bot = self.make_bot()
        bot._running = False
        self.assertEqual(bot.describe_setting_change("auto_pick")[0], "saved")

    def test_turning_off_auto_accept_cancels_delayed_accept(self):
        bot = self.make_bot(auto_accept=True, accept_instant=False, accept_delay_ms=300)
        bot._on_ready_check_event({"state": "InProgress", "playerResponse": "None"})
        self.assertTrue(bot._accept_pending)
        bot.config["auto_accept"] = False
        state, detail = bot.describe_setting_change("auto_accept")
        self.assertEqual((state, detail), ("active", "the pending accept was cancelled"))
        time.sleep(0.5)
        self.assertEqual(self.client.sent("POST", "/lol-matchmaking/v1/ready-check/accept"), [])

    def test_delayed_accept_respects_auto_accept_turned_off(self):
        bot = self.make_bot(auto_accept=True, accept_instant=False, accept_delay_ms=200)
        bot._on_ready_check_event({"state": "InProgress", "playerResponse": "None"})
        bot.config["auto_accept"] = False  # no status call: the worker must still check
        time.sleep(0.4)
        self.assertEqual(self.client.sent("POST", "/lol-matchmaking/v1/ready-check/accept"), [])

    def test_enabling_auto_accept_accepts_open_ready_check(self):
        bot = self.make_bot(auto_accept=False, accept_instant=True)
        open_check = {"state": "InProgress", "playerResponse": "None"}
        self.client.routes[("GET", "/lol-matchmaking/v1/ready-check")] = FakeResponse(200, open_check)
        bot._on_ready_check_event(open_check)
        bot.config["auto_accept"] = True
        state, detail = bot.describe_setting_change("auto_accept")
        self.assertEqual(state, "active")
        self.assertIn("open ready check", detail)
        self.assertTrue(wait_until(
            lambda: self.client.sent("POST", "/lol-matchmaking/v1/ready-check/accept")))

    def test_accept_delay_change_is_pending_until_next_ready_check(self):
        bot = self.make_bot(auto_accept=True, accept_instant=False, accept_delay_ms=2000)
        bot._on_ready_check_event({"state": "InProgress", "playerResponse": "None"})
        bot.config["accept_delay_ms"] = 100
        self.assertEqual(bot.describe_setting_change("accept_delay_ms")[0], "pending")
        self.assertEqual(bot.pending_settings(), {"accept_delay_ms": "ready_check"})
        # Further updates of the same ready check do not count as "next".
        bot._on_ready_check_event({"state": "InProgress", "playerResponse": "None"})
        self.assertEqual(self.applied, [])
        bot._on_ready_check_event({"state": "Invalid", "playerResponse": "None"})
        bot._on_ready_check_event({"state": "InProgress", "playerResponse": "None"})
        self.assertEqual(self.applied[0][0], ["accept_delay_ms"])
        self.assertEqual(bot.pending_settings(), {})

    def test_enabling_requeue_in_lobby_starts_search(self):
        bot = self.make_bot(auto_requeue_enabled=False)
        bot._last_phase = "Lobby"
        bot._requeue_suppressed = True
        self.client.routes[("GET", "/lol-lobby/v2/lobby/matchmaking/search-state")] = \
            FakeResponse(200, {"isCurrentlyInQueue": False})
        bot.config["auto_requeue_enabled"] = True
        state, detail = bot.describe_setting_change("auto_requeue_enabled")
        self.assertEqual((state, detail), ("active", "starting matchmaking from this lobby"))
        self.assertTrue(wait_until(
            lambda: self.client.sent("POST", "/lol-lobby/v2/lobby/matchmaking/search")))

    def test_pick_priority_after_lock_applies_next_champ_select(self):
        bot = self.make_bot(auto_pick=True)
        bot._last_phase = "ChampSelect"
        bot._cs_kind_status = {"pick": "done"}
        self.assertEqual(bot.describe_setting_change("pick_priority")[0], "pending")
        bot._handle_phase_change("InProgress")
        self.assertEqual(self.applied, [])
        bot._handle_phase_change("ChampSelect")
        self.assertEqual(self.applied[0][0], ["pick_priority"])

    def test_lobby_message_text_is_read_at_send_time(self):
        bot = self.make_bot(lobby_message_enabled=True, lobby_message_text="first")
        bot._last_phase = "ChampSelect"
        self.client.routes[("GET", "/lol-chat/v1/conversations")] = \
            FakeResponse(200, [{"type": "championSelect", "id": "room"}])
        self.assertTrue(bot._send_champ_select_message())
        bot.config["lobby_message_text"] = "second"
        self.assertEqual(bot.describe_setting_change("lobby_message_text")[0], "active")
        path = "/lol-chat/v1/conversations/room/messages"
        self.assertTrue(wait_until(lambda: self.client.sent("POST", path), timeout=3))
        self.assertEqual(self.client.sent("POST", path)[0]["body"], "second")
        self.assertEqual(bot.describe_setting_change("lobby_message_text")[0], "pending")


class ChampSelectWorkerTests(BotTestCase):
    def test_confirm_delay_follows_live_settings(self):
        bot = self.make_bot(auto_pick=True, pick_mode="normal", delay_ms=5000)
        result = {}

        def run():
            started = time.monotonic()
            result["ok"] = bot._sleep_before_action(99_000, "pick")
            result["elapsed"] = time.monotonic() - started

        worker = threading.Thread(target=run)
        worker.start()
        time.sleep(0.2)
        bot.config["pick_mode"] = "instant"
        worker.join(2)
        self.assertTrue(result["ok"])
        self.assertLess(result["elapsed"], 1.0)

    def test_turning_off_auto_pick_aborts_the_lock(self):
        bot = self.make_bot(auto_pick=True, pick_mode="normal", delay_ms=5000)
        result = {}
        worker = threading.Thread(
            target=lambda: result.setdefault("ok", bot._sleep_before_action(99_000, "pick")))
        worker.start()
        time.sleep(0.2)
        bot.config["auto_pick"] = False
        worker.join(2)
        self.assertFalse(result["ok"])

    def test_dodge_during_delay_cancels_pick_without_blocking_events(self):
        bot = self.make_bot(auto_pick=True, pick_mode="normal", delay_ms=3000,
                            pick_priority=["Ahri"])
        bot._last_phase = "ChampSelect"
        session = {
            "localPlayerCellId": 1,
            "timer": {"phase": "BAN_PICK", "adjustedTimeLeftInPhase": 30_000},
            "actions": [[{"id": 7, "actorCellId": 1, "type": "pick",
                          "completed": False, "isInProgress": True}]],
        }
        worker = threading.Thread(target=bot._champ_select_loop, args=(bot._stop_event,))
        worker.start()
        bot._queue_champ_select(session)
        self.assertTrue(wait_until(lambda: bot._cs_inflight_kind == "pick"))
        started = time.monotonic()
        bot._handle_phase_change("Lobby")   # the websocket thread is not blocked
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertTrue(wait_until(lambda: bot._cs_inflight_kind is None))
        self.assertEqual(self.client.sent("POST", "/lol-champ-select/v1/session/actions/7/complete"), [])
        bot.stop()
        worker.join(2)

    def test_mailbox_skips_stale_sessions(self):
        bot = self.make_bot()
        handled, gate = [], threading.Event()

        def slow_handler(session):
            handled.append(session["n"])
            if session["n"] == 1:
                gate.wait(2)

        bot._handle_champ_select = slow_handler
        worker = threading.Thread(target=bot._champ_select_loop, args=(bot._stop_event,))
        worker.start()
        bot._queue_champ_select({"n": 1})
        self.assertTrue(wait_until(lambda: handled == [1]))
        bot._queue_champ_select({"n": 2})
        bot._queue_champ_select({"n": 3})
        gate.set()
        self.assertTrue(wait_until(lambda: handled == [1, 3]))
        time.sleep(0.1)
        self.assertEqual(handled, [1, 3])
        bot.stop()
        worker.join(2)


class ClientAndConfigTests(unittest.TestCase):
    def test_parse_lockfile(self):
        self.assertEqual(app.parse_lockfile("LeagueClient:1234:50123:s3cret:https"), ("50123", "s3cret"))
        self.assertIsNone(app.parse_lockfile("garbage"))
        self.assertIsNone(app.parse_lockfile(""))

    def test_disconnected_client_raises_cleanly(self):
        with self.assertRaises(app.LCUNotFoundError):
            app.LCUClient().get("/x")

    def test_refresh_keeps_credentials_through_a_slow_answer(self):
        client = app.LCUClient()
        client._set_credentials("50123", "tok")
        client.probe = lambda timeout=3: False
        client._find_credentials = lambda: ("50123", "tok")
        self.assertFalse(client.refresh())
        self.assertTrue(client.is_connected)

        def gone():
            raise app.LCUNotFoundError("gone")

        client._find_credentials = gone
        self.assertFalse(client.refresh())
        self.assertFalse(client.is_connected)

    def test_refresh_picks_up_new_credentials_after_client_restart(self):
        client = app.LCUClient()
        client._set_credentials("50123", "old")
        probes = iter([False, True])
        client.probe = lambda timeout=3: next(probes)
        client._find_credentials = lambda: ("60000", "new")
        self.assertTrue(client.refresh())
        self.assertEqual((client.port, client.token), ("60000", "new"))
        url, header = client.websocket_target()
        self.assertEqual(url, "wss://127.0.0.1:60000/")
        self.assertTrue(header.startswith("Authorization: Basic "))

    def test_config_save_is_atomic_and_corrupt_config_is_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            original = config.CONFIG_PATH
            config.CONFIG_PATH = os.path.join(folder, "config.json")
            try:
                store = app.load_profile_store()
                store["profiles"]["Default"]["delay_ms"] = 1234
                self.assertTrue(app.save_profile_store(store))
                self.assertFalse(os.path.exists(config.CONFIG_PATH + ".tmp"))
                self.assertEqual(app.load_profile_store()["profiles"]["Default"]["delay_ms"], 1234)

                with open(config.CONFIG_PATH, "w", encoding="utf-8") as f:
                    f.write('{"profiles": {"Default": {"delay')   # truncated write
                self.assertEqual(app.load_profile_store()["profiles"]["Default"]["delay_ms"], 500)
                backups = [n for n in os.listdir(folder) if ".broken-" in n]
                self.assertEqual(len(backups), 1)
            finally:
                config.CONFIG_PATH = original

    def test_heavy_modules_are_not_imported_at_startup(self):
        code = ("import sys, LeagueArenaTools; "
                "print([m for m in ('requests', 'psutil', 'websocket', 'pystray', 'PIL') "
                "if m in sys.modules])")
        output = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                cwd=os.path.dirname(os.path.abspath(__file__)), check=True)
        self.assertEqual(output.stdout.strip(), "[]")


class AppStatusTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.original_path = config.CONFIG_PATH
        config.CONFIG_PATH = os.path.join(self.folder.name, "config.json")
        with open(config.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"profiles": {"Default": {"hover_enabled": False}}}, f)
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = app.App(self.root)
        self.root.after_cancel(self.app._poll_after_id)
        self.app.bot.start = lambda: True
        self.app.bot.stop = lambda: True

    def finish_poll(self, connected):
        self.app._finish_poll_connection(connected, False)
        # It schedules the next poll; cancel it so none outlives the test.
        self.root.after_cancel(self.app._poll_after_id)

    def tearDown(self):
        self.root.update()      # run queued after(0) callbacks before destroy
        self.app._final_close()
        config.CONFIG_PATH = self.original_path
        self.folder.cleanup()

    def test_problems_are_reported(self):
        a = self.app
        a.var_hover.set(True)
        self.assertEqual(a._setting_problem("hover_enabled"), "no hover champion selected")
        a.var_lobby_message_enabled.set(True)
        a.var_lobby_message_text.set("  ")
        self.assertEqual(a._setting_problem("lobby_message_text"), "the message text is empty")
        a.champion_data.name_to_id = {"ahri": 103}
        a.champion_data.loaded = True
        a.var_pick.set(True)
        a._on_pick_change(["Ahri", "Bravery", "Ahrii"])
        self.assertEqual(a._setting_problem("pick_priority"), "unknown champion in the pick list: Ahrii")
        a.var_accept.set(True)
        a.var_accept_instant.set(False)
        a.var_accept_delay_ms.set(9500)
        self.assertIn("outlast", a._setting_problem("accept_delay_ms"))
        a._refresh_settings_summary()
        self.assertEqual(a.settings_summary_var.get(), "4 issues")

    def test_change_while_stopped_and_running(self):
        a = self.app
        a._report_setting_change("auto_pick")
        self.assertIn("applies when the bot starts", a.settings_status_var.get())
        a.bot_running = True
        a.bot.describe_setting_change = lambda key: ("pending", "applies next champ select")
        a.bot.pending_settings = lambda: {"auto_pick": "champ_select"}
        a._report_setting_change("auto_pick")
        self.assertEqual(a.settings_status_var.get(), "Auto Pick OFF: applies next champ select")
        self.assertEqual(a.settings_summary_var.get(), "1 pending")
        a.bot.pending_settings = lambda: {}
        a._on_settings_applied(["auto_pick"], "now in effect (new champ select)")
        self.root.update()
        self.assertEqual(a._setting_states["auto_pick"][0], "active")
        self.assertEqual(a.settings_summary_var.get(), "All active")

    def test_disconnect_grace_and_auto_resume(self):
        a = self.app
        a.client._set_credentials("50123", "tok")
        self.finish_poll(True)
        a._start_bot("Bot started.")
        self.assertTrue(a.bot_running)
        self.finish_poll(False)          # one slow answer
        self.assertTrue(a.bot_running)
        self.assertTrue(a._connection_state)
        self.finish_poll(False)          # really gone
        self.assertFalse(a.bot_running)
        self.assertTrue(a._resume_bot_on_connect)
        self.assertEqual(a.bot_state_var.get(), "Waiting for League")
        self.assertFalse(a._profile_action_allowed())
        self.finish_poll(True)
        self.assertTrue(a.bot_running)
        self.assertFalse(a._resume_bot_on_connect)

    def test_stop_button_cancels_auto_resume(self):
        a = self.app
        a._enter_resume_wait()
        a._toggle_bot()
        self.assertFalse(a._resume_bot_on_connect)
        self.assertEqual(a.bot_state_var.get(), "Stopped")


if __name__ == "__main__":
    unittest.main()
