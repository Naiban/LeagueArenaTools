import unittest

import LeagueArenaTools as app
from lat.skins import parse_skin_catalog, sanitize_skin_choices
from test_runtime import BotTestCase, FakeResponse

MY_SELECTION = "/lol-champ-select/v1/session/my-selection"

SKINS_JSON = {
    "103000": {"id": 103000, "name": "Ahri", "isBase": True,
               "splashPath": "/lol-game-data/assets/ASSETS/Characters/Ahri/Skins/Base/Images/ahri_splash_centered_0.jpg",
               "chromas": []},
    "103001": {"id": 103001, "name": "Dynasty Ahri", "isBase": False,
               "splashPath": "/lol-game-data/assets/ASSETS/Characters/Ahri/Skins/Skin01/Images/ahri_splash_centered_1.jpg",
               "chromas": []},
    "103015": {"id": 103015, "name": "Arcade Ahri", "isBase": False,
               "chromas": [{"id": 103016, "name": "Ruby", "colors": ["#D33528", "#D33528"]},
                           {"id": 103017, "name": "Pearl", "colors": ["#ECF9F8"]}]},
    "105000": {"id": 105000, "name": "Fizz", "isBase": True, "chromas": []},
}


class CatalogTests(unittest.TestCase):
    def test_only_owned_skins_and_chromas_are_listed(self):
        catalog = parse_skin_catalog(SKINS_JSON, {103015, 103016})
        self.assertEqual([s["id"] for s in catalog[103]], [103000, 103015])
        self.assertEqual([c["id"] for c in catalog[103][1]["chromas"]], [103016])
        self.assertEqual(catalog[103][1]["chromas"][0]["color"], "#D33528")
        self.assertEqual([s["id"] for s in catalog[105]], [105000])

    def test_splash_uses_communitydragon(self):
        catalog = parse_skin_catalog(SKINS_JSON, {103001})
        url = catalog[103][1]["image_urls"][0]
        self.assertTrue(url.startswith("https://raw.communitydragon.org/"))
        self.assertTrue(url.endswith("skins/skin01/images/ahri_splash_centered_1.jpg"))

    def test_choices_are_sanitized(self):
        self.assertEqual(
            sanitize_skin_choices({
                "103": {"skin_id": 103015, "name": "Arcade Ahri"},
                "105": {"skin_id": 103015},          # skin of another champion
                "x": {"skin_id": 1},
                "86": "garbage",
            }),
            {"103": {"skin_id": 103015, "name": "Arcade Ahri"}})
        self.assertEqual(app._merge_config({"skin_choices": None})["skin_choices"], {})


class AutoSkinTests(BotTestCase):
    def session(self, champion_id=103, skin_id=103000, pick_done=True):
        return {
            "localPlayerCellId": 1,
            "timer": {"phase": "FINALIZATION", "adjustedTimeLeftInPhase": 30_000},
            "myTeam": [{"cellId": 1, "championId": champion_id, "selectedSkinId": skin_id}],
            "actions": [[{"id": 7, "actorCellId": 1, "type": "pick",
                          "completed": pick_done, "isInProgress": not pick_done}]],
        }

    def make_skin_bot(self, **overrides):
        return self.make_bot(skin_apply_enabled=True, skin_choices={
            "103": {"skin_id": 103015, "name": "Arcade Ahri"}}, **overrides)

    def test_chosen_skin_is_selected_once_after_lock(self):
        bot = self.make_skin_bot()
        bot._handle_champ_select(self.session())
        bot._handle_champ_select(self.session())          # player changed it back by hand
        self.assertEqual(self.client.sent("PATCH", MY_SELECTION), [{"selectedSkinId": 103015}])
        self.assertTrue(any("Arcade Ahri selected for Ahri" in line for line in self.logs))

    def test_nothing_is_sent_before_the_pick_is_locked(self):
        bot = self.make_skin_bot()
        bot._handle_champ_select(self.session(pick_done=False))
        self.assertEqual(self.client.sent("PATCH", MY_SELECTION), [])

    def test_champion_without_a_choice_or_disabled_is_left_alone(self):
        bot = self.make_skin_bot()
        bot._handle_champ_select(self.session(champion_id=105, skin_id=105000))
        self.assertEqual(self.client.sent("PATCH", MY_SELECTION), [])
        bot = self.make_bot(skin_choices={"103": {"skin_id": 103015, "name": "Arcade Ahri"}})
        bot._handle_champ_select(self.session())
        self.assertEqual(self.client.sent("PATCH", MY_SELECTION), [])

    def test_failed_selection_is_retried_then_given_up(self):
        bot = self.make_skin_bot()
        self.client.routes[("PATCH", MY_SELECTION)] = FakeResponse(500)
        for _ in range(5):
            bot._handle_champ_select(self.session())
        self.assertEqual(len(self.client.sent("PATCH", MY_SELECTION)), 3)
        self.assertTrue(any("could not select" in line for line in self.logs))

    def test_new_choice_mid_champ_select_is_applied(self):
        bot = self.make_skin_bot()
        bot._handle_champ_select(self.session())
        bot.config["skin_choices"] = {"103": {"skin_id": 103001, "name": "Dynasty Ahri"}}
        bot._last_phase = "ChampSelect"
        bot._cs_skin_reset = True
        bot._handle_champ_select(self.session(skin_id=103015))
        self.assertEqual(self.client.sent("PATCH", MY_SELECTION)[-1], {"selectedSkinId": 103001})


if __name__ == "__main__":
    unittest.main()
