"""Guards for the package layout (see README, "Code layout")."""
import importlib
import os
import pkgutil
import subprocess
import sys
import unittest

import LeagueArenaTools as app
import lat
from lat.ui.app import App

REPO = os.path.dirname(os.path.abspath(__file__))


def all_modules():
    return [info.name for info in pkgutil.walk_packages(lat.__path__, "lat.")]


class LayoutTests(unittest.TestCase):
    def test_every_module_imports(self):
        for name in all_modules():
            with self.subTest(module=name):
                importlib.import_module(name)

    def test_core_modules_do_not_need_tkinter(self):
        # Bot, client, config and history data stay usable (and testable)
        # without the UI; only lat.ui imports tkinter.
        core = [name for name in all_modules() if not name.startswith("lat.ui")]
        code = ("import sys, importlib; "
                f"[importlib.import_module(m) for m in {core!r}]; "
                "print('tkinter' in sys.modules)")
        output = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                cwd=REPO, check=True)
        self.assertEqual(output.stdout.strip(), "False")

    def test_app_parts_do_not_override_each_other(self):
        # App is assembled from mixins; a method name defined twice would
        # silently hide one of them.
        owners = {}
        for klass in App.__mro__[:-1]:
            for name, value in vars(klass).items():
                if name.startswith("__") and name != "__init__":
                    continue
                with self.subTest(name=name):
                    self.assertNotIn(name, owners,
                                     f"{name} is defined in {owners.get(name)} and {klass.__name__}")
                owners[name] = klass.__name__

    def test_mixins_live_in_lat_ui(self):
        for klass in App.__mro__[1:-1]:
            self.assertTrue(klass.__module__.startswith("lat.ui."), klass)
            self.assertTrue(klass.__name__.endswith("Mixin"), klass)

    def test_entry_point_re_exports_resolve(self):
        for name in app.__all__:
            with self.subTest(name=name):
                self.assertTrue(hasattr(app, name))

    def test_config_stays_next_to_the_entry_point(self):
        from lat import config, paths
        self.assertEqual(paths.get_base_dir(), REPO)
        self.assertEqual(config.CONFIG_PATH,
                         os.path.join(REPO, "league_arena_tools_config.json"))


if __name__ == "__main__":
    unittest.main()
