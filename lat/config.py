"""Settings: defaults, profiles, statistics and the saved window position,
stored in league_arena_tools_config.json."""

import os
import re
import sys
import json
import copy
import time
from datetime import datetime

from lat.constants import BRAVERY_LABEL
from lat.paths import get_base_dir


CONFIG_PATH = os.path.join(get_base_dir(), "league_arena_tools_config.json")

PROFILE_STORE_VERSION = 1

MIN_DELAY_MS = 1

DEFAULT_CONFIG = {
    "auto_accept": False,
    "accept_instant": False,
    "accept_delay_ms": 0,
    "auto_ban": False,
    "auto_pick": False,
    "ban_priority": [],
    "pick_priority": [],
    "hover_enabled": False,
    "hover_champion": "",
    "pick_mode": "normal",       # "normal" | "instant" | "bravery"
    "delay_ms": 500,
    "favorites": [],
    "favorites_expanded": False,
    "push_enabled": False,
    "push_topic": "",
    "push_events": {
        "accept": False,
        "ban": False,
        "dodge": False,
        "game_loaded": False,
        "game_end": False,
    },
    "lobby_message_enabled": False,
    "lobby_message_text": "",
    "custom_status_text": "",
    "auto_requeue_enabled": False,
    "arenasweats_region": "euw",
    "history_stat_column": "damage",
    "history_arena_only": True,
    "minimize_to_tray": True,
    "auto_update_enabled": True,
}

DEFAULT_STATS = {
    "accepted": 0,
    "picked": 0,
    "games": 0,
    "dodged": 0,
}

STAT_ITEMS = (
    ("accepted", "MATCHES ACCEPTED"),
    ("picked", "PICKS LOCKED"),
    ("games", "GAMES PLAYED"),
    ("dodged", "DODGES"),
)


# Settings the bot reads while running, shown in the Live Settings status.
BOT_SETTING_LABELS = {
    "auto_accept": "Auto Accept",
    "accept_instant": "Instant accept",
    "accept_delay_ms": "Accept delay",
    "auto_ban": "Auto Ban",
    "ban_priority": "Ban priority",
    "auto_pick": "Auto Pick",
    "pick_priority": "Pick priority",
    "pick_mode": "Pick confirmation",
    "delay_ms": "Confirm delay",
    "hover_enabled": "Hover",
    "hover_champion": "Hover champion",
    "auto_requeue_enabled": "Auto Re-queue",
    "lobby_message_enabled": "Champ select message",
    "lobby_message_text": "Champ select message text",
    "push_enabled": "Phone notifications",
    "push_topic": "Notification topic",
    "push_events": "Notification events",
}
# Free-text and spinbox fields change per keystroke/click; report them once
# the value settles instead of once per character.
TYPED_SETTING_KEYS = frozenset({
    "accept_delay_ms", "delay_ms", "lobby_message_text", "push_topic",
})
# One key per validation group, used for the full check on bot start.
SETTING_CHECK_KEYS = (
    "auto_accept", "auto_ban", "auto_pick", "hover_enabled",
    "lobby_message_enabled", "push_enabled",
)
# Ready checks close after roughly ten seconds.
READY_CHECK_SAFE_DELAY_MS = 9000


def _sanitize_stats(value):
    stats = dict(DEFAULT_STATS)
    if isinstance(value, dict):
        for key in stats:
            try:
                number = int(value.get(key, 0))
            except (TypeError, ValueError):
                number = 0
            stats[key] = max(0, min(number, 10_000_000))
    return stats


_GEOMETRY_RE = re.compile(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)")


def parse_window_geometry(text, min_size=(920, 700)):
    """Tk geometry "WxH+X+Y" -> (w, h, x, y), sizes clamped to min_size;
    None for anything else (including the 1x1 of an unmapped window)."""
    match = _GEOMETRY_RE.fullmatch(str(text or "").strip())
    if not match:
        return None
    width, height, x, y = (int(part) for part in match.groups())
    if width < 200 or height < 200 or width > 20000 or height > 20000:
        return None
    return max(width, min_size[0]), max(height, min_size[1]), x, y


def _sanitize_window(value):
    window = {"geometry": "", "maximized": False}
    if isinstance(value, dict):
        if parse_window_geometry(value.get("geometry")):
            window["geometry"] = str(value["geometry"]).strip()
        window["maximized"] = value.get("maximized") is True
    return window


def window_title_visible(x, y, width):
    """True when part of a window's title bar at (x, y) lies on a monitor,
    so a position saved on a now-disconnected screen is not restored."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32")
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HMONITOR
        for px in (x + 60, x + width // 2, x + width - 60):
            if user32.MonitorFromPoint(wintypes.POINT(px, y + 12), 0):  # DEFAULTTONULL
                return True
        return False
    except Exception:
        return True


def _merge_config(saved=None):
    saved = saved if isinstance(saved, dict) else {}
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg.update(saved)
    if "delay_ms" not in saved and "min_delay" in saved:
        cfg["delay_ms"] = max(MIN_DELAY_MS, int(float(saved.get("min_delay", 0.5)) * 1000))
    if cfg.get("pick_mode") == "bravery":
        priority = list(cfg.get("pick_priority", []))
        if BRAVERY_LABEL not in priority:
            priority.append(BRAVERY_LABEL)
        cfg["pick_priority"] = priority
        cfg["pick_mode"] = "instant"
    if isinstance(cfg.get("push_events"), dict):
        merged_events = copy.deepcopy(DEFAULT_CONFIG["push_events"])
        merged_events.update(cfg["push_events"])
        cfg["push_events"] = merged_events
    else:
        cfg["push_events"] = copy.deepcopy(DEFAULT_CONFIG["push_events"])
    return cfg


def _preserve_unreadable_config():
    """Keep a copy of a config that failed to parse instead of letting the
    next save overwrite the user's profiles with defaults."""
    backup = f"{CONFIG_PATH}.broken-{datetime.now():%Y%m%d-%H%M%S}"
    try:
        os.replace(CONFIG_PATH, backup)
    except OSError:
        pass


def load_profile_store():
    raw = None
    for attempt in range(2):
        if not os.path.exists(CONFIG_PATH):
            break
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            break
        except ValueError:
            _preserve_unreadable_config()
            break
        except OSError:
            # Usually a short-lived lock (antivirus, sync client): retry once.
            if attempt == 0:
                time.sleep(0.2)

    profiles = {}
    active_profile = "Default"
    if isinstance(raw, dict) and isinstance(raw.get("profiles"), dict):
        for name, profile_config in raw["profiles"].items():
            clean_name = str(name).strip()
            if clean_name and isinstance(profile_config, dict):
                profiles[clean_name] = _merge_config(profile_config)
        requested_active = str(raw.get("active_profile") or "").strip()
        if requested_active in profiles:
            active_profile = requested_active
    elif isinstance(raw, dict):
        profiles[active_profile] = _merge_config(raw)

    if not profiles:
        profiles[active_profile] = _merge_config()
    if active_profile not in profiles:
        active_profile = next(iter(profiles))
    return {
        "format_version": PROFILE_STORE_VERSION,
        "active_profile": active_profile,
        "profiles": profiles,
        "stats": _sanitize_stats(raw.get("stats") if isinstance(raw, dict) else None),
        "window": _sanitize_window(raw.get("window") if isinstance(raw, dict) else None),
    }


def save_profile_store(store):
    # Write a sibling temp file and swap it in, so a crash or power loss in
    # the middle of a save can never leave a truncated config behind.
    temp_path = f"{CONFIG_PATH}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, CONFIG_PATH)
        return True
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        return False
