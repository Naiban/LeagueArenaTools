import threading
import time
import unittest

import LeagueArenaTools as app
from lat import bot as bot_module
from lat import http as http_module
from test_runtime import BotTestCase, FakeResponse, wait_until

ACCEPT = "/lol-matchmaking/v1/ready-check/accept"
MY_SELECTION = "/lol-champ-select/v1/session/my-selection"


class NoNetwork:
    """requests stand-in: the Live Client API is never reachable."""

    def Session(self):
        return self

    def get(self, *_args, **_kwargs):
        raise ConnectionError("no game running")


class BotFixTestCase(BotTestCase):
    def setUp(self):
        self.original_requests = http_module._requests_module
        http_module._requests_module = NoNetwork()
        self.addCleanup(setattr, http_module, "_requests_module", self.original_requests)


class ReadyCheckTests(BotFixTestCase):
    def test_manual_decline_cancels_the_delayed_accept(self):
        bot = self.make_bot(auto_accept=True, accept_instant=False, accept_delay_ms=250)
        bot._on_ready_check_event({"state": "InProgress", "playerResponse": "None"})
        self.assertTrue(bot._accept_pending)
        bot._on_ready_check_event({"state": "InProgress", "playerResponse": "Declined"})
        self.assertFalse(bot._accept_pending)
        time.sleep(0.45)
        self.assertEqual(self.client.sent("POST", ACCEPT), [])

    def test_declined_ready_check_is_never_accepted(self):
        bot = self.make_bot(auto_accept=True, accept_instant=True)
        bot._on_ready_check_event({"state": "InProgress", "playerResponse": "Declined"})
        time.sleep(0.1)
        self.assertEqual(self.client.sent("POST", ACCEPT), [])

    def test_closed_ready_check_cancels_the_delayed_accept(self):
        bot = self.make_bot(auto_accept=True, accept_instant=False, accept_delay_ms=250)
        bot._on_ready_check_event({"state": "InProgress", "playerResponse": "None"})
        bot._on_ready_check_event({"state": "Invalid", "playerResponse": "None"})
        time.sleep(0.45)
        self.assertEqual(self.client.sent("POST", ACCEPT), [])

    def test_open_ready_check_is_still_accepted(self):
        bot = self.make_bot(auto_accept=True, accept_instant=False, accept_delay_ms=50)
        bot._on_ready_check_event({"state": "InProgress", "playerResponse": "None"})
        self.assertTrue(wait_until(lambda: self.client.sent("POST", ACCEPT)))


class ChampSelectFixTests(BotFixTestCase):
    def session(self, phase="BAN_PICK", **selection):
        return {
            "localPlayerCellId": 1,
            "timer": {"phase": phase, "adjustedTimeLeftInPhase": 30_000},
            "mySelection": selection,
            "actions": [[{"id": 7, "actorCellId": 1, "type": "pick",
                          "completed": False, "isInProgress": True}]],
        }

    def test_unowned_priority_champion_is_skipped(self):
        bot = self.make_bot(auto_pick=True, pick_mode="instant", pick_priority=["Ahri", "Fizz"])
        self.client.routes[("GET", "/lol-champ-select/v1/pickable-champions")] = \
            FakeResponse(200, [105])                       # Ahri (103) not owned
        self.client.routes[("GET", "/lol-champ-select/v1/session")] = FakeResponse(
            200, {"actions": [[{"id": 7, "completed": True}]]})
        self.assertTrue(bot._perform_action(7, "pick", ["Ahri", "Fizz"], 30_000, instant=True))
        picked = {body.get("championId")
                  for body in self.client.sent("PATCH", "/lol-champ-select/v1/session/actions/7")
                  if body and "championId" in body}
        self.assertEqual(picked, {105})
        # The list is asked once per champ select, not on every attempt.
        bot._perform_action(7, "pick", ["Ahri", "Fizz"], 30_000, instant=True)
        self.assertEqual(len(self.client.sent("GET", "/lol-champ-select/v1/pickable-champions")), 1)

    def test_champion_hovered_by_another_player_is_still_picked(self):
        session = self.session()
        session["actions"][0].append({"id": 8, "actorCellId": 2, "type": "pick", "championId": 103,
                                      "completed": False, "isInProgress": True})
        bot = self.make_bot()
        self.assertNotIn(103, bot._session_unavailable_champions(session))
        session["actions"][0][1]["completed"] = True
        self.assertIn(103, bot._session_unavailable_champions(session))

    def test_empty_pickable_list_does_not_block_the_pick(self):
        bot = self.make_bot(auto_pick=True, pick_mode="instant")
        self.client.routes[("GET", "/lol-champ-select/v1/pickable-champions")] = FakeResponse(200, [])
        self.client.routes[("GET", "/lol-champ-select/v1/session")] = FakeResponse(
            200, {"actions": [[{"id": 7, "completed": True}]]})
        self.assertTrue(bot._perform_action(7, "pick", ["Ahri"], 30_000, instant=True))

    def test_hover_is_sent_once_and_not_after_a_manual_change(self):
        bot = self.make_bot(hover_enabled=True, hover_champion="Ahri")
        bot._handle_champ_select(self.session(phase="PLANNING"))
        bot._handle_champ_select(self.session(phase="PLANNING"))   # quick second update
        self.assertEqual(len(self.client.sent("PATCH", MY_SELECTION)), 1)
        # The client shows the hover; then the player hovers Fizz by hand.
        bot._handle_champ_select(self.session(phase="PLANNING", championPickIntent=103))
        bot._last_hover_attempt.clear()
        bot._handle_champ_select(self.session(phase="PLANNING", championPickIntent=105))
        self.assertEqual(len(self.client.sent("PATCH", MY_SELECTION)), 1)

    def test_unconfirmed_hover_is_retried_after_a_pause(self):
        bot = self.make_bot(hover_enabled=True, hover_champion="Ahri")
        bot._handle_champ_select(self.session(phase="PLANNING"))
        bot._last_hover_attempt[None] -= app.HOVER_RETRY_S
        bot._handle_champ_select(self.session(phase="PLANNING"))
        self.assertEqual(len(self.client.sent("PATCH", MY_SELECTION)), 2)

    def test_missing_timer_does_not_break_champ_select(self):
        bot = self.make_bot()
        session = self.session()
        session["timer"] = None
        bot._handle_champ_select(session)


