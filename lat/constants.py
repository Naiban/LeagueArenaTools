"""Values shared by several modules: version, title, Bravery and game phases."""


__version__ = "1.03"
APP_TITLE = "LeagueArenaTools"

# Gameflow phases while a game runs ("Reconnect": the client lost the game
# connection, the game itself goes on).
GAME_RUNNING_PHASES = frozenset({"InProgress", "Reconnect"})

BRAVERY_LABEL = "Bravery"
BRAVERY_KEY = "__BRAVERY__"
