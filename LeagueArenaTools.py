"""LeagueArenaTools - entry point.

The application lives in the `lat` package (see README, "Code layout").
`python LeagueArenaTools.py` starts it, and PyInstaller builds the EXE from
this file. The names below are re-exported so `import LeagueArenaTools`
keeps working for the tests and existing scripts; new code imports from
the `lat` modules directly.
"""

from lat.bot import HOVER_RETRY_S, LeagueArenaToolsBot
from lat.config import (
    _merge_config, _sanitize_window, load_profile_store, parse_window_geometry,
    save_profile_store, window_title_visible,
)
from lat.constants import APP_TITLE, __version__
from lat.history.cache import fetch_cached_json, fetch_icon_bytes
from lat.history.data import (
    ARENA_DATA_URL, ARENASWEATS_BASE_URL, ARENASWEATS_REGIONS, CHERRY_AUGMENTS_URL,
    ITEMS_DATA_URL, LIVE_CLIENT_ACTIVE_NAME_URL, LIVE_CLIENT_PLAYERS_URL,
    LIVE_CLIENT_STATS_URL, PLATFORM_TO_AS_REGION, QUEUE_LABELS, build_live_view,
    compute_arena_stats, format_game_end_message, format_rating_tooltip, is_arena_game,
    is_finished_game, match_history_path, parse_live_players, parse_match_detail,
    rating_from_response, select_local_player, shorten_riot_id, summarize_history_game,
)
from lat.history.export import build_match_export, format_match_csv, format_match_text
from lat.lcu import ChampionData, LCUClient, LCUNotFoundError, _normalize, parse_lockfile
from lat.notify import NTFY_BASE_URL, PushNotifier
from lat.ui.app import App, main
from lat.ui.theme import PANEL_BG
from lat.ui.widgets import NeoToggle
from lat.updater import (
    UPDATE_ASSET_NAME, UpdateError, _download_update, _release_update, build_update_script,
    consume_update_failed_marker, update_failed_marker_path,
)

__all__ = [
    "APP_TITLE", "ARENASWEATS_BASE_URL", "ARENASWEATS_REGIONS", "ARENA_DATA_URL", "App",
    "CHERRY_AUGMENTS_URL", "ChampionData", "HOVER_RETRY_S", "ITEMS_DATA_URL", "LCUClient",
    "LCUNotFoundError", "LIVE_CLIENT_ACTIVE_NAME_URL", "LIVE_CLIENT_PLAYERS_URL",
    "LIVE_CLIENT_STATS_URL", "LeagueArenaToolsBot", "NTFY_BASE_URL", "NeoToggle", "PANEL_BG",
    "PLATFORM_TO_AS_REGION", "PushNotifier", "QUEUE_LABELS", "UPDATE_ASSET_NAME",
    "UpdateError", "__version__", "_download_update", "_merge_config", "_normalize",
    "_release_update", "_sanitize_window", "build_live_view", "build_match_export",
    "build_update_script", "compute_arena_stats", "consume_update_failed_marker",
    "fetch_cached_json", "fetch_icon_bytes", "format_game_end_message", "format_match_csv",
    "format_match_text", "format_rating_tooltip", "is_arena_game", "is_finished_game",
    "load_profile_store", "main", "match_history_path", "parse_live_players",
    "parse_lockfile", "parse_match_detail", "parse_window_geometry", "rating_from_response",
    "save_profile_store", "select_local_player", "shorten_riot_id", "summarize_history_game",
    "update_failed_marker_path", "window_title_visible",
]


if __name__ == "__main__":
    main()
