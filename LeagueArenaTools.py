import os
import re
import sys
import json
import copy
import time
import random
import base64
import secrets
import threading
from datetime import datetime

import tkinter as tk
from tkinter import ttk, scrolledtext, simpledialog, messagebox

import requests
import psutil

try:
    import pystray
    from PIL import Image
    TRAY_AVAILABLE = True
except Exception:
    TRAY_AVAILABLE = False

try:
    import websocket
except ImportError:
    websocket = None

__version__ = "1.00"
APP_TITLE = "LeagueArenaTools"
WEBSOCKET_RECONNECT_DELAY = 0.25
BRAVERY_LABEL = "Bravery"
BRAVERY_KEY = "__BRAVERY__"


# =============================================================================
# 0) PATHS / CONFIG
# =============================================================================

def get_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_resource_path(filename):
    resource_dir = getattr(sys, "_MEIPASS", get_base_dir())
    return os.path.join(resource_dir, filename)


CONFIG_PATH = os.path.join(get_base_dir(), "league_arena_tools_config.json")
LEGACY_CONFIG_PATH = os.path.join(get_base_dir(), "shinkansen_config.json")
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
    "poll_interval": 0.25,
    "push_enabled": False,
    "push_topic": "",
    "push_events": {
        "accept": False,
        "ban": False,
        "dodge": False,
        "game_loaded": False,
    },
    "lobby_message_enabled": False,
    "lobby_message_text": "",
    "custom_status_text": "",
    "auto_requeue_enabled": False,
    "minimize_to_tray": True,
}


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


def load_profile_store():
    raw = None
    for path in (CONFIG_PATH, LEGACY_CONFIG_PATH):
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            break
        except Exception:
            continue

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
    }


def save_profile_store(store):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


# =============================================================================
# 1) LCU CLIENT - connection to the local League of Legends client API
# =============================================================================

PORT_RE = re.compile(r"--app-port=([0-9]+)")
TOKEN_RE = re.compile(r"--remoting-auth-token=([\w-]+)")


class LCUNotFoundError(Exception):
    """Raised when a running LoL client cannot be found."""


class LCUClient:
    def __init__(self):
        self.port = None
        self.token = None
        self.base_url = None
        self.session = requests.Session()
        # The League client uses a local self-signed certificate. Keep TLS
        # encryption, but do not disable verification globally for ntfy or
        # other external requests.
        self.session.verify = False
        self._openapi_cache = None

    @property
    def is_connected(self):
        return self.base_url is not None

    def connect(self):
        port, token = self._find_credentials()
        self.port = port
        self.token = token
        self.base_url = f"https://127.0.0.1:{port}"
        auth = base64.b64encode(f"riot:{token}".encode()).decode()
        self.session.headers.update({"Authorization": f"Basic {auth}"})

    def disconnect(self):
        self.port = None
        self.token = None
        self.base_url = None
        self._openapi_cache = None
        self.session.headers.pop("Authorization", None)

    def _find_credentials(self):
        cmdline = self._get_league_commandline()
        port_match = PORT_RE.search(cmdline)
        token_match = TOKEN_RE.search(cmdline)
        if not port_match or not token_match:
            raise LCUNotFoundError(
                "No connection data found. Is the League of Legends client running?"
            )
        return port_match.group(1), token_match.group(1)

    @staticmethod
    def _get_league_commandline():
        for process in psutil.process_iter(["name", "cmdline"]):
            try:
                name = (process.info.get("name") or "").lower()
                if name not in ("leagueclientux.exe", "leagueclientuxheadless.exe"):
                    continue
                commandline = " ".join(process.info.get("cmdline") or [])
                if "--app-port" in commandline:
                    return commandline
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        raise LCUNotFoundError(
            "Cannot find the LeagueClientUx.exe process. Start the League client and try again."
        )

    def get(self, path):
        return self.session.get(self.base_url + path, timeout=5)

    def post(self, path, json=None):
        return self.session.post(self.base_url + path, json=json, timeout=5)

    def put(self, path, json=None):
        return self.session.put(self.base_url + path, json=json, timeout=5)

    def patch(self, path, json=None):
        return self.session.patch(self.base_url + path, json=json, timeout=5)

    def request(self, method, path, json=None):
        return self.session.request(method.upper(), self.base_url + path, json=json, timeout=5)

    def get_openapi(self):
        if self._openapi_cache is not None:
            return self._openapi_cache
        for path in ("/swagger/v3/openapi.json", "/swagger/v2/swagger.json"):
            try:
                r = self.get(path)
                if r.status_code == 200:
                    self._openapi_cache = r.json()
                    return self._openapi_cache
            except Exception:
                pass
        self._openapi_cache = {}
        return self._openapi_cache

    def websocket_url(self):
        if not self.port:
            return None
        return f"wss://127.0.0.1:{self.port}/"

    def authorization_header(self):
        if not self.token:
            return None
        auth = base64.b64encode(f"riot:{self.token}".encode()).decode()
        return f"Authorization: Basic {auth}"


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


# =============================================================================
# 1b) PUSH NOTIFICATIONS - sent to the phone via ntfy.sh (free, no account
#     needed). The "topic" is a random, private identifier - it acts as a
#     simple verification: only a device subscribed to that exact topic
#     receives notifications from this app instance.
# =============================================================================

NTFY_BASE_URL = "https://ntfy.sh"
LIVE_CLIENT_STATS_URL = "https://127.0.0.1:2999/liveclientdata/gamestats"


def generate_push_topic():
    return "leaguearenatools-" + secrets.token_hex(8)


class PushNotifier:
    """Thin wrapper over ntfy.sh. notify() checks the global switch and the
    per-event switch, then sends the request on a separate thread so it
    never blocks the bot loop (accept/ban need to stay fast)."""

    EVENT_META = {
        "accept": ("Match accepted", "video_game"),
        "ban": ("Champion banned", "no_entry_sign"),
        "dodge": ("Lobby dodge", "warning"),
        "game_loaded": ("Game loaded", "white_check_mark"),
    }

    def __init__(self, config, log_callback):
        self.config = config
        self.log = log_callback

    def notify(self, event_key, message):
        if not self.config.get("push_enabled"):
            return
        events = self.config.get("push_events", {})
        if not events.get(event_key, False):
            return
        topic = (self.config.get("push_topic") or "").strip()
        if not topic:
            return
        title, tag = self.EVENT_META.get(event_key, (APP_TITLE, "bell"))
        threading.Thread(target=self._send, args=(topic, title, message, tag), daemon=True).start()

    def send_test(self):
        """Sends a test notification bypassing the per-event switches (used by
        the "Test" button in the UI), but still requires notifications to be
        enabled and a topic to be set."""
        if not self.config.get("push_enabled"):
            return False
        topic = (self.config.get("push_topic") or "").strip()
        if not topic:
            return False
        threading.Thread(
            target=self._send, args=(topic, f"{APP_TITLE} - test", "This is a test notification.", "bell"),
            daemon=True,
        ).start()
        return True

    def _send(self, topic, title, message, tag):
        try:
            resp = requests.post(
                f"{NTFY_BASE_URL}/{topic}",
                data=message.encode("utf-8"),
                headers={"Title": title, "Priority": "default", "Tags": tag},
                timeout=5,
            )
            if resp.status_code >= 300:
                self.log(f"Push notification failed ({resp.status_code}) for topic '{topic}'")
        except Exception as e:
            self.log(f"Push notification error: {e}")


# =============================================================================
# 2) CHAMPION DATA - shared between the bot and the GUI (dropdown/search)
# =============================================================================

class ChampionData():
    def __init__(self):
        self.name_to_id = {}
        self.id_to_name = {}
        self.names_sorted = []
        self.loaded = False

    def load(self, client: LCUClient) -> bool:
        try:
            r = client.get("/lol-game-data/assets/v1/champion-summary.json")
            if r.status_code != 200:
                return False
            mapping, id_to_name, names = {}, {}, []
            for champ in r.json():
                cid = champ.get("id", -1)
                if cid <= 0:
                    continue
                name = champ.get("name", "")
                alias = champ.get("alias", "")
                if name:
                    mapping[_normalize(name)] = cid
                    id_to_name[cid] = name
                    names.append(name)
                if alias:
                    mapping[_normalize(alias)] = cid
            self.name_to_id = mapping
            self.id_to_name = id_to_name
            self.names_sorted = sorted(set(names), key=str.lower)
            self.loaded = True
            return True
        except Exception:
            return False

    def resolve(self, name: str):
        return self.name_to_id.get(_normalize(name))

    def label(self, champion_id: int) -> str:
        return self.id_to_name.get(champion_id, str(champion_id))


# =============================================================================
# 3) BOT - auto accept / auto ban / auto pick / hover (also works in Arena)
# =============================================================================