class PhaseTests(BotFixTestCase):
    def test_starting_mid_game_or_reconnecting_is_not_a_new_game(self):
        stats = []
        bot = self.make_bot()
        bot._stats_callback = stats.append
        bot._handle_phase_change("InProgress")              # bot started mid-game
        bot._handle_phase_change("Reconnect")
        bot._handle_phase_change("InProgress")
        self.assertEqual(stats, [])
        for phase in ("EndOfGame", "Lobby", "ChampSelect", "GameStart", "InProgress"):
            bot._handle_phase_change(phase)
        self.assertEqual(stats, ["games"])

    def test_game_end_notification_waits_for_the_finished_game(self):
        bot = self.make_bot(push_enabled=True, push_topic="topic",
                            push_events={"game_end": True})
        sent = []
        bot.notifier._send = lambda topic, title, message, tag: sent.append((title, message))
        original = bot_module.GAME_END_REPORT_INTERVAL_S
        bot_module.GAME_END_REPORT_INTERVAL_S = 0.01
        self.addCleanup(setattr, bot_module, "GAME_END_REPORT_INTERVAL_S", original)
        self.client.routes[("GET", "/lol-gameflow/v1/session")] = FakeResponse(
            200, {"gameData": {"gameId": 77}})
        old_game = {"gameId": 76, "participants": [{"stats": {}}]}
        finished = {"gameId": 77, "queueId": 1750, "gameMode": "CHERRY", "gameDuration": 1100,
                    "participants": [{"championId": 103, "stats": {
                        "subteamPlacement": 2, "win": True, "kills": 8, "deaths": 4,
                        "assists": 6, "totalDamageDealtToChampions": 23456}}]}
        answers = [old_game, old_game, old_game, finished]
        history = app.match_history_path(0, 0)
        self.client.routes[("GET", history)] = lambda _body: FakeResponse(
            200, {"games": {"games": [answers.pop(0) if len(answers) > 1 else answers[0]]}})
        bot._last_phase = "GameStart"
        bot._handle_phase_change("InProgress")
        self.assertTrue(wait_until(lambda: bot._game_end_ids[0] == 77))
        bot._handle_phase_change("EndOfGame")
        self.assertTrue(wait_until(lambda: sent, timeout=3))
        title, message = sent[0]
        self.assertEqual(title, "Victory - #2")
        self.assertEqual(message, "Arena · Ahri · 8/4/6 KDA · 23 456 damage · 18m 20s")
        self.assertEqual(len(sent), 1)

    def test_no_history_polling_without_the_notification(self):
        bot = self.make_bot(push_enabled=False)
        bot._last_phase = "GameStart"
        bot._handle_phase_change("InProgress")
        bot._handle_phase_change("EndOfGame")
        time.sleep(0.1)
        threads = [t.name for t in threading.enumerate()]
        self.assertNotIn("LeagueArenaTools-GameEnd", threads)


class NotifierTests(unittest.TestCase):
    def test_topic_cannot_change_the_url(self):
        posted = []

        class Http:
            def post(self, url, **_kwargs):
                posted.append(url)
                return FakeResponse(200)

        original = http_module._requests_module
        http_module._requests_module = Http()
        try:
            app.PushNotifier({}, lambda _m: None)._send("a/b?c#d", "t", "m", "bell")
        finally:
            http_module._requests_module = original
        self.assertEqual(posted, [f"{app.NTFY_BASE_URL}/a%2Fb%3Fc%23d"])

    def test_wants_needs_switch_event_and_topic(self):
        config = {"push_enabled": True, "push_topic": " t ", "push_events": {"game_end": True}}
        notifier = app.PushNotifier(config, lambda _m: None)
        self.assertTrue(notifier.wants("game_end"))
        self.assertFalse(notifier.wants("accept"))
        config["push_topic"] = "  "
        self.assertFalse(notifier.wants("game_end"))


if __name__ == "__main__":
    unittest.main()