class LeagueArenaToolsBot:
    def __init__(self, client: LCUClient, log_callback, live_config: dict, champion_data: ChampionData):
        self.client = client
        self.log = log_callback
        self.config = live_config
        self.champion_data = champion_data
        self.notifier = PushNotifier(live_config, log_callback)

        self._running = False
        self._lifecycle_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._stop_event.set()
        self._event_thread = None
        self._active_websocket = None
        self._champ_select_pulse = threading.Event()
        self._handler_lock = threading.Lock()
        self._ready_check_lock = threading.Lock()

        self._done_actions = set()
        self._hovered_actions = set()
        self._pending_hovers = {}
        self._last_hover_attempt = {}
        self._warned_names = set()
        self._last_action_debug = ""
        self._last_hover_log = ""
        self._last_phase = None
        self._last_queue_id = None
        self._requeue_suppressed = False
        self._requeue_worker_running = False
        self._requeue_worker_lock = threading.Lock()
        self._requeue_thread = None
        self._rejoin_thread = None
        self._last_ready_check_state = None
        self._ready_check_generation = 0
        self._bravery_routes = None
        self._bravery_unavailable_logged = False
        self._champ_select_reset_done = False

        self._game_loaded_watch_thread = None
        self._game_loaded_watch_stop = threading.Event()
        self._live_client_session = requests.Session()
        self._live_client_session.verify = False

    def start(self):
        with self._lifecycle_lock:
            if self._running:
                return False
            self._running = True
            self._stop_event = threading.Event()
            stop_event = self._stop_event
            self._requeue_suppressed = False
            self._requeue_worker_running = False
            self._last_phase = None
            self._last_ready_check_state = None
            self._champ_select_reset_done = False
            self._event_thread = threading.Thread(
                target=self._event_loop,
                args=(stop_event,),
                name="LeagueArenaTools-LCU",
                daemon=True,
            )
            self._event_thread.start()
            return True

    def stop(self):
        with self._lifecycle_lock:
            if not self._running and self._stop_event.is_set():
                return False
            self._running = False
            self._stop_event.set()
            self._champ_select_pulse.set()
            self._ready_check_generation += 1
            self._requeue_suppressed = True
            self._stop_game_loaded_watch()
            websocket_connection = self._active_websocket
            self._active_websocket = None

        if websocket_connection is not None:
            try:
                websocket_connection.close()
            except Exception:
                pass

        current = threading.current_thread()
        for worker in (self._event_thread, self._requeue_thread, self._rejoin_thread):
            if worker is not None and worker is not current and worker.is_alive():
                worker.join(timeout=0.35)
        return True

    def _is_active(self, stop_event=None):
        event = stop_event or self._stop_event
        return self._running and self._stop_event is event and not event.is_set()

    def _wait(self, seconds, stop_event=None):
        event = stop_event or self._stop_event
        if event.wait(max(0.0, seconds)):
            return False
        return self._is_active(event)

    def _request_if_active(self, stop_event, method, path, json=None):
        with self._lifecycle_lock:
            if not self._is_active(stop_event):
                return None
            return method(path, json=json)

    def _event_loop(self, stop_event):
        if websocket is None:
            self.log("LCU websocket unavailable - requires websocket-client.")
            return
        self.log("Bot started. ReadyCheck and ChampSelect run event-only via the LCU websocket.")

        while self._is_active(stop_event):
            ws = None
            try:
                ws_url = self.client.websocket_url()
                auth_header = self.client.authorization_header()
                if not ws_url or not auth_header:
                    if not self._wait(0.5, stop_event):
                        return
                    continue

                ws = websocket.create_connection(
                    ws_url,
                    header=[auth_header],
                    sslopt={"cert_reqs": 0},
                    timeout=1,
                )
                with self._lifecycle_lock:
                    if not self._is_active(stop_event):
                        ws.close()
                        return
                    self._active_websocket = ws
                ws.settimeout(5)
                ws.send(json.dumps([5, "OnJsonApiEvent_lol-champ-select_v1_session"]))
                ws.send(json.dumps([5, "OnJsonApiEvent_lol-gameflow_v1_gameflow-phase"]))
                ws.send(json.dumps([5, "OnJsonApiEvent_lol-matchmaking_v1_ready-check"]))
                self._bootstrap_event_state()
                self.log("LCU websocket active - no polling needed.")

                while self._is_active(stop_event):
                    try:
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    except Exception:
                        break
                    if not raw:
                        continue
                    try:
                        event = json.loads(raw)
                    except Exception:
                        continue
                    self._handle_lcu_event(event)
            except Exception as e:
                if self._is_active(stop_event):
                    self.log(f"LCU websocket unavailable ({e}) - reconnecting.")
                    if not self._wait(WEBSOCKET_RECONNECT_DELAY, stop_event):
                        return
            finally:
                with self._lifecycle_lock:
                    if self._active_websocket is ws:
                        self._active_websocket = None
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass

    def _bootstrap_event_state(self):
        try:
            r = self.client.get("/lol-gameflow/v1/gameflow-phase")
            if r.status_code == 200:
                self._handle_phase_change(r.json())
        except Exception:
            pass
        try:
            r = self.client.get("/lol-matchmaking/v1/ready-check")
            if r.status_code == 200:
                self._handle_ready_check(r.json())
        except Exception:
            pass
        if self._last_phase == "ChampSelect":
            try:
                r = self.client.get("/lol-champ-select/v1/session")
                if r.status_code == 200:
                    self._handle_champ_select_locked(r.json())
            except Exception:
                pass

    def _handle_lcu_event(self, event):
        if not self._is_active():
            return
        if not isinstance(event, list) or len(event) < 3 or event[0] != 8:
            return
        name = event[1]
        payload = event[2] if isinstance(event[2], dict) else {}

        if name == "OnJsonApiEvent_lol-gameflow_v1_gameflow-phase":
            self._handle_phase_change(payload.get("data"))
            return

        if name == "OnJsonApiEvent_lol-matchmaking_v1_ready-check":
            data = payload.get("data")
            if isinstance(data, dict):
                self._handle_ready_check(data)
            return

        if name != "OnJsonApiEvent_lol-champ-select_v1_session":
            return

        self._champ_select_pulse.set()
        event_type = payload.get("eventType")
        if event_type == "Create":
            self._reset_champ_select_state()
        if event_type in ("Create", "Update"):
            session = payload.get("data")
            if isinstance(session, dict):
                self._handle_champ_select_locked(session)

    def _handle_phase_change(self, phase):
        if not self._is_active():
            return
        if phase == self._last_phase:
            return
        previous_phase = self._last_phase
        self._last_phase = phase
        self.log(f"Game phase: {phase}")
        self._last_ready_check_state = None
        if phase == "ChampSelect":
            self._reset_champ_select_state()
        else:
            self._champ_select_reset_done = False

        if previous_phase == "ChampSelect" and phase not in ("GameStart", "InProgress"):
            self.notifier.notify("dodge", "Lobby reset - someone dodged or champ select was interrupted.")

        if phase == "InProgress" and previous_phase != "InProgress":
            self._start_game_loaded_watch()
        elif previous_phase == "InProgress" and phase != "InProgress":
            self._stop_game_loaded_watch()

        if phase == "Lobby" and previous_phase != "Lobby":
            self._capture_queue_id()
            self._requeue_suppressed = False
            self._maybe_auto_requeue()

        if phase == "ChampSelect" and previous_phase != "ChampSelect":
            self._capture_queue_id()
            self._send_champ_select_message()

        if phase == "None" and previous_phase in ("WaitingForStats", "PreEndOfGame", "EndOfGame"):
            self._maybe_auto_rejoin_lobby()

    def _capture_queue_id(self):
        try:
            resp = self.client.get("/lol-lobby/v2/lobby")
            if resp.status_code == 200:
                queue_id = (resp.json().get("gameConfig") or {}).get("queueId")
                if isinstance(queue_id, int) and queue_id > 0:
                    self._last_queue_id = queue_id
        except Exception:
            pass

    def _send_champ_select_message(self):
        stop_event = self._stop_event
        if not self._is_active(stop_event) or not self.config.get("lobby_message_enabled"):
            return
        text = (self.config.get("lobby_message_text") or "").strip()
        if not text:
            return
        threading.Thread(
            target=self._send_champ_select_message_worker,
            args=(text, stop_event),
            name="LeagueArenaTools-LobbyMessage",
            daemon=True,
        ).start()

    def _send_champ_select_message_worker(self, text, stop_event):
        # Small initial delay - if we enter champ select faster than the
        # rest of the lobby, the message can land before other players even
        # have the chat loaded to see it.
        if not self._wait(1.0, stop_event):
            return
        # The champ select chat room is created fresh each time, so it can take
        # a moment to become available - retry a few times before giving up.
        conv_id = None
        for _ in range(8):
            if not self._is_active(stop_event):
                return
            conv_id = self._find_champ_select_conversation_id()
            if conv_id:
                break
            if not self._wait(0.5, stop_event):
                return
        if not conv_id:
            self.log("Champ select message: chat not found, skipped.")
            return
        if not self._is_active(stop_event):
            return
        try:
            resp = self.client.post(f"/lol-chat/v1/conversations/{conv_id}/messages", json={"body": text, "type": "chat"})
            if resp.status_code in (200, 201, 204):
                self.log(f"Sent champ select message: {text}")
            else:
                self.log(f"Champ select message failed (status {resp.status_code}).")
        except Exception as e:
            self.log(f"Champ select message error: {e}")

    def _maybe_auto_requeue(self):
        if not self._is_active() or not self.config.get("auto_requeue_enabled"):
            return
        self._start_requeue_worker(self._stop_event)

    def _start_requeue_worker(self, stop_event=None):
        stop_event = stop_event or self._stop_event
        if not self._is_active(stop_event):
            return
        with self._requeue_worker_lock:
            if self._requeue_worker_running:
                return
            self._requeue_worker_running = True
            self._requeue_thread = threading.Thread(
                target=self._auto_requeue_worker,
                args=(stop_event,),
                name="LeagueArenaTools-Requeue",
                daemon=True,
            )
            self._requeue_thread.start()

    def _maybe_auto_rejoin_lobby(self):
        stop_event = self._stop_event
        if not self._is_active(stop_event) or not self.config.get("auto_requeue_enabled"):
            return
        if not self._last_queue_id:
            self.log("Auto re-queue: no queue type remembered, skipping lobby rejoin.")
            return
        if self._rejoin_thread is not None and self._rejoin_thread.is_alive():
            return
        self._rejoin_thread = threading.Thread(
            target=self._auto_rejoin_lobby_worker,
            args=(stop_event,),
            name="LeagueArenaTools-Rejoin",
            daemon=True,
        )
        self._rejoin_thread.start()

    def _auto_rejoin_lobby_worker(self, stop_event):
        # Give the client a moment - it sometimes recreates the lobby itself
        # after a match ends, before we try to do it manually.
        if not self._wait(1.5, stop_event):
            return
        if not self._is_active(stop_event):
            return
        try:
            existing = self.client.get("/lol-lobby/v2/lobby")
            if existing.status_code == 200 and existing.json():
                # Lobby already exists (client recreated it itself) - the
                # regular Lobby-entry hook will handle it, nothing else to do.
                return
        except Exception:
            pass

        if not self._is_active(stop_event):
            return
        try:
            resp = self._request_if_active(
                stop_event,
                self.client.post,
                "/lol-lobby/v2/lobby",
                {"queueId": self._last_queue_id},
            )
            if resp is None:
                return
            if resp.status_code not in (200, 201, 204):
                self.log(f"Auto re-queue: failed to recreate lobby (status {resp.status_code}).")
                return
        except Exception as e:
            self.log(f"Auto re-queue: error recreating lobby: {e}")
            return

        if not self._is_active(stop_event):
            return
        self.log("Auto re-queue: lobby recreated, starting search.")
        self._requeue_suppressed = False
        self._start_requeue_worker(stop_event)

    def _is_matchmaking_searching(self, stop_event=None):
        if stop_event is not None and not self._is_active(stop_event):
            return False
        try:
            r = self.client.get("/lol-lobby/v2/lobby/matchmaking/search-state")
            if r.status_code != 200:
                return False
            data = r.json()
            in_queue = data.get("isCurrentlyInQueue")
            if isinstance(in_queue, bool):
                return in_queue
            state = str(data.get("searchState", "")).lower()
            return state not in ("", "none", "invalid", "canceled", "cancelled", "notsearching")
        except Exception:
            return False

    def _auto_requeue_worker(self, stop_event):
        # Runs for as long as we stay in the Lobby phase. Keeps trying to
        # (re)start matchmaking search - e.g. while waiting for a party member
        # to accept/ready up - instead of giving up after a few seconds. If the
        # search becomes active and then gets cancelled while we're still in
        # the lobby (i.e. no match was found), that was a manual cancel: back
        # off for this lobby session instead of immediately re-queueing again,
        # until the next game, dodge, or manual search start.
        try:
            was_active = False
            logged_started = False
            while self._is_active(stop_event) and self._last_phase == "Lobby":
                if not self.config.get("auto_requeue_enabled"):
                    return

                active_now = self._is_matchmaking_searching(stop_event)

                if self._requeue_suppressed:
                    if active_now:
                        # The user started a search on their own - resume
                        # normal auto-requeue behavior for it.
                        self._requeue_suppressed = False
                        was_active = True
                        logged_started = True
                    if not self._wait(1.5, stop_event):
                        return
                    continue

                if active_now:
                    if not logged_started:
                        self.log("Auto re-queue: match search started.")
                        logged_started = True
                    was_active = True
                    if not self._wait(1.5, stop_event):
                        return
                    continue

                if was_active:
                    self._requeue_suppressed = True
                    was_active = False
                    logged_started = False
                    self.log("Auto re-queue: search was cancelled - waiting for the next game, dodge, or manual search.")
                    if not self._wait(1.5, stop_event):
                        return
                    continue

                if not self._is_active(stop_event):
                    return
                try:
                    if self._request_if_active(
                        stop_event,
                        self.client.post,
                        "/lol-lobby/v2/lobby/matchmaking/search",
                    ) is None:
                        return
                except Exception as e:
                    self.log(f"Auto re-queue error: {e}")
                    return
                if not self._wait(2.0, stop_event):
                    return
        finally:
            with self._requeue_worker_lock:
                if self._requeue_thread is threading.current_thread():
                    self._requeue_worker_running = False
                    self._requeue_thread = None

    def _find_champ_select_conversation_id(self):
        try:
            resp = self.client.get("/lol-chat/v1/conversations")
        except Exception:
            return None
        if resp.status_code != 200:
            return None
        try:
            conversations = resp.json()
        except Exception:
            return None
        if not isinstance(conversations, list):
            return None
        for conv in conversations:
            if isinstance(conv, dict) and (conv.get("type") or "").lower() == "championselect":
                return conv.get("id")
        return None

    def _start_game_loaded_watch(self):
        self._stop_game_loaded_watch()
        stop_event = threading.Event()
        self._game_loaded_watch_stop = stop_event
        t = threading.Thread(target=self._game_loaded_watch_loop, args=(stop_event,), daemon=True)
        self._game_loaded_watch_thread = t
        t.start()

    def _stop_game_loaded_watch(self):
        if self._game_loaded_watch_thread is not None:
            self._game_loaded_watch_stop.set()
            self._game_loaded_watch_thread = None

    def _game_loaded_watch_loop(self, stop_event):
        # The LCU itself has no loading-screen percentage - "InProgress" fires
        # as soon as the lobby closes, before anyone has actually loaded into
        # the game. The Live Client Data API (game port, 127.0.0.1:2999) exposes
        # "gameTime", which stays at zero through the whole loading screen and
        # only starts ticking once ALL players are actually loaded in.
        while not stop_event.is_set() and self._running:
            try:
                r = self._live_client_session.get(LIVE_CLIENT_STATS_URL, timeout=2)
                if r.status_code == 200:
                    game_time = r.json().get("gameTime")
                    if isinstance(game_time, (int, float)) and game_time > 0.5:
                        self.notifier.notify("game_loaded", "The game has actually started - everyone loaded in.")
                        return
            except Exception:
                pass
            if stop_event.wait(1.0):
                return

    def _reset_champ_select_state(self):
        if self._champ_select_reset_done:
            return
        self._done_actions.clear()
        self._hovered_actions.clear()
        self._pending_hovers.clear()
        self._last_hover_attempt.clear()
        self._last_action_debug = ""
        self._last_hover_log = ""
        self._champ_select_reset_done = True

    def _handle_champ_select_locked(self, session=None):
        if not self._is_active():
            return
        if not self._handler_lock.acquire(blocking=False):
            return
        try:
            self._handle_champ_select(session)
        finally:
            self._handler_lock.release()

    def _handle_ready_check(self, data):
        if not self._is_active() or not self.config.get("auto_accept"):
            return
        if not self._ready_check_lock.acquire(blocking=False):
            return
        try:
            state_key = f"{data.get('state')}:{data.get('playerResponse')}"
            if state_key == self._last_ready_check_state:
                return
            self._last_ready_check_state = state_key
            if data.get("state") != "InProgress" or data.get("playerResponse") == "Accepted":
                return
            self._ready_check_generation += 1
            generation = self._ready_check_generation
            delay_ms = 0 if self.config.get("accept_instant", True) else int(self.config.get("accept_delay_ms", 0))
            if delay_ms <= 0:
                self._accept_ready_check(generation)
            else:
                self.log(f"Accepting match in {delay_ms} ms")
                stop_event = self._stop_event
                threading.Thread(
                    target=self._delayed_accept_ready_check,
                    args=(generation, delay_ms, stop_event),
                    name="LeagueArenaTools-AcceptDelay",
                    daemon=True,
                ).start()
        finally:
            self._ready_check_lock.release()

    def _delayed_accept_ready_check(self, generation, delay_ms, stop_event):
        if self._wait(delay_ms / 1000, stop_event):
            self._accept_ready_check(generation, stop_event)

    def _accept_ready_check(self, generation, stop_event=None):
        stop_event = stop_event or self._stop_event
        if not self._is_active(stop_event) or generation != self._ready_check_generation:
            return
        try:
            resp = self.client.post("/lol-matchmaking/v1/ready-check/accept")
            if resp.status_code in (200, 204):
                self.log("Match accepted")
                self.notifier.notify("accept", "Match was accepted automatically.")
        except Exception as e:
            self.log(f"Auto Accept error: {e}")

    def _handle_champ_select(self, session=None):
        if not self._is_active():
            return
        if session is None:
            r = self.client.get("/lol-champ-select/v1/session")
            if r.status_code != 200:
                return
            session = r.json()

        local_cell = session.get("localPlayerCellId")
        timer = session.get("timer", {})
        timer_ms = timer.get("adjustedTimeLeftInPhase", 99_000)
        timer_phase = timer.get("phase", "")
        my_actions = []
        unavailable = self._session_unavailable_champions(session)

        self._confirm_pending_hovers(session)

        if self.config.get("hover_enabled") and timer_phase != "BAN_PICK":
            self._send_hover(None, session=session)

        for group in session.get("actions", []):
            for action in group:
                if action.get("actorCellId") != local_cell:
                    continue
                if action.get("completed"):
                    continue

                action_id = action["id"]
                action_type = action.get("type")
                if action_type not in ("ban", "pick"):
                    continue

                # HOVER FIX: send the hover intent as early as possible (e.g. during
                # pre-ban / planning) instead of waiting for 'isInProgress', so no time
                # is wasted on hovering once the pick phase starts.
                about_to_auto_complete = (
                    ((action_type == "ban" and self.config.get("auto_ban")) or
                     (action_type == "pick" and self.config.get("auto_pick"))) and
                    not self._is_planning_phase(timer_phase)
                )

                if self.config.get("hover_enabled") and action_type == "pick" and action_id not in self._hovered_actions and not about_to_auto_complete:
                    self._send_hover(action_id, session=session)

                in_progress = action.get("isInProgress")
                if in_progress is False:
                    continue
                my_actions.append(f"{action_id}:{action_type}:progress={in_progress}:phase={timer_phase}")

                can_perform_now = (
                    action_id not in self._done_actions
                    and not self._is_planning_phase(timer_phase)
                    and (
                        (action_type == "ban" and "ban" in (timer_phase or "").lower())
                        or (action_type == "pick" and "pick" in (timer_phase or "").lower())
                    )
                )

                if action_id in self._done_actions:
                    continue
                if not can_perform_now:
                    continue

                if action_type == "ban" and self.config.get("auto_ban"):
                    ok = self._perform_action(
                        action_id, "ban", self.config.get("ban_priority", []), timer_ms,
                        instant=True, session=session, unavailable=unavailable,
                    )
                    if ok:
                        self._done_actions.add(action_id)

                elif action_type == "pick" and self.config.get("auto_pick"):
                    pick_mode = self.config.get("pick_mode")
                    instant = pick_mode == "instant"
                    ok = self._perform_action(
                        action_id, "pick", self.config.get("pick_priority", []), timer_ms,
                        instant=instant, session=session, unavailable=unavailable,
                    )
                    if ok:
                        self._done_actions.add(action_id)

        debug_line = ",".join(my_actions)
        if debug_line and debug_line != self._last_action_debug:
            self._last_action_debug = debug_line
            self.log(f"Champ select actions: {debug_line}")

    def _get_action_by_id(self, action_id):
        try:
            r = self.client.get("/lol-champ-select/v1/session")
            if r.status_code != 200:
                return None
            for group in r.json().get("actions", []):
                for action in group:
                    if action.get("id") == action_id:
                        return action
        except Exception:
            pass
        return None

    @staticmethod
    def _action_has_champion(action, champion_id):
        return bool(action and action.get("championId") == champion_id)

    @staticmethod
    def _walk_values(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from LeagueArenaToolsBot._walk_values(child)
        elif isinstance(value, list):
            for child in value:
                yield from LeagueArenaToolsBot._walk_values(child)

    def _session_unavailable_champions(self, session):
        ids = set()
        local_cell = session.get("localPlayerCellId")
        for node in self._walk_values(session):
            cid = node.get("championId")
            if isinstance(cid, int) and cid > 0:
                completed = node.get("completed")
                action_type = node.get("type")
                actor_cell = node.get("actorCellId")
                if completed or action_type == "ban" or (action_type == "pick" and actor_cell != local_cell):
                    ids.add(cid)
            cid = node.get("bannedChampionId")
            if isinstance(cid, int) and cid > 0:
                ids.add(cid)
        return ids

    def _confirm_pending_hovers(self, session):
        if not self._pending_hovers:
            return
        confirmed = []
        my_selection = session.get("mySelection") or session.get("my-selection") or {}
        my_selection_ids = set()
        if isinstance(my_selection, dict):
            for key in ("championId", "championPickIntent"):
                cid = my_selection.get(key)
                if isinstance(cid, int) and cid > 0:
                    my_selection_ids.add(cid)
        for action_id, cid in list(self._pending_hovers.items()):
            matched = action_id is None and cid in my_selection_ids
            for group in session.get("actions", []):
                for action in group:
                    if action_id is not None and action.get("id") == action_id and action.get("championId") == cid:
                        matched = True
                    elif action_id is None and action.get("championId") == cid:
                        matched = True
            if matched:
                confirmed.append(action_id)
        for action_id in confirmed:
            cid = self._pending_hovers.pop(action_id, None)
            if action_id is not None:
                self._hovered_actions.add(action_id)
            msg = f"Hover confirmed: {self.champion_data.label(cid)}"
            if msg != self._last_hover_log:
                self.log(msg)
                self._last_hover_log = msg

    @staticmethod
    def _is_planning_phase(timer_phase):
        phase = (timer_phase or "").lower()
        return phase in ("planning", "prepick", "pre-pick")

    def _send_hover(self, action_id, session=None):
        stop_event = self._stop_event
        if not self._is_active(stop_event):
            return False
        name = self.config.get("hover_champion")
        if not name:
            return False
        cid = self.champion_data.resolve(name)
        if not cid or cid <= 0:
            self.log(f"Hover: unknown champion '{name}'")
            return False
        try:
            requests_to_send = []
            if action_id is not None:
                requests_to_send.append((
                    self.client.patch,
                    f"/lol-champ-select/v1/session/actions/{action_id}",
                    {"championId": cid},
                ))
            requests_to_send.extend([
                (
                self.client.patch,
                "/lol-champ-select/v1/session/my-selection",
                {"championId": cid},
                ),
                (
                    self.client.patch,
                    "/lol-champ-select/v1/session/my-selection",
                    {"championPickIntent": cid},
                ),
            ])

            statuses = []
            for func, path, body in requests_to_send:
                if not self._is_active(stop_event):
                    return False
                resp = func(path, json=body)
                statuses.append(f"{path}:{resp.status_code}")
                if resp.status_code in (200, 204):
                    self._pending_hovers[action_id] = cid
                    msg = f"Hover sent: {name}"
                    if msg != self._last_hover_log:
                        self.log(msg)
                        self._last_hover_log = msg
                    if session is not None:
                        self._confirm_pending_hovers(session)
                    return True
            msg = f"Hover failed ({' | '.join(statuses)}) for: {name}"
            if msg != self._last_hover_log:
                self.log(msg)
                self._last_hover_log = msg
        except Exception as e:
            self.log(f"Hover error: {e}")
        return False

    def _resolve_priority_entries(self, names, allow_bravery=False):
        entries = []
        for raw in names:
            label = (raw or "").strip()
            if allow_bravery and _normalize(label) == _normalize(BRAVERY_LABEL):
                entries.append((BRAVERY_KEY, BRAVERY_LABEL))
                continue
            cid = self.champion_data.resolve(raw)
            if cid:
                entries.append((cid, self.champion_data.label(cid)))
            elif label and raw not in self._warned_names:
                self._warned_names.add(raw)
                self.log(f"Unknown champion: '{label}' - skipping in priority list")
        return entries

    def _resolve_priority_ids(self, names):
        return [entry for entry, _ in self._resolve_priority_entries(names) if isinstance(entry, int)]

    @staticmethod
    def _extract_champion_ids(payload):
        ids = set()
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = payload.get("champions") or payload.get("championIds") or payload.get("ids") or []
        else:
            items = []
        for item in items:
            if isinstance(item, int) and item > 0:
                ids.add(item)
            elif isinstance(item, dict):
                for key in ("id", "championId", "championID"):
                    cid = item.get(key)
                    if isinstance(cid, int) and cid > 0:
                        ids.add(cid)
                        break
        return ids

    def _perform_action(self, action_id, kind, priority_names, timer_ms, instant=False, session=None, unavailable=None):
        if not self._is_active():
            return False
        unavailable = unavailable or set()
        priority_entries = self._resolve_priority_entries(priority_names, allow_bravery=(kind == "pick"))
        if priority_names and not priority_entries:
            self.log(f"No recognized champions in the {kind} priority list. Check that the champion list loaded from the LoL client.")
            return False

        if priority_entries:
            for entry, label in priority_entries:
                if entry == BRAVERY_KEY:
                    if kind == "pick" and self._perform_bravery(action_id, timer_ms, instant=instant):
                        return True
                    continue
                if entry in unavailable:
                    self.log(f"{label} unavailable - checking next {kind} priority.")
                    continue
                if instant:
                    return self._instant_action(action_id, kind, entry)
                if not self._set_action_champion(action_id, entry, kind):
                    self.log(f"Failed to set champion for action '{kind}'")
                    return False
                if not self._sleep_before_action(timer_ms, instant=instant):
                    return False
                if self._complete_action(action_id, entry):
                    action_label = "Banned" if kind == "ban" else "Picked"
                    champ_label = self.champion_data.label(entry)
                    self.log(f"{action_label} champion: {champ_label}")
                    if kind == "ban":
                        self.notifier.notify("ban", f"Banned: {champ_label}")
                    return True
                self.log(f"Failed to complete action '{kind}'")
                return False

        endpoint = (
            "/lol-champ-select/v1/bannable-champions" if kind == "ban"
            else "/lol-champ-select/v1/pickable-champions"
        )
        r = self.client.get(endpoint)
        available = None
        if r.status_code == 200:
            try:
                available = self._extract_champion_ids(r.json())
            except Exception:
                available = None
        else:
            self.log(f"Cannot fetch {'bannable' if kind == 'ban' else 'pickable'} champion list ({r.status_code})")

        chosen = None
        if chosen is None:
            if available is None:
                pool = {cid for cid in self.champion_data.name_to_id.values() if cid > 0 and cid not in unavailable}
                if pool:
                    chosen = random.choice(list(pool))
            elif available:
                pool = [cid for cid in available if cid not in unavailable]
                if pool:
                    chosen = random.choice(pool)

        if not chosen or chosen <= 0:
            return False

        if instant:
            return self._instant_action(action_id, kind, chosen)

        if not self._set_action_champion(action_id, chosen, kind):
            self.log(f"Failed to set champion for action '{kind}'")
            return False

        if not self._sleep_before_action(timer_ms, instant=instant):
            return False

        if self._complete_action(action_id, chosen):
            label = "Banned" if kind == "ban" else "Picked"
            champ_label = self.champion_data.label(chosen)
            self.log(f"{label} champion: {champ_label}")
            if kind == "ban":
                self.notifier.notify("ban", f"Banned: {champ_label}")
            return True
        else:
            self.log(f"Failed to complete action '{kind}'")
            return False

    def _instant_action_legacy(self, action_id, kind, champion_id):
        stop_event = self._stop_event
        if not self._is_active(stop_event):
            return False
        paths = [
            (self.client.patch, f"/lol-champ-select/v1/session/actions/{action_id}", {"championId": champion_id, "completed": True}),
            (self.client.post, f"/lol-champ-select/v1/session/actions/{action_id}/complete", None),
            (self.client.patch, f"/lol-champ-select/v1/session/actions/{action_id}", {"championId": champion_id}),
            (self.client.patch, f"/lol-champ-select/v1/session/actions/{action_id}", {"completed": True}),
        ]
        if kind == "pick":
            paths.insert(2, (
                self.client.patch,
                "/lol-champ-select/v1/session/my-selection",
                {"championId": champion_id},
            ))

        statuses = []
        for func, path, body in paths:
            if not self._is_active(stop_event):
                return False
            try:
                resp = func(path, json=body)
                statuses.append(f"{path}:{resp.status_code}")
            except Exception as e:
                statuses.append(f"{path}:ERR {e}")

        for _ in range(6):
            action = self._get_action_by_id(action_id)
            if action and action.get("completed"):
                label = "Banned" if kind == "ban" else "Picked"
                self.log(f"{label} champion: {self.champion_data.label(champion_id)}")
                return True
            if not self._wait(0.05, stop_event):
                return False

        self.log(f"Instant {kind} unconfirmed: " + " | ".join(statuses))
        return False

    def _instant_action(self, action_id, kind, champion_id):
        stop_event = self._stop_event
        if not self._is_active(stop_event):
            return False
        primary = (
            self.client.patch,
            f"/lol-champ-select/v1/session/actions/{action_id}",
            {"championId": champion_id, "completed": True},
        )
        burst = [
            (self.client.patch, f"/lol-champ-select/v1/session/actions/{action_id}", {"championId": champion_id, "completed": True}),
            (self.client.patch, f"/lol-champ-select/v1/session/actions/{action_id}", {"completed": True, "championId": champion_id}),
            (self.client.post, f"/lol-champ-select/v1/session/actions/{action_id}/complete", None),
            (self.client.patch, f"/lol-champ-select/v1/session/actions/{action_id}", {"championId": champion_id}),
            (self.client.patch, f"/lol-champ-select/v1/session/actions/{action_id}", {"completed": True}),
            (self.client.patch, f"/lol-champ-select/v1/session/actions/{action_id}", {"championId": champion_id, "completed": True}),
            (self.client.post, f"/lol-champ-select/v1/session/actions/{action_id}/complete", None),
        ]
        if kind == "pick":
            my_selection = (
                self.client.patch,
                "/lol-champ-select/v1/session/my-selection",
                {"championId": champion_id},
            )
            burst[1:1] = [
                my_selection,
                (self.client.patch, "/lol-champ-select/v1/session/my-selection", {"championId": champion_id}),
                (self.client.patch, "/lol-champ-select/v1/session/my-selection", {"championPickIntent": champion_id}),
                my_selection,
            ]

        statuses = []
        status_lock = threading.Lock()

        def _send(func, path, body):
            if not self._is_active(stop_event):
                return
            try:
                resp = func(path, json=body)
                item = f"{path}:{resp.status_code}"
            except Exception as e:
                item = f"{path}:ERR {e}"
            with status_lock:
                statuses.append(item)

        _send(*primary)
        threads = []
        for item in burst:
            t = threading.Thread(target=_send, args=item, daemon=True)
            t.start()
            threads.append(t)

        for _ in range(10):
            if not self._is_active(stop_event):
                return False
            action = self._get_action_by_id(action_id)
            if action and action.get("completed"):
                label = "Banned" if kind == "ban" else "Picked"
                champ_label = self.champion_data.label(champion_id)
                self.log(f"{label} champion: {champ_label}")
                if kind == "ban":
                    self.notifier.notify("ban", f"Banned: {champ_label}")
                return True
            if not self._wait(0.02, stop_event):
                return False

        for t in threads:
            t.join(timeout=0.02)

        self.log(f"Instant {kind} unconfirmed: " + " | ".join(statuses))
        return False

    def _set_action_champion(self, action_id, champion_id, kind):
        stop_event = self._stop_event
        if not self._is_active(stop_event):
            return False
        paths_and_bodies = [
            (self.client.patch, f"/lol-champ-select/v1/session/actions/{action_id}", {"championId": champion_id}),
        ]
        if kind == "pick":
            paths_and_bodies.append((
                self.client.patch,
                "/lol-champ-select/v1/session/my-selection",
                {"championId": champion_id},
            ))
        statuses = []
        for func, path, body in paths_and_bodies:
            if not self._is_active(stop_event):
                return False
            try:
                resp = func(path, json=body)
                statuses.append(f"{path}:{resp.status_code}")
                if resp.status_code in (200, 204):
                    return True
            except Exception as e:
                statuses.append(f"{path}:ERR {e}")
        self.log("Champion selection unconfirmed: " + " | ".join(statuses))
        return False

    def _complete_action(self, action_id, champion_id):
        stop_event = self._stop_event
        if not self._is_active(stop_event):
            return False
        attempts = [
            (self.client.post, f"/lol-champ-select/v1/session/actions/{action_id}/complete", None),
            (self.client.patch, f"/lol-champ-select/v1/session/actions/{action_id}", {"completed": True}),
            (self.client.patch, f"/lol-champ-select/v1/session/actions/{action_id}", {"championId": champion_id, "completed": True}),
        ]
        statuses = []
        for func, path, body in attempts:
            if not self._is_active(stop_event):
                return False
            try:
                resp = func(path, json=body)
                statuses.append(f"{path}:{resp.status_code}")
                if resp.status_code in (200, 204):
                    return True
            except Exception as e:
                statuses.append(f"{path}:ERR {e}")
        self.log("Action confirmation unconfirmed: " + " | ".join(statuses))
        return False

    def _sleep_before_action(self, timer_ms, instant=False):
        if instant:
            return self._is_active()
        delay_ms_cfg = self.config.get("delay_ms", 500)
        safe_max_ms = max(0, timer_ms - 1000)
        delay_ms = min(delay_ms_cfg, safe_max_ms) if safe_max_ms > 0 else 0
        if delay_ms:
            return self._wait(delay_ms / 1000, self._stop_event)
        return self._is_active()

    @staticmethod
    def _looks_like_bravery(value):
        if isinstance(value, str):
            normalized = _normalize(value)
            return normalized in ("bravery", "arenabravery", "random", "randomchampion")
        return False

    def _complete_bravery_action(self, action_id):
        stop_event = self._stop_event
        if not self._is_active(stop_event):
            return False
        attempts = [
            (self.client.post, f"/lol-champ-select/v1/session/actions/{action_id}/complete", None),
            (self.client.patch, f"/lol-champ-select/v1/session/actions/{action_id}", {"completed": True}),
            (self.client.post, f"/lol-lobby-team-builder/champ-select/v1/session/actions/{action_id}/complete", None),
            (self.client.patch, f"/lol-lobby-team-builder/champ-select/v1/session/actions/{action_id}", {"completed": True}),
        ]
        statuses = []
        ok = False
        for func, path, body in attempts:
            if not self._is_active(stop_event):
                return False
            try:
                resp = func(path, json=body)
                statuses.append(f"{path}:{resp.status_code}")
                if resp.status_code in (200, 204):
                    ok = True
            except Exception as e:
                statuses.append(f"{path}:ERR {e}")
        if ok:
            self.log("Bravery complete sent: " + " | ".join(statuses))
        return ok

    def _perform_bravery(self, action_id, timer_ms, instant=False):
        if not self._is_active():
            return False
        if not instant:
            if not self._sleep_before_action(timer_ms, instant=False):
                return False

        candidates = [-3]

        for cid in candidates:
            if not self._is_active():
                return False
            self.log(f"Bravery: trying championId={cid} via the same mechanism as a normal instant pick")
            if self._instant_action(action_id, "pick", cid):
                return True

        self.log("BRAVERY FAILED - championId=-3 was not confirmed as completed")
        return False


# =============================================================================
# 4) FUTURISTIC MINIMAL STYLE - dark theme, purple-cyan accent
# =============================================================================

BG = "#080b12"
SIDEBAR_BG = "#0d111c"
PANEL_BG = "#121827"
PANEL_ALT = "#192135"
PANEL_BORDER = "#26324b"
FIELD_BG = "#0b101b"
ACCENT = "#7c5cff"
ACCENT_DARK = "#5b3df0"
ACCENT_ALT = "#2dd4bf"
TEXT_PRIMARY = "#f4f7ff"
TEXT_MUTED = "#91a0bd"
SUCCESS = "#34d399"
DANGER = "#fb7185"
WARNING = "#fbbf24"
AERO_DARK = ACCENT         # alias for compatibility with any references
BODY_BG = BG
TEXT_DARK = TEXT_PRIMARY
BORDER = PANEL_BORDER
WIN_W = 1080
WIN_H = 760


def _blend(c1, c2, t):
    c1, c2 = c1.lstrip("#"), c2.lstrip("#")
    r1, g1, b1 = int(c1[0:2], 16), int(c1[2:4], 16), int(c1[4:6], 16)
    r2, g2, b2 = int(c2[0:2], 16), int(c2[2:4], 16), int(c2[4:6], 16)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _ease_out_cubic(t):
    return 1 - (1 - t) ** 3


class Animator:
    """Simple tween helper for Tk widgets - advances frames via .after()."""

    def __init__(self, widget, fps=60):
        self.widget = widget
        self.fps = fps
        self._job = None

    def stop(self):
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def animate(self, duration_ms, on_frame, on_done=None, ease=_ease_out_cubic):
        self.stop()
        steps = max(1, int(duration_ms / (1000 / self.fps)))

        def _step(i=0):
            t = min(1.0, i / steps)
            try:
                on_frame(ease(t))
            except tk.TclError:
                return  # widget was destroyed during the animation
            if i < steps:
                self._job = self.widget.after(int(1000 / self.fps), lambda: _step(i + 1))
            else:
                self._job = None
                if on_done:
                    on_done()

        _step()


class AeroButton(tk.Canvas):
    """Main action button (Start/Stop bot) - soft gradient, gentle shadow,
    and a smooth animated hover/press transition instead of a hard color jump."""

    def __init__(self, parent, text, command=None, width=220, height=40, **kwargs):
        self._pad_top = 3
        self._pad_bottom = 8  # space for the shadow under the button
        canvas_h = height + self._pad_top + self._pad_bottom
        super().__init__(parent, width=width, height=canvas_h, highlightthickness=0,
                          bg=parent["bg"], cursor="hand2", **kwargs)
        self.command = command
        self.text = text
        self.width = width
        self.height = height
        self._hover_t = 0.0
        self._pressed = False
        self._animator = Animator(self)
        self.bind("<Enter>", lambda e: self._animate_hover(1.0))
        self.bind("<Leave>", lambda e: self._animate_hover(0.0))
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._draw()

    def set_text(self, text):
        self.text = text
        self._draw()

    def _animate_hover(self, target):
        start = self._hover_t

        def frame(p):
            self._hover_t = start + (target - start) * p
            self._draw()

        self._animator.animate(150, frame)

    def _on_press(self, _event):
        self._pressed = True
        self._draw()

    def _on_release(self, _event):
        was_pressed = self._pressed
        self._pressed = False
        self._draw()
        if was_pressed and self.command:
            self.command()

    def _draw(self):
        self.delete("all")
        w, h = self.width, self.height
        top_y = self._pad_top
        r = h / 2
        hover = self._hover_t

        top_c = _blend(ACCENT, ACCENT_ALT, 0.25 * hover)
        bot_c = _blend(ACCENT_DARK, ACCENT, 0.25 * hover)
        if self._pressed:
            top_c, bot_c = ACCENT_DARK, "#3d2ab8"

        # soft shadow under the button, subtly "lifting" on hover
        shadow_color = _blend(str(self["bg"]), "#000000", 0.30 + 0.10 * hover)
        shadow_y = top_y + h - 2 + 3 * hover
        self.create_oval(6, shadow_y - 5, h + 6, shadow_y + 5, fill=shadow_color, outline="")
        self.create_oval(w - h - 6, shadow_y - 5, w - 6, shadow_y + 5, fill=shadow_color, outline="")
        self.create_rectangle(r + 6, shadow_y - 5, w - r - 6, shadow_y + 5, fill=shadow_color, outline="")

        # pill gradient
        for i in range(h):
            ratio = i / h
            color = _blend(top_c, bot_c, ratio)
            self.create_line(r, top_y + i, w - r, top_y + i, fill=color)
        self.create_oval(0, top_y, h, top_y + h, fill=top_c, outline="")
        self.create_oval(w - h, top_y, w, top_y + h, fill=bot_c, outline="")

        # subtle top highlight - gives a "glassy pill" impression
        gloss = _blend(top_c, "#ffffff", 0.35)
        self.create_line(r, top_y + 2, w - r, top_y + 2, fill=gloss, width=1)

        self.create_text(w / 2, top_y + h / 2, text=self.text.upper(),
                          fill="#ffffff", font=("Segoe UI", 10, "bold"))


class NeoToggle(tk.Canvas):
    """Modern on/off pill switch bound to a tk.BooleanVar.
    The knob and track color animate smoothly between states."""

    def __init__(self, parent, variable, command=None, width=42, height=22, **kwargs):
        super().__init__(parent, width=width, height=height, highlightthickness=0,
                          bg=parent["bg"], cursor="hand2", **kwargs)
        self.variable = variable
        self.command = command
        self.width = width
        self.height = height
        self._animator = Animator(self)
        self._t = 1.0 if variable.get() else 0.0  # 0 = off, 1 = on
        self.bind("<Button-1>", self._toggle)
        self.variable.trace_add("write", lambda *_: self._sync())
        self._draw()

    def _toggle(self, event=None):
        self.variable.set(not self.variable.get())
        if self.command:
            self.command()

    def _sync(self):
        target = 1.0 if self.variable.get() else 0.0
        if abs(target - self._t) < 1e-6:
            return
        start = self._t

        def frame(p):
            self._t = start + (target - start) * p
            self._draw()

        self._animator.animate(180, frame)

    def _draw(self):
        self.delete("all")
        t = self._t
        track = _blend("#293054", ACCENT, t)
        r = self.height / 2
        self.create_oval(0, 0, self.height, self.height, fill=track, outline="")
        self.create_oval(self.width - self.height, 0, self.width, self.height, fill=track, outline="")
        self.create_rectangle(r, 0, self.width - r, self.height, fill=track, outline="")
        kx = 3 + (self.width - self.height) * t
        self.create_oval(kx, 3, kx + self.height - 6, self.height - 3, fill="#ffffff", outline="")


class NeoChip(tk.Canvas):
    """Pill-shaped selector button (replaces ttk.Radiobutton), grouped
    via a shared tk.StringVar."""

    def __init__(self, parent, text, value, variable, command=None, width=84, height=26, **kwargs):
        super().__init__(parent, width=width, height=height, highlightthickness=0,
                          bg=parent["bg"], cursor="hand2", **kwargs)
        self.text = text
        self.value = value
        self.variable = variable
        self.command = command
        self.width = width
        self.height = height
        self.bind("<Button-1>", self._select)
        self.variable.trace_add("write", lambda *_: self._draw())
        self._draw()

    def _select(self, event=None):
        self.variable.set(self.value)
        if self.command:
            self.command()

    def _draw(self):
        self.delete("all")
        active = self.variable.get() == self.value
        bg = ACCENT if active else PANEL_ALT
        fg = "#ffffff" if active else TEXT_MUTED
        r = self.height / 2
        self.create_oval(0, 0, self.height, self.height, fill=bg, outline="")
        self.create_oval(self.width - self.height, 0, self.width, self.height, fill=bg, outline="")
        self.create_rectangle(r, 0, self.width - r, self.height, fill=bg, outline="")
        self.create_text(self.width / 2, self.height / 2, text=self.text,
                          fill=fg, font=("Segoe UI", 9, "bold"))


class MiniButton(tk.Button):
    """Small helper button for the dark theme."""

    def __init__(self, parent, text, command=None, width=3, fg=TEXT_PRIMARY, **kwargs):
        super().__init__(
            parent, text=text, command=command, width=width, fg=fg,
            bg=PANEL_ALT, activebackground=ACCENT, activeforeground="#ffffff",
            relief="flat", bd=0, highlightthickness=1, highlightbackground=PANEL_BORDER,
            font=("Segoe UI", 8, "bold"), cursor="hand2", **kwargs
        )


def style_listbox(box):
    box.configure(
        bg=FIELD_BG,
        fg=TEXT_PRIMARY,
        selectbackground=ACCENT,
        selectforeground="#ffffff",
        relief="flat",
        bd=0,
        highlightthickness=1,
        highlightbackground=PANEL_BORDER,
        highlightcolor=ACCENT,
        activestyle="none",
        font=("Segoe UI", 9),
    )


class Card(tk.Frame):
    """Compact settings card: a header (title + optional toggle on the
    right) and a body for the actual content. Keeps a group's options close
    together, separating groups with only a thin border."""

    def __init__(self, parent, title, toggle_var=None, on_toggle=None, **kwargs):
        super().__init__(parent, bg=PANEL_BG, highlightthickness=1,
                          highlightbackground=PANEL_BORDER, highlightcolor=PANEL_BORDER, **kwargs)
        header = tk.Frame(self, bg=PANEL_BG)
        header.pack(fill="x", padx=14, pady=(10, 4))
        accent_dot = tk.Canvas(header, width=6, height=6, highlightthickness=0, bg=PANEL_BG)
        accent_dot.pack(side="left", padx=(0, 8))
        accent_dot.create_oval(0, 0, 6, 6, fill=ACCENT_ALT, outline="")
        tk.Label(header, text=title.upper(), bg=PANEL_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        if toggle_var is not None:
            NeoToggle(header, toggle_var, command=on_toggle).pack(side="right")

        self.body = tk.Frame(self, bg=PANEL_BG)
        self._body_pack_opts = {"fill": "x", "padx": 14, "pady": (2, 12)}

        if toggle_var is not None:
            # The card shows/hides its body based on the header toggle's state.
            def _sync_visibility(*_):
                if toggle_var.get():
                    if not self.body.winfo_ismapped():
                        self.body.pack(**self._body_pack_opts)
                else:
                    if self.body.winfo_ismapped():
                        self.body.pack_forget()

            toggle_var.trace_add("write", _sync_visibility)
            _sync_visibility()
        else:
            self.body.pack(**self._body_pack_opts)


# =============================================================================
# 5) CHAMPION SELECTION WIDGETS (search + list, alphabetical, filtered)
# =============================================================================

class ScrollableArea(tk.Frame):
    """Scrollable container for the entire window content."""

    def __init__(self, parent, bg=BG):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0)
        self.scrollbar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)

        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(self._window, width=e.width))
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind_all("<Button-4>", lambda e: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind_all("<Button-5>", lambda e: self.canvas.yview_scroll(1, "units"))

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


class PriorityListEditor(tk.Frame):
    """Champion search + priority list. Enter/double-click adds,
    dragging between lists adds/reorders/removes."""

    DRAG_THRESHOLD = 6

    def __init__(self, parent, champion_data: ChampionData, on_change, initial=None, bg=PANEL_BG, allow_bravery=False):
        super().__init__(parent, bg=bg)
        self.champion_data = champion_data
        self.on_change = on_change
        self.items = list(initial or [])
        self.allow_bravery = allow_bravery

        self._drag_source = None
        self._drag_item = None
        self._drag_item_index = None
        self._drag_start_xy = (0, 0)

        self.badge_var = tk.StringVar()
        self.badge = tk.Label(self, textvariable=self.badge_var, bg=PANEL_ALT, fg=ACCENT_ALT,
                               font=("Segoe UI", 9, "bold"), anchor="w", padx=10, pady=5,
                               wraplength=WIN_W - 80, justify="left")
        self.badge.pack(fill="x", pady=(0, 6))

        search_row = tk.Frame(self, bg=bg)
        search_row.pack(fill="x", pady=(0, 4))
        self.search_var = tk.StringVar()
        entry = tk.Entry(search_row, textvariable=self.search_var, bg=FIELD_BG, fg=TEXT_PRIMARY,
                          insertbackground=TEXT_PRIMARY, relief="flat", bd=0,
                          highlightthickness=1, highlightbackground=PANEL_BORDER, highlightcolor=ACCENT,
                          font=("Segoe UI", 9))
        entry.pack(side="left", fill="x", expand=True, ipady=4)
        entry.bind("<KeyRelease>", self._refresh_filtered)
        entry.bind("<Return>", self._add_first_filtered)
        entry.bind("<Down>", self._focus_filtered)

        lists_row = tk.Frame(self, bg=bg)
        lists_row.pack(fill="x")

        left_col = tk.Frame(lists_row, bg=bg)
        left_col.pack(side="left", fill="both", expand=True, padx=(0, 4))
        tk.Label(left_col, text="ALL CHAMPIONS", bg=bg, fg=TEXT_MUTED, font=("Segoe UI", 7, "bold")).pack(anchor="w")
        self.filtered_box = tk.Listbox(left_col, height=4, exportselection=False)
        style_listbox(self.filtered_box)
        self.filtered_box.pack(fill="x")
        self.filtered_box.bind("<Double-Button-1>", self._add_selected_filtered)
        self.filtered_box.bind("<Return>", self._add_selected_filtered)
        self.filtered_box.bind("<ButtonPress-1>", lambda e: self._start_drag(e, "filtered"))
        self.filtered_box.bind("<ButtonRelease-1>", self._on_drag_release)

        right_col = tk.Frame(lists_row, bg=bg)
        right_col.pack(side="left", fill="both", expand=True, padx=(4, 0))
        tk.Label(right_col, text="PRIORITY", bg=bg, fg=TEXT_MUTED, font=("Segoe UI", 7, "bold")).pack(anchor="w")
        self.order_box = tk.Listbox(right_col, height=4, exportselection=False)
        style_listbox(self.order_box)
        self.order_box.pack(fill="x")
        self.order_box.bind("<ButtonPress-1>", lambda e: self._start_drag(e, "order"))
        self.order_box.bind("<ButtonRelease-1>", self._on_drag_release)

        btn_row = tk.Frame(self, bg=bg)
        btn_row.pack(fill="x", pady=(4, 0))
        MiniButton(btn_row, "^", command=self._move_up).pack(side="left")
        MiniButton(btn_row, "v", command=self._move_down).pack(side="left", padx=4)
        MiniButton(btn_row, "Remove", width=7, command=self._remove_selected).pack(side="left")
        if self.allow_bravery:
            MiniButton(btn_row, "+ Bravery", width=9, command=lambda: self.add_champion(BRAVERY_LABEL)).pack(side="left", padx=(6, 0))

        self._refresh_filtered()
        self._refresh_order()

    def refresh_champion_source(self):
        self._refresh_filtered()

    def add_champion(self, name, to_front=False):
        self._insert_champion(name, 0 if to_front else len(self.items))

    def set_list(self, names):
        self.items = list(names)
        self._refresh_order()

    def get_list(self):
        return list(self.items)

    def _refresh_filtered(self, event=None):
        query = _normalize(self.search_var.get())
        names = [n for n in self.champion_data.names_sorted if query in _normalize(n)]
        self.filtered_box.delete(0, tk.END)
        for n in names:
            self.filtered_box.insert(tk.END, n)

    def _focus_filtered(self, event=None):
        if self.filtered_box.size() > 0:
            self.filtered_box.selection_clear(0, tk.END)
            self.filtered_box.selection_set(0)
            self.filtered_box.activate(0)
            self.filtered_box.focus_set()
        return "break"

    def _add_first_filtered(self, event=None):
        sel = self.filtered_box.curselection()
        if sel:
            name = self.filtered_box.get(sel[0])
        elif self.filtered_box.size() > 0:
            name = self.filtered_box.get(0)
        else:
            return
        self.add_champion(name)
        self.search_var.set("")
        self._refresh_filtered()

    def _add_selected_filtered(self, event=None):
        sel = self.filtered_box.curselection()
        if sel:
            self.add_champion(self.filtered_box.get(sel[0]))

    def _refresh_order(self):
        self.order_box.delete(0, tk.END)
        for i, n in enumerate(self.items, 1):
            self.order_box.insert(tk.END, f"{i}. {n}")
        self.badge_var.set("> " + " -> ".join(self.items) if self.items else "> random (no priority set)")

    def _remove_selected(self):
        sel = self.order_box.curselection()
        if sel:
            self._remove_index(sel[0])

    def _move_up(self):
        sel = self.order_box.curselection()
        if sel and sel[0] > 0:
            self._reorder_champion(sel[0], sel[0] - 1)

    def _move_down(self):
        sel = self.order_box.curselection()
        if sel and sel[0] < len(self.items) - 1:
            self._reorder_champion(sel[0], sel[0] + 1)

    def _insert_champion(self, name, idx):
        if not name:
            return
        if _normalize(name) == _normalize(BRAVERY_LABEL):
            name = BRAVERY_LABEL
        if name in self.items:
            self.items.remove(name)
        idx = max(0, min(idx, len(self.items)))
        self.items.insert(idx, name)
        self._refresh_order()
        self.on_change(list(self.items))

    def _reorder_champion(self, old_idx, new_idx):
        if old_idx == new_idx or not (0 <= old_idx < len(self.items)):
            return
        name = self.items.pop(old_idx)
        new_idx = max(0, min(new_idx, len(self.items)))
        self.items.insert(new_idx, name)
        self._refresh_order()
        self.order_box.selection_set(new_idx)
        self.on_change(list(self.items))

    def _remove_index(self, idx):
        if 0 <= idx < len(self.items):
            del self.items[idx]
            self._refresh_order()
            self.on_change(list(self.items))

    def _start_drag(self, event, source):
        self._drag_source = source
        self._drag_start_xy = (event.x_root, event.y_root)
        if source == "filtered":
            idx = self.filtered_box.nearest(event.y)
            self._drag_item = self.filtered_box.get(idx) if 0 <= idx < self.filtered_box.size() else None
            self._drag_item_index = None
        else:
            idx = self.order_box.nearest(event.y)
            self._drag_item = self.items[idx] if 0 <= idx < len(self.items) else None
            self._drag_item_index = idx

    def _on_drag_release(self, event):
        source, item, item_index = self._drag_source, self._drag_item, self._drag_item_index
        self._drag_source = self._drag_item = self._drag_item_index = None
        if not item or not source:
            return

        dx = abs(event.x_root - self._drag_start_xy[0])
        dy = abs(event.y_root - self._drag_start_xy[1])
        moved_enough = dx > self.DRAG_THRESHOLD or dy > self.DRAG_THRESHOLD

        target = self.winfo_containing(event.x_root, event.y_root)
        landed_on_order = target is self.order_box

        if source == "filtered":
            if landed_on_order:
                local_y = event.y_root - self.order_box.winfo_rooty()
                idx = self.order_box.nearest(local_y) if self.order_box.size() > 0 else 0
                self._insert_champion(item, idx)
        elif source == "order":
            if landed_on_order and moved_enough:
                local_y = event.y_root - self.order_box.winfo_rooty()
                new_idx = self.order_box.nearest(local_y) if self.order_box.size() > 0 else 0
                self._reorder_champion(item_index, new_idx)
            elif not landed_on_order and moved_enough:
                self._remove_index(item_index)


class SingleChampionPicker(tk.Frame):
    """Search + pick a SINGLE champion (Hover)."""

    def __init__(self, parent, champion_data: ChampionData, on_change, initial="", bg=PANEL_BG):
        super().__init__(parent, bg=bg)
        self.champion_data = champion_data
        self.on_change = on_change
        self.selected = initial

        self.selected_var = tk.StringVar(value=(f"> {initial}" if initial else "> none"))
        self.badge = tk.Label(self, textvariable=self.selected_var, bg=PANEL_ALT, fg=ACCENT_ALT,
                               font=("Segoe UI", 9, "bold"), anchor="w", padx=10, pady=5,
                               wraplength=WIN_W - 80, justify="left")
        self.badge.pack(fill="x", pady=(0, 6))

        row = tk.Frame(self, bg=bg)
        row.pack(fill="x")
        self.search_var = tk.StringVar()
        entry = tk.Entry(row, textvariable=self.search_var, bg=FIELD_BG, fg=TEXT_PRIMARY,
                          insertbackground=TEXT_PRIMARY, relief="flat", bd=0,
                          highlightthickness=1, highlightbackground=PANEL_BORDER, highlightcolor=ACCENT,
                          font=("Segoe UI", 9))
        entry.pack(side="left", fill="x", expand=True, ipady=4)
        entry.bind("<KeyRelease>", self._refresh_filtered)
        entry.bind("<Return>", self._select_first)
        entry.bind("<Down>", self._focus_filtered)
        MiniButton(row, "Clear", width=8, command=self._clear).pack(side="left", padx=(6, 0))

        self.filtered_box = tk.Listbox(self, height=4, exportselection=False)
        style_listbox(self.filtered_box)
        self.filtered_box.pack(fill="x", pady=(4, 0))
        self.filtered_box.bind("<Double-Button-1>", self._select_clicked)
        self.filtered_box.bind("<Return>", self._select_clicked)

        self._refresh_filtered()

    def set_value(self, name):
        self.selected = name
        self.selected_var.set(f"> {name}" if name else "> none")

    def choose(self, name):
        self.set_value(name)
        self.on_change(name)

    def refresh_champion_source(self):
        self._refresh_filtered()

    def _refresh_filtered(self, event=None):
        query = _normalize(self.search_var.get())
        names = [n for n in self.champion_data.names_sorted if query in _normalize(n)]
        self.filtered_box.delete(0, tk.END)
        for n in names:
            self.filtered_box.insert(tk.END, n)

    def _focus_filtered(self, event=None):
        if self.filtered_box.size() > 0:
            self.filtered_box.selection_clear(0, tk.END)
            self.filtered_box.selection_set(0)
            self.filtered_box.activate(0)
            self.filtered_box.focus_set()
        return "break"

    def _select_first(self, event=None):
        sel = self.filtered_box.curselection()
        if sel:
            self.choose(self.filtered_box.get(sel[0]))
        elif self.filtered_box.size() > 0:
            self.choose(self.filtered_box.get(0))

    def _select_clicked(self, event=None):
        sel = self.filtered_box.curselection()
        if sel:
            self.choose(self.filtered_box.get(sel[0]))

    def _clear(self):
        self.choose("")


class FavoritesPanel(tk.Frame):
    """Favorite champions with quick Ban / Pick / Hover buttons."""

    def __init__(self, parent, champion_data: ChampionData, on_change, on_quick_action, initial=None, bg=PANEL_BG):
        super().__init__(parent, bg=bg)
        self.champion_data = champion_data
        self.on_change = on_change
        self.on_quick_action = on_quick_action
        self.favorites = list(initial or [])
        self.bg = bg

        add_row = tk.Frame(self, bg=bg)
        add_row.pack(fill="x")
        self.search_var = tk.StringVar()
        entry = tk.Entry(add_row, textvariable=self.search_var, bg=FIELD_BG, fg=TEXT_PRIMARY,
                          insertbackground=TEXT_PRIMARY, relief="flat", bd=0,
                          highlightthickness=1, highlightbackground=PANEL_BORDER, highlightcolor=ACCENT,
                          font=("Segoe UI", 9))
        entry.pack(side="left", fill="x", expand=True, ipady=4)
        entry.bind("<KeyRelease>", self._refresh_filtered)
        entry.bind("<Return>", self._add_first)
        MiniButton(add_row, "+ Add", width=6, command=self._add_first).pack(side="left", padx=(6, 0))

        self.filtered_box = tk.Listbox(self, height=3, exportselection=False)
        style_listbox(self.filtered_box)
        self.filtered_box.pack(fill="x", pady=(4, 6))
        self.filtered_box.bind("<Double-Button-1>", self._add_clicked)

        self.rows_frame = tk.Frame(self, bg=bg)
        self.rows_frame.pack(fill="x")

        self._refresh_filtered()
        self._render_rows()

    def refresh_champion_source(self):
        self._refresh_filtered()

    def set_list(self, names):
        self.favorites = list(names)
        self._render_rows()
        self._refresh_filtered()

    def _refresh_filtered(self, event=None):
        query = _normalize(self.search_var.get())
        names = [n for n in self.champion_data.names_sorted
                 if query in _normalize(n) and n not in self.favorites]
        self.filtered_box.delete(0, tk.END)
        for n in names:
            self.filtered_box.insert(tk.END, n)

    def _add_first(self, event=None):
        if self.filtered_box.size() > 0:
            self._add(self.filtered_box.get(0))

    def _add_clicked(self, event=None):
        sel = self.filtered_box.curselection()
        if sel:
            self._add(self.filtered_box.get(sel[0]))

    def _add(self, name):
        if name and name not in self.favorites:
            self.favorites.append(name)
            self.search_var.set("")
            self._refresh_filtered()
            self._render_rows()
            self.on_change(list(self.favorites))

    def _remove(self, name):
        if name in self.favorites:
            self.favorites.remove(name)
            self._refresh_filtered()
            self._render_rows()
            self.on_change(list(self.favorites))

    def _render_rows(self):
        for w in self.rows_frame.winfo_children():
            w.destroy()
        if not self.favorites:
            tk.Label(self.rows_frame, text="No favorites yet.",
                     bg=self.bg, fg=TEXT_MUTED, font=("Segoe UI", 8)).pack(anchor="w")
            return
        for name in self.favorites:
            row = tk.Frame(self.rows_frame, bg=PANEL_ALT)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=name, bg=PANEL_ALT, fg=TEXT_PRIMARY, font=("Segoe UI", 9),
                     width=14, anchor="w").pack(side="left", padx=(6, 0))
            MiniButton(row, "Ban", width=4, command=lambda n=name: self.on_quick_action("ban", n)).pack(side="left", padx=2)
            MiniButton(row, "Pick", width=4, command=lambda n=name: self.on_quick_action("pick", n)).pack(side="left", padx=2)
            MiniButton(row, "Hov", width=4, command=lambda n=name: self.on_quick_action("hover", n)).pack(side="left", padx=2)
            MiniButton(row, "x", width=2, fg=DANGER, command=lambda n=name: self._remove(n)).pack(side="left", padx=(6, 0))


# =============================================================================
# 6) MAIN GUI
# =============================================================================

class App:
    PAGE_META = {
        "dashboard": ("Dashboard", "Live status and the most important switches"),
        "champions": ("Champions", "Hover, ban, pick and favorite priorities"),
        "automation": ("Automation", "Matchmaking, lobby and background behavior"),
        "status": ("Custom Status", "Set the status shown in the League client"),
        "notifications": ("Notifications", "Private phone alerts delivered through ntfy"),
        "activity": ("Activity", "Runtime events and diagnostics"),
    }

    def __init__(self, root):
        self.root = root
        self.profile_store = load_profile_store()
        self.active_profile = self.profile_store["active_profile"]
        self.live_config = copy.deepcopy(self.profile_store["profiles"][self.active_profile])
        self._save_after_id = None
        self._poll_after_id = None
        self._tray_icon = None
        self._closing = False
        self._connection_state = None
        self._active_page = None
        self._status_request_running = False
        self._loading_profile = False

        root.title(f"{APP_TITLE} v{__version__}")
        root.geometry(f"{WIN_W}x{WIN_H}")
        root.minsize(920, 660)
        root.configure(bg=BG)
        try:
            root.iconbitmap(get_resource_path("league_arena_tools.ico"))
        except Exception:
            pass
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.client = LCUClient()
        self.champion_data = ChampionData()
        self.bot_running = False
        self.bot = LeagueArenaToolsBot(self.client, self.log, self.live_config, self.champion_data)
        self._init_variables()
        self._build_header()
        self._build_shell()
        self._show_page("dashboard")
        self.log(f"{APP_TITLE} v{__version__} started. Profile: {self.active_profile}")
        self._poll_connection()

    def _init_variables(self):
        cfg = self.live_config
        self.var_accept = tk.BooleanVar(value=cfg.get("auto_accept", False))
        self.var_accept_instant = tk.BooleanVar(value=cfg.get("accept_instant", False))
        self.var_accept_delay_ms = tk.IntVar(value=int(cfg.get("accept_delay_ms", 0)))
        self.var_auto_requeue = tk.BooleanVar(value=cfg.get("auto_requeue_enabled", False))
        self.var_hover = tk.BooleanVar(value=cfg.get("hover_enabled", False))
        self.var_ban = tk.BooleanVar(value=cfg.get("auto_ban", False))
        self.var_pick = tk.BooleanVar(value=cfg.get("auto_pick", False))
        self.var_pick_mode = tk.StringVar(value=cfg.get("pick_mode", "normal"))
        self.var_fav_expanded = tk.BooleanVar(value=cfg.get("favorites_expanded", True))
        self.var_push_enabled = tk.BooleanVar(value=cfg.get("push_enabled", False))
        self.var_push_topic = tk.StringVar(value=cfg.get("push_topic") or generate_push_topic())
        self.var_lobby_message_enabled = tk.BooleanVar(value=cfg.get("lobby_message_enabled", False))
        self.var_lobby_message_text = tk.StringVar(value=cfg.get("lobby_message_text") or "")
        self.custom_status_info_var = tk.StringVar(value="Not loaded")
        self.custom_status_count_var = tk.StringVar(value="0 characters")
        self.profile_var = tk.StringVar(value=self.active_profile)
        self.active_profile_var = tk.StringVar(value=f"Active: {self.active_profile}")
        self.profile_notice_var = tk.StringVar(value="")
        self.var_minimize_to_tray = tk.BooleanVar(value=cfg.get("minimize_to_tray", True))
        self.var_delay_ms = tk.IntVar(value=int(cfg.get("delay_ms", 500)))

        bindings = (
            ("auto_accept", self.var_accept, self._bind_bool),
            ("accept_instant", self.var_accept_instant, self._bind_bool),
            ("accept_delay_ms", self.var_accept_delay_ms, self._bind_int),
            ("auto_requeue_enabled", self.var_auto_requeue, self._bind_bool),
            ("hover_enabled", self.var_hover, self._bind_bool),
            ("auto_ban", self.var_ban, self._bind_bool),
            ("auto_pick", self.var_pick, self._bind_bool),
            ("pick_mode", self.var_pick_mode, self._bind_str),
            ("favorites_expanded", self.var_fav_expanded, self._bind_bool),
            ("push_enabled", self.var_push_enabled, self._bind_bool),
            ("push_topic", self.var_push_topic, self._bind_str),
            ("lobby_message_enabled", self.var_lobby_message_enabled, self._bind_bool),
            ("lobby_message_text", self.var_lobby_message_text, self._bind_str),
            ("minimize_to_tray", self.var_minimize_to_tray, self._bind_bool),
            ("delay_ms", self.var_delay_ms, self._bind_int),
        )
        for key, variable, binder in bindings:
            binder(key, variable)

        events = cfg.get("push_events", {})
        self.push_event_vars = {
            key: tk.BooleanVar(value=events.get(key, False))
            for key in ("accept", "ban", "dodge", "game_loaded")
        }
        self.status_var = tk.StringVar(value="Looking for League client...")
        self.bot_state_var = tk.StringVar(value="Stopped")
        self.champion_count_var = tk.StringVar(value="Not loaded")

    def _build_header(self):
        header = tk.Frame(self.root, bg=SIDEBAR_BG, height=76, highlightthickness=1,
                          highlightbackground=PANEL_BORDER)
        header.pack(fill="x")
        header.pack_propagate(False)
        brand = tk.Frame(header, bg=SIDEBAR_BG)
        brand.pack(side="left", padx=22, pady=13)
        logo = tk.Canvas(brand, width=44, height=44, bg=SIDEBAR_BG, highlightthickness=0)
        logo.pack(side="left")
        logo.create_oval(2, 2, 42, 42, fill=ACCENT, outline=ACCENT_ALT, width=2)
        logo.create_text(22, 22, text="L", fill="white", font=("Segoe UI", 18, "bold"))
        titles = tk.Frame(brand, bg=SIDEBAR_BG)
        titles.pack(side="left", padx=(12, 0))
        tk.Label(titles, text=APP_TITLE, bg=SIDEBAR_BG, fg=TEXT_PRIMARY,
                 font=("Segoe UI", 17, "bold")).pack(anchor="w")
        tk.Label(titles, text=f"v{__version__}", bg=SIDEBAR_BG,
                 fg=TEXT_MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.toggle_btn = AeroButton(header, "Start Bot", command=self._toggle_bot, width=176, height=36)
        self.toggle_btn.pack(side="right", padx=(10, 22), pady=12)
        pill = tk.Frame(header, bg=PANEL_ALT, highlightthickness=1, highlightbackground=PANEL_BORDER)
        pill.pack(side="right", padx=8, pady=20)
        self.status_dot_canvas = tk.Canvas(pill, width=18, height=18, bg=PANEL_ALT, highlightthickness=0)
        self.status_dot_canvas.pack(side="left", padx=(9, 2), pady=5)
        self.status_dot = self.status_dot_canvas.create_oval(5, 5, 13, 13, fill=DANGER, outline="")
        self.header_status_var = tk.StringVar(value="League offline")
        tk.Label(pill, textvariable=self.header_status_var, bg=PANEL_ALT, fg=TEXT_MUTED,
                 font=("Segoe UI", 9, "bold")).pack(side="left", padx=(2, 10), pady=5)

    def _build_shell(self):
        shell = tk.Frame(self.root, bg=BG)
        shell.pack(fill="both", expand=True)
        sidebar = tk.Frame(shell, bg=SIDEBAR_BG, width=190)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(sidebar, text="WORKSPACE", bg=SIDEBAR_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=20, pady=(24, 10))
        self.nav_buttons = {}
        for key, label in (
            ("dashboard", "  Dashboard"), ("champions", "  Champions"),
            ("automation", "  Automation"), ("status", "  Custom Status"),
            ("notifications", "  Notifications"),
            ("activity", "  Activity"),
        ):
            button = tk.Button(
                sidebar, text=label, command=lambda page=key: self._show_page(page),
                anchor="w", bg=SIDEBAR_BG, fg=TEXT_MUTED, activebackground=PANEL_ALT,
                activeforeground=TEXT_PRIMARY, relief="flat", bd=0, padx=16, pady=11,
                font=("Segoe UI", 10, "bold"), cursor="hand2",
            )
            button.pack(fill="x", padx=10, pady=2)
            self.nav_buttons[key] = button
        footer = tk.Frame(sidebar, bg=SIDEBAR_BG)
        footer.pack(side="bottom", fill="x", padx=14, pady=14)
        tk.Label(footer, text="CONFIG PROFILES", bg=SIDEBAR_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w")
        tk.Label(footer, textvariable=self.active_profile_var, bg=SIDEBAR_BG, fg=ACCENT_ALT,
                 font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x", pady=(4, 3))
        self.profile_menu = tk.OptionMenu(footer, self.profile_var, self.active_profile)
        self.profile_menu.configure(
            bg=FIELD_BG, fg=TEXT_PRIMARY, activebackground=PANEL_ALT,
            activeforeground=TEXT_PRIMARY, relief="flat", bd=0,
            highlightthickness=1, highlightbackground=PANEL_BORDER,
            anchor="w", font=("Segoe UI", 8), width=18,
        )
        self.profile_menu_dropdown = self.profile_menu.nametowidget(self.profile_menu["menu"])
        self.profile_menu_dropdown.configure(
            bg=FIELD_BG, fg=TEXT_PRIMARY, activebackground=ACCENT,
            activeforeground="#ffffff", font=("Segoe UI", 8),
        )
        self.profile_menu.pack(fill="x")
        profile_buttons = tk.Frame(footer, bg=SIDEBAR_BG)
        profile_buttons.pack(fill="x", pady=(5, 0))
        self.profile_new_btn = MiniButton(profile_buttons, "New", width=5,
                                          command=self._create_profile)
        self.profile_new_btn.pack(side="left")
        self.profile_use_btn = MiniButton(profile_buttons, "Use", width=5,
                                          command=self._apply_selected_profile)
        self.profile_use_btn.pack(side="left", padx=4)
        self.profile_delete_btn = MiniButton(profile_buttons, "Delete", width=6, fg=DANGER,
                                             command=self._delete_selected_profile)
        self.profile_delete_btn.pack(side="left")
        tk.Label(footer, textvariable=self.profile_notice_var, bg=SIDEBAR_BG,
                 fg=TEXT_MUTED, font=("Segoe UI", 7), anchor="w",
                 justify="left", wraplength=155).pack(fill="x", pady=(5, 0))
        self._refresh_profile_menu()

        content = tk.Frame(shell, bg=BG)
        content.pack(side="left", fill="both", expand=True)
        self.page_title_var = tk.StringVar()
        self.page_subtitle_var = tk.StringVar()
        heading = tk.Frame(content, bg=BG)
        heading.pack(fill="x", padx=26, pady=(20, 12))
        tk.Label(heading, textvariable=self.page_title_var, bg=BG, fg=TEXT_PRIMARY,
                 font=("Segoe UI", 20, "bold")).pack(anchor="w")
        tk.Label(heading, textvariable=self.page_subtitle_var, bg=BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 0))
        self.page_host = tk.Frame(content, bg=BG)
        self.page_host.pack(fill="both", expand=True, padx=24, pady=(0, 22))
        self.page_frames = {name: tk.Frame(self.page_host, bg=BG) for name in self.PAGE_META}
        for page in self.page_frames.values():
            page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._build_dashboard(self.page_frames["dashboard"])
        self._build_champions(self.page_frames["champions"])
        self._build_automation(self.page_frames["automation"])
        self._build_status(self.page_frames["status"])
        self._build_notifications(self.page_frames["notifications"])
        self._build_activity(self.page_frames["activity"])

    def _build_dashboard(self, page):
        page.grid_columnconfigure(0, weight=1, uniform="dashboard")
        page.grid_columnconfigure(1, weight=1, uniform="dashboard")
        page.grid_rowconfigure(1, weight=1)
        metrics = tk.Frame(page, bg=BG)
        metrics.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        for column in range(3):
            metrics.grid_columnconfigure(column, weight=1, uniform="metrics")
        self._metric(metrics, 0, "LEAGUE CLIENT", self.status_var, ACCENT_ALT)
        self._metric(metrics, 1, "BOT ENGINE", self.bot_state_var, ACCENT)
        self._metric(metrics, 2, "CHAMPION DATA", self.champion_count_var, SUCCESS)
        quick = Card(page, "Quick Controls")
        quick.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        self._toggle_row(quick.body, "Auto Accept", "Accept ready checks automatically", self.var_accept)
        self._toggle_row(quick.body, "Auto Ban", "Use your ordered ban priority", self.var_ban)
        self._toggle_row(quick.body, "Auto Pick", "Lock your selected champion priority", self.var_pick)
        self._toggle_row(quick.body, "Hover", "Show your intent before the pick", self.var_hover)
        self._toggle_row(quick.body, "Auto Re-queue", "Automatically start matchmaking after joining a lobby", self.var_auto_requeue)
        overview = Card(page, "Runtime Overview")
        overview.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        self._info_row(overview.body, "Pick mode", self.var_pick_mode)
        self._info_row(overview.body, "Confirm delay", self.var_delay_ms, " ms")
        self._info_row(overview.body, "Accept delay", self.var_accept_delay_ms, " ms")

    def _build_champions(self, page):
        page.grid_columnconfigure(0, weight=1, uniform="champions")
        page.grid_columnconfigure(1, weight=1, uniform="champions")
        page.grid_rowconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        ban_card = Card(page, "Auto Ban", toggle_var=self.var_ban)
        ban_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 6))
        self.ban_editor = PriorityListEditor(
            ban_card.body, self.champion_data, self._on_ban_change,
            initial=self.live_config.get("ban_priority", []),
        )
        self.ban_editor.pack(fill="both", expand=True)
        pick_card = Card(page, "Auto Pick", toggle_var=self.var_pick)
        pick_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 6))
        mode = tk.Frame(pick_card.body, bg=PANEL_BG)
        mode.pack(fill="x", pady=(0, 7))
        tk.Label(mode, text="Confirmation", bg=PANEL_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=(0, 8))
        NeoChip(mode, "Normal", "normal", self.var_pick_mode, width=74).pack(side="left", padx=(0, 6))
        NeoChip(mode, "Instant", "instant", self.var_pick_mode, width=74).pack(side="left")
        self.pick_editor = PriorityListEditor(
            pick_card.body, self.champion_data, self._on_pick_change,
            initial=self.live_config.get("pick_priority", []), allow_bravery=True,
        )
        self.pick_editor.pack(fill="both", expand=True)
        hover_card = Card(page, "Hover", toggle_var=self.var_hover)
        hover_card.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=(6, 0))
        self.hover_picker = SingleChampionPicker(
            hover_card.body, self.champion_data, self._on_hover_change,
            initial=self.live_config.get("hover_champion", ""),
        )
        self.hover_picker.pack(fill="x")
        favorites_card = Card(page, "Favorites", toggle_var=self.var_fav_expanded)
        favorites_card.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=(6, 0))
        self.favorites_panel = FavoritesPanel(
            favorites_card.body, self.champion_data, self._on_favorites_change,
            self._on_quick_action, initial=self.live_config.get("favorites", []),
        )
        self.favorites_panel.pack(fill="both", expand=True)

    def _build_automation(self, page):
        page.grid_columnconfigure(0, weight=1, uniform="automation")
        page.grid_columnconfigure(1, weight=1, uniform="automation")
        accept_card = Card(page, "Match Accept", toggle_var=self.var_accept)
        accept_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 6))
        self._toggle_row(accept_card.body, "Instant accept", "Skip the configured delay", self.var_accept_instant)
        accept_delay = self._number_row(
            accept_card.body, "Delay", self.var_accept_delay_ms, 0, 10000, 100, "ms"
        )
        def sync_accept_delay(*_):
            if self.var_accept_instant.get():
                accept_delay.pack_forget()
            elif not accept_delay.winfo_ismapped():
                accept_delay.pack(fill="x", pady=(8, 0))
        self.var_accept_instant.trace_add("write", sync_accept_delay)
        sync_accept_delay()
        requeue_card = Card(page, "Auto Re-queue", toggle_var=self.var_auto_requeue)
        requeue_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 6))
        tk.Label(requeue_card.body,
                 text="Automatically starts matchmaking after joining a lobby.",
                 bg=PANEL_BG, fg=TEXT_MUTED, justify="left", anchor="w", wraplength=360,
                 font=("Segoe UI", 9)).pack(fill="x")
        message_card = Card(page, "Champ Select Message", toggle_var=self.var_lobby_message_enabled)
        message_card.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=6)
        tk.Label(message_card.body, text="Sent once when champ select chat becomes available.",
                 bg=PANEL_BG, fg=TEXT_MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 8))
        self._entry(message_card.body, self.var_lobby_message_text).pack(fill="x", ipady=5)
        timing_card = Card(page, "Pick and Ban Timing")
        timing_card.grid(row=1, column=1, sticky="nsew", padx=(6, 0), pady=6)
        self._number_row(timing_card.body, "Confirm delay", self.var_delay_ms,
                         MIN_DELAY_MS, 15000, 50, "ms").pack(fill="x")
        tk.Label(timing_card.body,
                 text="Normal mode waits this long while preserving a one-second safety margin before phase end.",
                 bg=PANEL_BG, fg=TEXT_MUTED, justify="left", anchor="w", wraplength=360,
                 font=("Segoe UI", 9)).pack(fill="x", pady=(10, 0))
        background_card = Card(page, "Background Mode", toggle_var=self.var_minimize_to_tray)
        background_card.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        note = (
            f"Closing the window sends {APP_TITLE} to the notification tray. Use Show or Quit from its menu."
            if TRAY_AVAILABLE else
            "Tray support is unavailable because pystray or Pillow could not be loaded."
        )
        tk.Label(background_card.body, text=note, bg=PANEL_BG, fg=TEXT_MUTED,
                 justify="left", anchor="w", font=("Segoe UI", 9)).pack(fill="x")

    def _build_notifications(self, page):
        card = Card(page, "Phone Notifications", toggle_var=self.var_push_enabled)
        card.pack(fill="both", expand=True)
        tk.Label(card.body,
                 text="Install ntfy on your phone and subscribe to the private topic below. No account is required.",
                 bg=PANEL_BG, fg=TEXT_MUTED, justify="left", anchor="w",
                 font=("Segoe UI", 9)).pack(fill="x", pady=(0, 12))
        tk.Label(card.body, text="Private topic", bg=PANEL_BG, fg=TEXT_PRIMARY,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        controls = tk.Frame(card.body, bg=PANEL_BG)
        controls.pack(fill="x", pady=(6, 16))
        self._entry(controls, self.var_push_topic).pack(side="left", fill="x", expand=True, ipady=5)
        MiniButton(controls, "New", command=self._regenerate_push_topic, width=6).pack(side="left", padx=(8, 4))
        MiniButton(controls, "Copy", command=self._copy_push_topic, width=6).pack(side="left", padx=4)
        MiniButton(controls, "Test", command=self._test_push_notification, width=6).pack(side="left", padx=(4, 0))
        for key, label, description in (
            ("accept", "Match accepted", "Ready check was accepted automatically"),
            ("ban", "Champion banned", "Configured ban action completed"),
            ("dodge", "Lobby dodge", "Champ select ended before the game"),
            ("game_loaded", "Game loaded", "Live Client API confirms the game started"),
        ):
            self._toggle_row(card.body, label, description, self.push_event_vars[key],
                             command=lambda event_key=key: self._on_push_event_change(event_key))

    def _build_status(self, page):
        card = Card(page, "League Custom Status")
        card.pack(fill="both", expand=True)

        state_row = tk.Frame(card.body, bg=PANEL_BG)
        state_row.pack(fill="x", pady=(0, 12))
        tk.Label(state_row, text="LEAGUE VALUE", bg=PANEL_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        tk.Label(state_row, textvariable=self.custom_status_info_var, bg=PANEL_BG,
                 fg=ACCENT_ALT, font=("Segoe UI", 9, "bold")).pack(side="right")

        editor_border = tk.Frame(card.body, bg=PANEL_BORDER)
        editor_border.pack(fill="both", expand=True)
        self.custom_status_editor = scrolledtext.ScrolledText(
            editor_border, wrap="word", undo=True, font=("Segoe UI", 10),
            bg=FIELD_BG, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
            selectbackground=ACCENT_DARK, relief="flat", bd=0, padx=12, pady=10,
        )
        self.custom_status_editor.pack(fill="both", expand=True, padx=1, pady=1)
        self.custom_status_editor.insert("1.0", self.live_config.get("custom_status_text", ""))
        self.custom_status_editor.bind("<KeyRelease>", self._on_custom_status_edit)
        self.custom_status_editor.bind("<<Paste>>", self._defer_custom_status_edit)
        self.custom_status_editor.bind("<<Cut>>", self._defer_custom_status_edit)

        controls = tk.Frame(card.body, bg=PANEL_BG)
        controls.pack(fill="x", pady=(12, 0))
        tk.Label(controls, textvariable=self.custom_status_count_var, bg=PANEL_BG,
                 fg=TEXT_MUTED, font=("Segoe UI", 8)).pack(side="left")
        MiniButton(controls, "Load current", width=12,
                   command=self._load_custom_status).pack(side="right", padx=(6, 0))
        MiniButton(controls, "Clear", width=7,
                   command=self._clear_custom_status).pack(side="right", padx=(6, 0))
        MiniButton(controls, "Set status", width=10,
                   command=self._apply_custom_status).pack(side="right")
        self._sync_custom_status_draft()

    def _build_activity(self, page):
        toolbar = tk.Frame(page, bg=BG)
        toolbar.pack(fill="x", pady=(0, 8))
        tk.Label(toolbar, text="RUNTIME LOG", bg=BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        MiniButton(toolbar, "Clear", width=7, command=self._clear_log).pack(side="right")
        wrapper = tk.Frame(page, bg=PANEL_BORDER)
        wrapper.pack(fill="both", expand=True)
        self.log_box = scrolledtext.ScrolledText(
            wrapper, state="disabled", font=("Cascadia Mono", 9), bg=FIELD_BG,
            fg="#cbd5e1", insertbackground=TEXT_PRIMARY, relief="flat", bd=0,
            padx=12, pady=10,
        )
        self.log_box.pack(fill="both", expand=True, padx=1, pady=1)
        self.log_box.tag_configure("ts", foreground=ACCENT_ALT)

    def _metric(self, parent, column, label, value_var, accent):
        card = tk.Frame(parent, bg=PANEL_BG, highlightthickness=1, highlightbackground=PANEL_BORDER)
        card.grid(row=0, column=column, sticky="ew",
                  padx=(0 if column == 0 else 6, 0 if column == 2 else 6))
        tk.Frame(card, bg=accent, width=4).pack(side="left", fill="y")
        body = tk.Frame(card, bg=PANEL_BG)
        body.pack(side="left", fill="both", expand=True, padx=14, pady=11)
        tk.Label(body, text=label, bg=PANEL_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w")
        tk.Label(body, textvariable=value_var, bg=PANEL_BG, fg=TEXT_PRIMARY,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(2, 0))

    def _toggle_row(self, parent, title, description, variable, command=None):
        row = tk.Frame(parent, bg=PANEL_BG)
        row.pack(fill="x", pady=5)
        text = tk.Frame(row, bg=PANEL_BG)
        text.pack(side="left", fill="x", expand=True)
        tk.Label(text, text=title, bg=PANEL_BG, fg=TEXT_PRIMARY,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Label(text, text=description, bg=PANEL_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 8)).pack(anchor="w")
        NeoToggle(row, variable, command=command).pack(side="right", padx=(12, 0))
        return row

    def _info_row(self, parent, label, variable, suffix=""):
        row = tk.Frame(parent, bg=PANEL_BG)
        row.pack(fill="x", pady=7)
        tk.Label(row, text=label, bg=PANEL_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        value = tk.Label(row, bg=PANEL_ALT, fg=TEXT_PRIMARY, padx=10, pady=4,
                         font=("Segoe UI", 9, "bold"))
        value.pack(side="right")
        def refresh(*_):
            try:
                current = variable.get()
            except tk.TclError:
                current = "-"
            value.configure(text=f"{current}{suffix}")
        variable.trace_add("write", refresh)
        refresh()

    def _number_row(self, parent, label, variable, minimum, maximum, increment, suffix):
        row = tk.Frame(parent, bg=PANEL_BG)
        tk.Label(row, text=label, bg=PANEL_BG, fg=TEXT_PRIMARY,
                 font=("Segoe UI", 9, "bold")).pack(side="left")
        tk.Label(row, text=suffix, bg=PANEL_BG, fg=TEXT_MUTED,
                 font=("Segoe UI", 8)).pack(side="right")
        tk.Spinbox(
            row, from_=minimum, to=maximum, increment=increment, textvariable=variable,
            width=8, bg=FIELD_BG, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
            buttonbackground=PANEL_ALT, relief="flat", bd=0, justify="center",
            highlightthickness=1, highlightbackground=PANEL_BORDER, highlightcolor=ACCENT,
            font=("Segoe UI", 9),
        ).pack(side="right", padx=(8, 5), ipady=3)
        return row

    def _entry(self, parent, variable):
        return tk.Entry(
            parent, textvariable=variable, bg=FIELD_BG, fg=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY, relief="flat", bd=0,
            highlightthickness=1, highlightbackground=PANEL_BORDER,
            highlightcolor=ACCENT, font=("Segoe UI", 9),
        )

    def _refresh_profile_menu(self):
        if not hasattr(self, "profile_menu"):
            return
        names = sorted(self.profile_store["profiles"], key=str.casefold)
        menu = self.profile_menu_dropdown
        menu.delete(0, "end")
        for name in names:
            menu.add_command(label=name, command=tk._setit(self.profile_var, name))

    def _profile_action_allowed(self):
        if not self.bot_running:
            return True
        message = "Stop the bot before changing profiles."
        self.profile_notice_var.set(message)
        self.log(message)
        return False

    def _set_profile_controls_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        self.profile_menu.configure(state=state)
        for button in (self.profile_new_btn, self.profile_use_btn, self.profile_delete_btn):
            button.configure(state=state, cursor="hand2" if enabled else "arrow")

    def _create_profile(self):
        if not self._profile_action_allowed():
            return
        name = simpledialog.askstring(
            "New config profile",
            "Profile name (copies the active settings):",
            parent=self.root,
        )
        if name is not None:
            self._create_profile_named(name)

    def _create_profile_named(self, name):
        if not self._profile_action_allowed():
            return False
        name = str(name).strip()
        if not name or len(name) > 32 or any(ord(char) < 32 for char in name):
            self.profile_notice_var.set("Use a name from 1 to 32 characters.")
            return False
        existing = next(
            (profile for profile in self.profile_store["profiles"] if profile.casefold() == name.casefold()),
            None,
        )
        if existing is not None:
            self.profile_var.set(existing)
            self.profile_notice_var.set("That profile already exists.")
            return False

        self._sync_custom_status_draft()
        self._save_profiles_now()
        self.profile_store["profiles"][name] = copy.deepcopy(self.live_config)
        self.profile_var.set(name)
        self._refresh_profile_menu()
        self._activate_profile(name, save_current=False)
        self.log(f"Created and activated config profile: {name}")
        return True

    def _apply_selected_profile(self):
        if not self._profile_action_allowed():
            return False
        name = self.profile_var.get()
        if name not in self.profile_store["profiles"]:
            self.profile_notice_var.set("Selected profile no longer exists.")
            self._refresh_profile_menu()
            return False
        if name == self.active_profile:
            self.profile_notice_var.set("This profile is already active.")
            return True
        self._activate_profile(name)
        self.log(f"Activated config profile: {name}")
        return True

    def _activate_profile(self, name, save_current=True):
        if save_current:
            self._sync_custom_status_draft()
            self._save_profiles_now()
        target_config = copy.deepcopy(self.profile_store["profiles"][name])
        self.active_profile = name
        self.profile_store["active_profile"] = name
        self.live_config.clear()
        self.live_config.update(target_config)

        self._loading_profile = True
        try:
            self._apply_config_to_ui()
        finally:
            self._loading_profile = False

        self.profile_var.set(name)
        self.active_profile_var.set(f"Active: {name}")
        self.profile_notice_var.set("Profile applied.")
        self._save_profiles_now()

    def _apply_config_to_ui(self):
        cfg = self.live_config
        values = (
            (self.var_accept, bool(cfg.get("auto_accept", False))),
            (self.var_accept_instant, bool(cfg.get("accept_instant", False))),
            (self.var_accept_delay_ms, int(cfg.get("accept_delay_ms", 0))),
            (self.var_auto_requeue, bool(cfg.get("auto_requeue_enabled", False))),
            (self.var_hover, bool(cfg.get("hover_enabled", False))),
            (self.var_ban, bool(cfg.get("auto_ban", False))),
            (self.var_pick, bool(cfg.get("auto_pick", False))),
            (self.var_pick_mode, cfg.get("pick_mode", "normal")),
            (self.var_fav_expanded, bool(cfg.get("favorites_expanded", True))),
            (self.var_push_enabled, bool(cfg.get("push_enabled", False))),
            (self.var_push_topic, cfg.get("push_topic") or generate_push_topic()),
            (self.var_lobby_message_enabled, bool(cfg.get("lobby_message_enabled", False))),
            (self.var_lobby_message_text, cfg.get("lobby_message_text") or ""),
            (self.var_minimize_to_tray, bool(cfg.get("minimize_to_tray", True))),
            (self.var_delay_ms, int(cfg.get("delay_ms", 500))),
        )
        for variable, value in values:
            variable.set(value)
        for key, variable in self.push_event_vars.items():
            variable.set(bool(cfg.get("push_events", {}).get(key, False)))

        self.ban_editor.set_list(cfg.get("ban_priority", []))
        self.pick_editor.set_list(cfg.get("pick_priority", []))
        self.hover_picker.set_value(cfg.get("hover_champion", ""))
        self.favorites_panel.set_list(cfg.get("favorites", []))
        self._set_custom_status_editor(cfg.get("custom_status_text", ""))
        self.custom_status_info_var.set("Not loaded")

    def _delete_selected_profile(self):
        if not self._profile_action_allowed():
            return False
        profiles = self.profile_store["profiles"]
        name = self.profile_var.get()
        if name not in profiles:
            self.profile_notice_var.set("Selected profile no longer exists.")
            return False
        if len(profiles) == 1:
            self.profile_notice_var.set("At least one profile is required.")
            return False
        if not messagebox.askyesno(
            "Delete config profile",
            f'Delete profile "{name}"?',
            parent=self.root,
        ):
            return False

        self._sync_custom_status_draft()
        self._save_profiles_now()
        del profiles[name]
        if name == self.active_profile:
            replacement = sorted(profiles, key=str.casefold)[0]
            self._activate_profile(replacement, save_current=False)
        else:
            self.profile_var.set(self.active_profile)
            self._save_profiles_now()
        self._refresh_profile_menu()
        self.profile_notice_var.set("Profile deleted.")
        self.log(f"Deleted config profile: {name}")
        return True

    def _show_page(self, name):
        if name not in self.page_frames:
            return
        self._active_page = name
        title, subtitle = self.PAGE_META[name]
        self.page_title_var.set(title)
        self.page_subtitle_var.set(subtitle)
        self.page_frames[name].tkraise()
        for key, button in self.nav_buttons.items():
            active = key == name
            button.configure(bg=PANEL_ALT if active else SIDEBAR_BG,
                             fg=TEXT_PRIMARY if active else TEXT_MUTED)

    def _set_connection_status(self, connected):
        self.status_dot_canvas.itemconfig(self.status_dot, fill=SUCCESS if connected else DANGER)
        self.header_status_var.set("League connected" if connected else "League offline")
        self.status_var.set("Connected" if connected else "Not running")

    def _bind_bool(self, key, variable):
        def callback(*_):
            self.live_config[key] = bool(variable.get())
            self._schedule_save()
        variable.trace_add("write", callback)
        self.live_config[key] = bool(variable.get())

    def _bind_int(self, key, variable):
        def callback(*_):
            try:
                self.live_config[key] = int(variable.get())
            except (tk.TclError, ValueError):
                return
            self._schedule_save()
        variable.trace_add("write", callback)
        try:
            self.live_config[key] = int(variable.get())
        except (tk.TclError, ValueError):
            pass

    def _bind_str(self, key, variable):
        def callback(*_):
            self.live_config[key] = variable.get()
            self._schedule_save()
        variable.trace_add("write", callback)
        self.live_config[key] = variable.get()

    def _on_ban_change(self, names):
        self.live_config["ban_priority"] = names
        self._schedule_save()

    def _on_pick_change(self, names):
        self.live_config["pick_priority"] = names
        self._schedule_save()

    def _on_hover_change(self, name):
        self.live_config["hover_champion"] = name
        self._schedule_save()

    def _on_favorites_change(self, names):
        self.live_config["favorites"] = names
        self._schedule_save()

    def _on_quick_action(self, action, name):
        if action == "ban":
            self.ban_editor.add_champion(name, to_front=True)
        elif action == "pick":
            self.pick_editor.add_champion(name, to_front=True)
        elif action == "hover":
            self.hover_picker.choose(name)
        self.log(f"Favorites -> {action.title()}: {name}")

    def _on_push_event_change(self, key):
        events = dict(self.live_config.get("push_events", {}))
        events[key] = bool(self.push_event_vars[key].get())
        self.live_config["push_events"] = events
        self._schedule_save()

    def _regenerate_push_topic(self):
        topic = generate_push_topic()
        self.var_push_topic.set(topic)
        self.log(f"New notification topic: {topic}")

    def _copy_push_topic(self):
        topic = self.var_push_topic.get().strip()
        if not topic:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(topic)
        self.log("Notification topic copied to clipboard.")

    def _defer_custom_status_edit(self, _event=None):
        self.root.after_idle(self._sync_custom_status_draft)

    def _on_custom_status_edit(self, _event=None):
        self._sync_custom_status_draft()

    def _sync_custom_status_draft(self):
        if not hasattr(self, "custom_status_editor"):
            return ""
        text = self.custom_status_editor.get("1.0", "end-1c")
        self.live_config["custom_status_text"] = text
        count = len(text)
        self.custom_status_count_var.set(f"{count} character{'s' if count != 1 else ''}")
        self._schedule_save()
        return text

    def _set_custom_status_editor(self, text):
        self.custom_status_editor.delete("1.0", "end")
        self.custom_status_editor.insert("1.0", text)
        self._sync_custom_status_draft()

    def _load_custom_status(self):
        self._start_custom_status_request(None)

    def _apply_custom_status(self):
        self._start_custom_status_request(self._sync_custom_status_draft())

    def _clear_custom_status(self):
        self._set_custom_status_editor("")
        self._start_custom_status_request("")

    def _start_custom_status_request(self, new_status):
        if self._status_request_running:
            self.custom_status_info_var.set("Request already running")
            return
        if not self.client.is_connected or self._connection_state is not True:
            self.custom_status_info_var.set("League client is offline")
            self.log("Custom status: League client is not connected.")
            return
        self._status_request_running = True
        self.custom_status_info_var.set("Loading..." if new_status is None else "Saving...")
        threading.Thread(
            target=self._custom_status_worker,
            args=(new_status,),
            name="LeagueArenaTools-CustomStatus",
            daemon=True,
        ).start()

    def _custom_status_worker(self, new_status):
        try:
            if new_status is not None:
                response = self.client.put("/lol-chat/v1/me", json={"statusMessage": new_status})
                if response.status_code not in (200, 201, 204):
                    self._finish_custom_status_request(
                        f"Save failed (HTTP {response.status_code})",
                        log_message=f"Custom status save failed (HTTP {response.status_code}).",
                    )
                    return

            response = self.client.get("/lol-chat/v1/me")
            if response.status_code != 200:
                self._finish_custom_status_request(
                    f"Load failed (HTTP {response.status_code})",
                    log_message=f"Custom status load failed (HTTP {response.status_code}).",
                )
                return
            data = response.json()
            stored_status = data.get("statusMessage", "") if isinstance(data, dict) else ""
            if not isinstance(stored_status, str):
                stored_status = str(stored_status or "")

            if new_status is None:
                message = f"Loaded {len(stored_status)} characters"
                log_message = f"Loaded League custom status ({len(stored_status)} characters)."
            elif stored_status == new_status:
                message = f"Saved {len(stored_status)} characters"
                log_message = f"League custom status saved ({len(stored_status)} characters)."
            else:
                message = f"League stored {len(stored_status)} of {len(new_status)} characters"
                log_message = (
                    "League altered or truncated the custom status: "
                    f"stored {len(stored_status)} of {len(new_status)} characters."
                )
            self._finish_custom_status_request(message, stored_status, log_message)
        except Exception as e:
            self._finish_custom_status_request(
                "Request failed", log_message=f"Custom status error: {e}"
            )

    def _finish_custom_status_request(self, message, stored_status=None, log_message=None):
        def finish():
            if self._closing:
                return
            self._status_request_running = False
            self.custom_status_info_var.set(message)
            if stored_status is not None:
                self._set_custom_status_editor(stored_status)
            if log_message:
                self.log(log_message)
        try:
            self.root.after(0, finish)
        except tk.TclError:
            pass

    def _test_push_notification(self):
        if not self.var_push_topic.get().strip():
            self.log("No notification topic configured.")
            return
        if not self.var_push_enabled.get():
            self.log("Enable notifications before sending a test.")
            return
        if self.bot.notifier.send_test():
            self.log("Test notification sent.")

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", tk.END)
        self.log_box.configure(state="disabled")

    def _schedule_save(self):
        if self._closing or self._loading_profile:
            return
        if self._save_after_id is not None:
            try:
                self.root.after_cancel(self._save_after_id)
            except Exception:
                pass
        def persist():
            self._save_after_id = None
            self._save_profiles_now()
        self._save_after_id = self.root.after(500, persist)

    def _save_profiles_now(self):
        self.profile_store["active_profile"] = self.active_profile
        self.profile_store["profiles"][self.active_profile] = copy.deepcopy(self.live_config)
        return save_profile_store(self.profile_store)

    def log(self, message):
        if self._closing:
            return
        def write():
            if self._closing or not self.log_box.winfo_exists():
                return
            now = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            self.log_box.configure(state="normal")
            self.log_box.insert("end", f"[{now}] ", "ts")
            self.log_box.insert("end", f"{message}\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        try:
            self.root.after(0, write)
        except tk.TclError:
            pass

    def _poll_connection(self):
        if self._closing:
            return
        connected = False
        if self.client.is_connected:
            try:
                response = self.client.get("/lol-gameflow/v1/gameflow-phase")
                connected = response.status_code not in (401, 403)
            except Exception:
                self.client.disconnect()
        if not connected:
            try:
                self.client.connect()
                response = self.client.get("/lol-gameflow/v1/gameflow-phase")
                connected = response.status_code not in (401, 403)
            except Exception:
                self.client.disconnect()
                connected = False
        if connected != self._connection_state:
            self._connection_state = connected
            self._set_connection_status(connected)
            if connected:
                self.log("Connected to the League of Legends client.")
            else:
                self.log("League client disconnected.")
                if self.bot_running:
                    self._stop_bot("Bot stopped because the League client disconnected.")
        if connected and not self.champion_data.loaded:
            if self.champion_data.load(self.client):
                count = len(self.champion_data.names_sorted)
                self.champion_count_var.set(f"{count} loaded")
                self.log(f"Loaded {count} champions.")
                self._refresh_all_champion_widgets()
        self._poll_after_id = self.root.after(2500, self._poll_connection)

    def _refresh_all_champion_widgets(self):
        self.ban_editor.refresh_champion_source()
        self.pick_editor.refresh_champion_source()
        self.hover_picker.refresh_champion_source()
        self.favorites_panel.refresh_champion_source()

    def _toggle_bot(self):
        if self.bot_running:
            self._stop_bot("Bot stopped. All pending automation was cancelled.")
            return
        if not self.client.is_connected or self._connection_state is not True:
            self.log("Cannot start - League client is not connected.")
            self._show_page("activity")
            return
        if self.bot.start():
            self.bot_running = True
            self.bot_state_var.set("Running")
            self.toggle_btn.set_text("Stop Bot")
            self._set_profile_controls_enabled(False)
            self.profile_notice_var.set("Stop the bot to change profiles.")
            self.log("Bot started.")

    def _stop_bot(self, message=None):
        self.bot.stop()
        self.bot_running = False
        self.bot_state_var.set("Stopped")
        self.toggle_btn.set_text("Start Bot")
        self._set_profile_controls_enabled(True)
        self.profile_notice_var.set("")
        if message:
            self.log(message)

    def _on_close(self):
        if self.live_config.get("minimize_to_tray") and TRAY_AVAILABLE:
            self._minimize_to_tray()
            return
        self._final_close()

    def _final_close(self):
        if self._closing:
            return
        self._closing = True
        self._sync_custom_status_draft()
        self.bot.stop()
        self.bot_running = False
        for after_id in (self._poll_after_id, self._save_after_id):
            if after_id is not None:
                try:
                    self.root.after_cancel(after_id)
                except Exception:
                    pass
        self._save_profiles_now()
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
            self._tray_icon = None
        self.root.destroy()

    def _minimize_to_tray(self):
        if self._tray_icon is not None:
            self.root.withdraw()
            return
        self.root.withdraw()
        self._tray_icon = self._build_tray_icon()
        threading.Thread(target=self._tray_icon.run, name="LeagueArenaTools-Tray", daemon=True).start()

    def _build_tray_icon(self):
        try:
            image = Image.open(get_resource_path("league_arena_tools.ico"))
        except Exception:
            image = Image.new("RGB", (64, 64), color=(124, 92, 255))
        specs = [
            ("Auto Accept", "var_accept"), ("Auto Ban", "var_ban"),
            ("Auto Pick", "var_pick"), ("Hover", "var_hover"),
            ("Auto Re-queue", "var_auto_requeue"),
        ]
        items = [
            pystray.MenuItem(label, self._tray_toggle_action(attr),
                             checked=self._tray_toggle_checked(attr))
            for label, attr in specs
        ]
        menu = pystray.Menu(
            pystray.MenuItem("Show", self._tray_show, default=True),
            pystray.Menu.SEPARATOR, *items, pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._tray_quit),
        )
        return pystray.Icon("league_arena_tools", image, f"{APP_TITLE} v{__version__}", menu)

    def _tray_toggle_action(self, variable_name):
        def action(icon, item):
            def apply():
                variable = getattr(self, variable_name)
                variable.set(not variable.get())
            self.root.after(0, apply)
            icon.update_menu()
        return action

    def _tray_toggle_checked(self, variable_name):
        return lambda item: getattr(self, variable_name).get()

    def _tray_show(self, icon, item):
        self.root.after(0, self._restore_from_tray)

    def _restore_from_tray(self):
        if self._tray_icon is not None:
            self._tray_icon.stop()
            self._tray_icon = None
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _tray_quit(self, icon, item):
        if self._tray_icon is not None:
            self._tray_icon.stop()
            self._tray_icon = None
        self.root.after(0, self._final_close)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
