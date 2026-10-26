"""The bot - auto accept / auto ban / auto pick / hover (also works in Arena),
re-queue, champ select message and game notifications, driven by LCU
websocket events."""

import json
import time
import random
import threading

from lat.constants import BRAVERY_KEY, BRAVERY_LABEL, GAME_RUNNING_PHASES
from lat.history.data import (
    format_game_end_message, is_finished_game, LIVE_CLIENT_STATS_URL, match_history_path,
    parse_history_games, summarize_history_game,
)
from lat.http import _http
from lat.lcu import ChampionData, LCUClient, _normalize
from lat.notify import PushNotifier


WEBSOCKET_RECONNECT_DELAY = 0.25
WEBSOCKET_MAX_RECONNECT_DELAY = 3.0
WEBSOCKET_IDLE_TIMEOUT = 5
WEBSOCKET_PING_INTERVAL = 30

GAME_END_REPORT_ATTEMPTS = 40
GAME_END_REPORT_INTERVAL_S = 4.0
HOVER_RETRY_S = 2.0


class LeagueArenaToolsBot:
    def __init__(self, client: LCUClient, log_callback, live_config: dict, champion_data: ChampionData,
                 stats_callback=None, settings_callback=None):
        self.client = client
        self.log = log_callback
        self.config = live_config
        self.champion_data = champion_data
        self.notifier = PushNotifier(live_config, log_callback)
        self._stats_callback = stats_callback
        self._settings_callback = settings_callback

        self._running = False
        self._lifecycle_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._stop_event.set()
        self._event_thread = None
        self._active_websocket = None
        self._ready_check_lock = threading.Lock()

        # Champ select runs on its own worker. The websocket thread only drops
        # the newest session into a one-slot mailbox, so a long confirm delay
        # never holds up ready checks or phase changes, and stale sessions are
        # skipped instead of queued.
        self._champ_select_pulse = threading.Event()
        self._cs_lock = threading.Lock()
        self._cs_latest = None
        self._cs_thread = None
        self._cs_epoch = 0           # bumped on every champ select entry/exit
        self._cs_seen_epoch = -1     # epoch the worker's state belongs to
        self._cs_kind_status = {}    # "ban"/"pick" -> "open" | "done"
        self._cs_inflight_kind = None
        self._cs_hover_reset = False

        self._done_actions = set()
        self._hovered_actions = set()
        self._pending_hovers = {}
        self._last_hover_attempt = {}
        self._warned_names = set()
        self._last_action_debug = ""
        self._last_hover_log = ""
        self._last_phase = None
        self._requeue_suppressed = False
        self._requeue_worker_running = False
        self._requeue_worker_lock = threading.Lock()
        self._requeue_thread = None
        self._rejoin_thread = None
        self._last_ready_check_state = None
        self._ready_check_open = False
        self._ready_check_generation = 0
        self._accept_pending = False
        self._bravery_routes = None
        self._bravery_unavailable_logged = False
        self._champ_select_reset_done = False
        self._lobby_message_state = None   # None | "sending" | "sent"

        # Settings changed mid-run that only take effect at a later event.
        self._pending_settings = {}        # key -> "ready_check" | "champ_select"
        self._pending_lock = threading.Lock()

        self._game_loaded_watch_thread = None
        self._game_loaded_watch_stop = threading.Event()
        self._live_client_session = None

        # Game-finished notification: armed when a game starts, sent once the
        # finished game shows up in match history.
        self._game_end_armed = False
        self._game_end_ids = (None, None)   # (gameflow game id, newest history id)
        self._game_end_armed_at = 0.0
        # Champions the client offers this player, per champ select and kind.
        self._available_cache_key = None
        self._available_cache = None

    @property
    def phase(self):
        return self._last_phase

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
            self._ready_check_open = False
            self._accept_pending = False
            self._champ_select_reset_done = False
            self._lobby_message_state = None
            self._cs_kind_status = {}
            self._cs_inflight_kind = None
            self._game_end_armed = False
            with self._cs_lock:
                self._cs_latest = None
                self._champ_select_pulse.clear()
            with self._pending_lock:
                self._pending_settings.clear()
            self._event_thread = threading.Thread(
                target=self._event_loop,
                args=(stop_event,),
                name="LeagueArenaTools-LCU",
                daemon=True,
            )
            self._cs_thread = threading.Thread(
                target=self._champ_select_loop,
                args=(stop_event,),
                name="LeagueArenaTools-ChampSelect",
                daemon=True,
            )
            self._event_thread.start()
            self._cs_thread.start()
            return True

    def stop(self):
        with self._lifecycle_lock:
            if not self._running and self._stop_event.is_set():
                return False
            self._running = False
            self._stop_event.set()
            self._champ_select_pulse.set()
            self._ready_check_generation += 1
            self._accept_pending = False
            self._cs_epoch += 1
            self._requeue_suppressed = True
            self._stop_game_loaded_watch()
            websocket_connection = self._active_websocket
            self._active_websocket = None
            with self._pending_lock:
                self._pending_settings.clear()

        if websocket_connection is not None:
            try:
                websocket_connection.close()
            except Exception:
                pass

        current = threading.current_thread()
        for worker in (self._event_thread, self._cs_thread, self._requeue_thread, self._rejoin_thread):
            if worker is not None and worker is not current and worker.is_alive():
                worker.join(timeout=0.35)
        return True

    def _is_active(self, stop_event=None):
        event = stop_event or self._stop_event
        return self._running and self._stop_event is event and not event.is_set()

    def _emit_stat(self, key):
        if self._stats_callback is None:
            return
        try:
            self._stats_callback(key)
        except Exception:
            pass

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
        try:
            import websocket
        except ImportError:
            self.log("LCU websocket unavailable - requires websocket-client.")
            return
        self.log("Bot started. ReadyCheck and ChampSelect run event-only via the LCU websocket.")

        retry_delay = WEBSOCKET_RECONNECT_DELAY
        outage_logged = False
        while self._is_active(stop_event):
            ws = None
            try:
                ws_url, auth_header = self.client.websocket_target()
                if not ws_url:
                    if not self._wait(0.5, stop_event):
                        return
                    continue

                ws = websocket.create_connection(
                    ws_url,
                    header=[auth_header],
                    sslopt={"cert_reqs": 0},
                    timeout=3,
                )
                with self._lifecycle_lock:
                    if not self._is_active(stop_event):
                        ws.close()
                        return
                    self._active_websocket = ws
                ws.settimeout(WEBSOCKET_IDLE_TIMEOUT)
                ws.send(json.dumps([5, "OnJsonApiEvent_lol-champ-select_v1_session"]))
                ws.send(json.dumps([5, "OnJsonApiEvent_lol-gameflow_v1_gameflow-phase"]))
                ws.send(json.dumps([5, "OnJsonApiEvent_lol-matchmaking_v1_ready-check"]))
                retry_delay = WEBSOCKET_RECONNECT_DELAY
                self._bootstrap_event_state()
                self.log("LCU websocket reconnected." if outage_logged
                         else "LCU websocket active - no polling needed.")
                outage_logged = False

                quiet_periods = 0
                while self._is_active(stop_event):
                    try:
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        # A silent socket can be a dead one (client froze or
                        # restarted). Ping now and then; a failed ping drops
                        # into the reconnect path below.
                        quiet_periods += 1
                        if quiet_periods * WEBSOCKET_IDLE_TIMEOUT >= WEBSOCKET_PING_INTERVAL:
                            quiet_periods = 0
                            ws.ping()
                        continue
                    except Exception:
                        break
                    quiet_periods = 0
                    if not raw:
                        continue
                    try:
                        event = json.loads(raw)
                    except Exception:
                        continue
                    try:
                        self._handle_lcu_event(event)
                    except Exception as e:
                        self.log(f"Event handling error: {e}")
            except Exception as e:
                if self._is_active(stop_event) and not outage_logged:
                    self.log(f"LCU websocket unavailable ({e}) - reconnecting.")
                    outage_logged = True
            finally:
                with self._lifecycle_lock:
                    if self._active_websocket is ws:
                        self._active_websocket = None
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass
            # Back off while the client is unreachable instead of retrying
            # (and logging) four times per second.
            if not self._wait(retry_delay, stop_event):
                return
            retry_delay = min(retry_delay * 2, WEBSOCKET_MAX_RECONNECT_DELAY)

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
                self._on_ready_check_event(r.json())
        except Exception:
            pass
        if self._last_phase == "ChampSelect":
            try:
                r = self.client.get("/lol-champ-select/v1/session")
                if r.status_code == 200:
                    self._queue_champ_select(r.json())
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
                self._on_ready_check_event(data)
            return

        if name != "OnJsonApiEvent_lol-champ-select_v1_session":
            return

        event_type = payload.get("eventType")
        if event_type == "Create":
            self._reset_champ_select_state()
        if event_type in ("Create", "Update"):
            session = payload.get("data")
            if isinstance(session, dict):
                self._queue_champ_select(session)

    def _queue_champ_select(self, session):
        with self._cs_lock:
            self._cs_latest = (self._cs_epoch, session)
            self._champ_select_pulse.set()

    def _champ_select_loop(self, stop_event):
        while self._is_active(stop_event):
            self._champ_select_pulse.wait(1.0)
            with self._cs_lock:
                queued, self._cs_latest = self._cs_latest, None
                self._champ_select_pulse.clear()
            if queued is None or not self._is_active(stop_event):
                continue
            epoch, session = queued
            if epoch != self._cs_epoch:
                continue  # champ select ended or restarted since it was queued
            try:
                self._handle_champ_select(session)
            except Exception as e:
                self.log(f"Champ select error: {e}")

    def recheck_champ_select(self):
        """Re-run champ select logic on a fresh session (after a setting
        change), without waiting for the client to push the next update."""
        stop_event = self._stop_event

        def worker():
            if not self._is_active(stop_event) or self._last_phase != "ChampSelect":
                return
            try:
                r = self.client.get("/lol-champ-select/v1/session")
                if r.status_code == 200 and self._is_active(stop_event):
                    self._queue_champ_select(r.json())
            except Exception:
                pass

        threading.Thread(target=worker, name="LeagueArenaTools-Recheck", daemon=True).start()

    def recheck_ready_check(self):
        stop_event = self._stop_event

        def worker():
            if not self._is_active(stop_event):
                return
            try:
                r = self.client.get("/lol-matchmaking/v1/ready-check")
                if r.status_code == 200 and self._is_active(stop_event):
                    self._last_ready_check_state = None
                    self._handle_ready_check(r.json())
            except Exception:
                pass

        threading.Thread(target=worker, name="LeagueArenaTools-Recheck", daemon=True).start()

    def _handle_phase_change(self, phase):
        if not self._is_active():
            return
        if phase == self._last_phase:
            return
        previous_phase = self._last_phase
        self._last_phase = phase
        self.log(f"Game phase: {phase}")
        self._last_ready_check_state = None
        if phase != "ReadyCheck":
            self._ready_check_open = False
        if phase == "ChampSelect":
            self._reset_champ_select_state()
            self._settings_now_applied("champ_select")
        else:
            if previous_phase == "ChampSelect":
                # Abort any pick still waiting out its confirm delay.
                self._cs_epoch += 1
            self._champ_select_reset_done = False

        if previous_phase == "ChampSelect" and phase not in ("GameStart", "InProgress"):
            self._emit_stat("dodged")
            self.notifier.notify("dodge", "Lobby reset - someone dodged or champ select was interrupted.")

        if phase == "InProgress" and previous_phase != "InProgress":
            # Starting the bot mid-game (no previous phase) or coming back
            # from a client reconnect is not a new game: no count, and no
            # "game loaded" alert for a game that is long running.
            if previous_phase not in (None, "Reconnect"):
                self._emit_stat("games")
                self._start_game_loaded_watch()
        elif previous_phase == "InProgress" and phase != "InProgress":
            self._stop_game_loaded_watch()

        if phase in GAME_RUNNING_PHASES:
            if not self._game_end_armed:
                self._arm_game_end_report()
        elif self._game_end_armed:
            self._start_game_end_report()

        if phase == "Lobby" and previous_phase != "Lobby":
            if previous_phase in ("Matchmaking", "ReadyCheck"):
                # Back in the lobby without a game starting: the queue was
                # cancelled (X on the timer) or the ready check was declined.
                # Stand down until a dodge or a finished game brings up the
                # next lobby, or until the player searches manually.
                self._requeue_suppressed = True
                self.log("Auto re-queue: queue cancelled - standing by.")
            else:
                # Fresh lobby (dodge, post-game, or newly created): requeue
                # again.
                self._requeue_suppressed = False
            self._maybe_auto_requeue()

        if phase == "ChampSelect" and previous_phase != "ChampSelect":
            self._send_champ_select_message()

        if phase == "None" and previous_phase in ("WaitingForStats", "PreEndOfGame", "EndOfGame"):
            self._maybe_auto_rejoin_lobby()

    def _send_champ_select_message(self):
        stop_event = self._stop_event
        if not self._is_active(stop_event) or not self.config.get("lobby_message_enabled"):
            return False
        if not (self.config.get("lobby_message_text") or "").strip():
            return False
        if self._lobby_message_state is not None:
            return False
        self._lobby_message_state = "sending"
        threading.Thread(
            target=self._send_champ_select_message_worker,
            args=(stop_event, self._cs_epoch),
            name="LeagueArenaTools-LobbyMessage",
            daemon=True,
        ).start()
        return True

    def _send_champ_select_message_worker(self, stop_event, epoch):
        try:
            self._deliver_champ_select_message(stop_event, epoch)
        finally:
            if self._lobby_message_state == "sending" and epoch == self._cs_epoch:
                self._lobby_message_state = None

    def _deliver_champ_select_message(self, stop_event, epoch):
        # Small initial delay - if we enter champ select faster than the
        # rest of the lobby, the message can land before other players even
        # have the chat loaded to see it.
        if not self._wait(1.0, stop_event):
            return
        # The champ select chat room is created fresh each time, so it can take
        # a moment to become available - retry a few times before giving up.
        conv_id = None
        for _ in range(8):
            if not self._is_active(stop_event) or epoch != self._cs_epoch:
                return
            conv_id = self._find_champ_select_conversation_id()
            if conv_id:
                break
            if not self._wait(0.5, stop_event):
                return
        if not conv_id:
            self.log("Champ select message: chat not found, skipped.")
            return
        # Read the text at send time so edits made in the meantime are used.
        text = (self.config.get("lobby_message_text") or "").strip()
        if not self._is_active(stop_event) or epoch != self._cs_epoch:
            return
        if not self.config.get("lobby_message_enabled") or not text:
            return
        try:
            resp = self.client.post(f"/lol-chat/v1/conversations/{conv_id}/messages", json={"body": text, "type": "chat"})
            if resp.status_code in (200, 201, 204):
                self._lobby_message_state = "sent"
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
        # After a game the client normally recreates the lobby itself. Wait
        # for it and NEVER create a lobby here - if no lobby exists the
        # player left or disbanded on purpose and we stay stopped.
        if not self._wait(1.5, stop_event):
            return
        if not self._is_active(stop_event):
            return
        try:
            existing = self.client.get("/lol-lobby/v2/lobby")
            if existing.status_code == 200 and existing.json():
                # Lobby exists - the regular Lobby-entry hook starts the search.
                return
        except Exception:
            pass
        self.log("Auto re-queue: no lobby found after the game - standing by.")

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
                    # A failed request (client busy, lobby not ready yet) is
                    # retried on the next pass instead of ending re-queue.
                    self.log(f"Auto re-queue error: {e} - retrying.")
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
        t = threading.Thread(target=self._game_loaded_watch_loop, args=(stop_event,),
                             name="LeagueArenaTools-GameLoaded", daemon=True)
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
        if self._live_client_session is None:
            session = _http().Session()
            session.verify = False
            self._live_client_session = session
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

    def _arm_game_end_report(self):
        """Remember which game is running, so its result can be found in
        match history once it is over."""
        stop_event = self._stop_event
        self._game_end_armed = True
        self._game_end_ids = (None, None)
        self._game_end_armed_at = time.time()

        def capture():
            game_id = newest = None
            try:
                r = self.client.get("/lol-gameflow/v1/session")
                if r.status_code == 200:
                    game_id = ((r.json() or {}).get("gameData") or {}).get("gameId") or None
            except Exception:
                pass
            try:
                r = self.client.get(match_history_path(0, 0))
                if r.status_code == 200:
                    games = parse_history_games(r.json())
                    newest = games[0].get("gameId") if games else None
            except Exception:
                pass
            if self._is_active(stop_event):
                self._game_end_ids = (game_id, newest)

        threading.Thread(target=capture, name="LeagueArenaTools-GameId", daemon=True).start()

    def _start_game_end_report(self):
        self._game_end_armed = False
        if not self.notifier.wants("game_end"):
            return
        game_id, previous_newest = self._game_end_ids
        threading.Thread(
            target=self._game_end_report_worker,
            args=(self._stop_event, game_id, previous_newest, self._game_end_armed_at),
            name="LeagueArenaTools-GameEnd", daemon=True,
        ).start()

    def _game_end_report_worker(self, stop_event, game_id, previous_newest, armed_at):
        # The finished game reaches match history a few seconds (sometimes a
        # minute or more) after the end screen.
        for _ in range(GAME_END_REPORT_ATTEMPTS):
            if not self._wait(GAME_END_REPORT_INTERVAL_S, stop_event):
                return
            try:
                r = self.client.get(match_history_path(0, 0))
                games = parse_history_games(r.json()) if r.status_code == 200 else []
            except Exception:
                games = []
            if not games:
                continue
            newest = games[0]
            if not is_finished_game(newest, game_id, previous_newest, armed_at):
                continue
            summary = summarize_history_game(newest)
            title, message = format_game_end_message(
                summary, self.champion_data.label(summary.get("champion_id") or 0))
            self.notifier.notify("game_end", message, title=title)
            self.log(f"Game finished: {title} - {message}")
            return
        self.log("Game finished, but it did not appear in match history - no notification sent.")

    def _reset_champ_select_state(self):
        # Runs on the websocket thread; the champ select worker owns the
        # per-lobby sets and clears them when it notices the new epoch.
        if self._champ_select_reset_done:
            return
        self._cs_epoch += 1
        self._cs_kind_status = {}
        self._lobby_message_state = None
        self._champ_select_reset_done = True

    def _sync_champ_select_epoch(self):
        epoch = self._cs_epoch
        if epoch != self._cs_seen_epoch:
            self._cs_seen_epoch = epoch
            self._done_actions.clear()
            self._hovered_actions.clear()
            self._pending_hovers.clear()
            self._last_hover_attempt.clear()
            self._last_action_debug = ""
            self._last_hover_log = ""
            self._cs_hover_reset = False
        elif self._cs_hover_reset:
            # Hover champion changed mid champ select: hover again.
            self._cs_hover_reset = False
            self._hovered_actions.clear()
            self._pending_hovers.clear()
            self._last_hover_attempt.clear()
            self._last_hover_log = ""

    def _on_ready_check_event(self, data):
        if not isinstance(data, dict):
            return
        is_open = data.get("state") == "InProgress"
        if is_open and not self._ready_check_open:
            self._settings_now_applied("ready_check")
        self._ready_check_open = is_open
        # A delayed accept must not fire once the ready check has closed or
        # the player answered it: accepting after a manual decline would
        # override it, and an accept the bot did not make must not be
        # counted or notified.
        if self._accept_pending and (not is_open or self._player_answered(data)):
            self._cancel_pending_accept()
        self._handle_ready_check(data)

    @staticmethod
    def _player_answered(data):
        return data.get("playerResponse") not in (None, "", "None")

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
            # "Declined" counts as answered too: never accept over it.
            if data.get("state") != "InProgress" or self._player_answered(data):
                return
            self._ready_check_generation += 1
            generation = self._ready_check_generation
            delay_ms = 0 if self.config.get("accept_instant", True) else int(self.config.get("accept_delay_ms", 0))
            if delay_ms <= 0:
                self._accept_ready_check(generation)
            else:
                self.log(f"Accepting match in {delay_ms} ms")
                stop_event = self._stop_event
                self._accept_pending = True
                threading.Thread(
                    target=self._delayed_accept_ready_check,
                    args=(generation, delay_ms, stop_event),
                    name="LeagueArenaTools-AcceptDelay",
                    daemon=True,
                ).start()
        finally:
            self._ready_check_lock.release()

    def _delayed_accept_ready_check(self, generation, delay_ms, stop_event):
        try:
            if self._wait(delay_ms / 1000, stop_event):
                self._accept_ready_check(generation, stop_event)
        finally:
            if generation == self._ready_check_generation:
                self._accept_pending = False

    def _cancel_pending_accept(self):
        if not self._accept_pending:
            return False
        self._ready_check_generation += 1
        self._accept_pending = False
        self._last_ready_check_state = None
        return True

    def _accept_ready_check(self, generation, stop_event=None):
        stop_event = stop_event or self._stop_event
        if not self._is_active(stop_event) or generation != self._ready_check_generation:
            return
        if not self.config.get("auto_accept"):
            return  # switched off while the accept delay was running
        try:
            resp = self.client.post("/lol-matchmaking/v1/ready-check/accept")
            if resp.status_code in (200, 204):
                self.log("Match accepted")
                self._emit_stat("accepted")
                self.notifier.notify("accept", "Match was accepted automatically.")
            else:
                self.log(f"Auto Accept failed (status {resp.status_code}).")
        except Exception as e:
            self.log(f"Auto Accept error: {e}")

    # ------------------------------------------------------------------
    # Live settings: what a change made while the bot runs actually does.
    # ------------------------------------------------------------------

    def describe_setting_change(self, key):
        """Apply a mid-run setting change where it needs a nudge and report
        when it takes effect: ("active" | "pending" | "saved", detail)."""
        if not self._running:
            return "saved", "applies when the bot starts"
        cfg = self.config
        phase = self._last_phase
        in_champ_select = phase == "ChampSelect"

        if key == "auto_accept":
            if cfg.get("auto_accept"):
                if self._ready_check_open or phase == "ReadyCheck":
                    self.recheck_ready_check()
                    return "active", "accepting the open ready check now"
                return "active", "the next ready check will be accepted"
            if self._cancel_pending_accept():
                return "active", "the pending accept was cancelled"
            return "active", "ready checks are left to you"

        if key in ("accept_instant", "accept_delay_ms"):
            if self._accept_pending:
                return self._defer(key, "ready_check",
                                   "this ready check keeps its timer; applies to the next one")
            return "active", "used for the next ready check"

        if key in ("auto_ban", "auto_pick"):
            kind = "ban" if key == "auto_ban" else "pick"
            if not in_champ_select:
                return "active", "used in the next champ select"
            if cfg.get(key):
                if self._cs_kind_status.get(kind) == "done":
                    return self._defer(key, "champ_select",
                                       f"your {kind} is already locked; applies next champ select")
                self.recheck_champ_select()
                return "active", "re-checking the current champ select"
            if self._cs_inflight_kind == kind:
                return "active", f"the pending {kind} lock was cancelled"
            return "active", f"{kind}s are left to you"

        if key in ("pick_mode", "delay_ms"):
            if in_champ_select and self._cs_inflight_kind == "pick":
                return "active", "the running pick timer was adjusted"
            if in_champ_select and self._cs_kind_status.get("pick") == "done":
                return self._defer(key, "champ_select",
                                   "your pick is already locked; applies next champ select")
            return "active", "used for the next pick"

        if key in ("ban_priority", "pick_priority"):
            kind = "ban" if key == "ban_priority" else "pick"
            if in_champ_select and (self._cs_kind_status.get(kind) == "done"
                                    or self._cs_inflight_kind == kind):
                return self._defer(key, "champ_select",
                                   f"this {kind} is already chosen; applies next champ select")
            return "active", f"used for the next {kind}"

        if key in ("hover_enabled", "hover_champion"):
            if not cfg.get("hover_enabled"):
                return "active", "hover stopped"
            if not in_champ_select:
                return "active", "used in the next champ select"
            if self._cs_kind_status.get("pick") == "done":
                return self._defer(key, "champ_select",
                                   "your pick is already locked; applies next champ select")
            self._cs_hover_reset = True
            self.recheck_champ_select()
            return "active", "hovering in the current champ select"

        if key == "auto_requeue_enabled":
            if cfg.get("auto_requeue_enabled"):
                if phase == "Lobby":
                    self._requeue_suppressed = False
                    self._maybe_auto_requeue()
                    return "active", "starting matchmaking from this lobby"
                return "active", "takes over when you are back in a lobby"
            return "active", "stopped (a running search is not cancelled)"

        if key in ("lobby_message_enabled", "lobby_message_text"):
            state = self._lobby_message_state if in_champ_select else None
            if not cfg.get("lobby_message_enabled"):
                if state == "sending":
                    return "active", "the queued message was cancelled"
                return "active", "no champ select message will be sent"
            if not in_champ_select:
                return "active", "sent in the next champ select"
            if state == "sending":
                return "active", "used for the message being sent now"
            if state == "sent":
                return self._defer(key, "champ_select",
                                   "already sent here; applies next champ select")
            if key == "lobby_message_enabled" and self._send_champ_select_message():
                return "active", "sending to this champ select now"
            return self._defer(key, "champ_select", "applies next champ select")

        if key in ("push_enabled", "push_topic", "push_events"):
            return "active", "used for the next notification"

        return "active", "applied"

    def _defer(self, key, trigger, detail):
        with self._pending_lock:
            self._pending_settings[key] = trigger
        return "pending", detail

    def pending_settings(self):
        with self._pending_lock:
            return dict(self._pending_settings)

    def _settings_now_applied(self, trigger):
        with self._pending_lock:
            keys = [key for key, when in self._pending_settings.items() if when == trigger]
            for key in keys:
                del self._pending_settings[key]
        if not keys or self._settings_callback is None:
            return
        reason = "new ready check" if trigger == "ready_check" else "new champ select"
        try:
            self._settings_callback(keys, f"now in effect ({reason})")
        except Exception:
            pass

    def _handle_champ_select(self, session=None):
        if not self._is_active():
            return
        if session is None:
            r = self.client.get("/lol-champ-select/v1/session")
            if r.status_code != 200:
                return
            session = r.json()
        self._sync_champ_select_epoch()

        local_cell = session.get("localPlayerCellId")
        timer = session.get("timer") or {}
        timer_ms = timer.get("adjustedTimeLeftInPhase", 99_000)
        timer_phase = timer.get("phase", "")
        my_actions = []
        kind_status = {}
        unavailable = self._session_unavailable_champions(session)

        for group in session.get("actions", []):
            for action in group:
                if action.get("actorCellId") != local_cell or action.get("type") not in ("ban", "pick"):
                    continue
                kind = action.get("type")
                if not action.get("completed"):
                    kind_status[kind] = "open"
                else:
                    kind_status.setdefault(kind, "done")
        self._cs_kind_status = kind_status

        self._confirm_pending_hovers(session)

        # Hover once per champ select (retried until the client shows it),
        # not on every session update: re-sending would undo a champion the
        # player hovered by hand, and makes no sense after lock-in.
        if (self.config.get("hover_enabled") and timer_phase != "BAN_PICK"
                and kind_status.get("pick") != "done"
                and self._hover_attempt_due(None)):
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

                if (self.config.get("hover_enabled") and action_type == "pick"
                        and not about_to_auto_complete and self._hover_attempt_due(action_id)):
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
                    self._cs_inflight_kind = "ban"
                    try:
                        ok = self._perform_action(
                            action_id, "ban", self.config.get("ban_priority", []), timer_ms,
                            instant=True, session=session, unavailable=unavailable,
                        )
                    finally:
                        self._cs_inflight_kind = None
                    if ok:
                        self._done_actions.add(action_id)
                        self._mark_kind_done("ban")

                elif action_type == "pick" and self.config.get("auto_pick"):
                    pick_mode = self.config.get("pick_mode")
                    instant = pick_mode == "instant"
                    self._cs_inflight_kind = "pick"
                    try:
                        ok = self._perform_action(
                            action_id, "pick", self.config.get("pick_priority", []), timer_ms,
                            instant=instant, session=session, unavailable=unavailable,
                        )
                    finally:
                        self._cs_inflight_kind = None
                    if ok:
                        self._done_actions.add(action_id)
                        self._mark_kind_done("pick")
                        self._emit_stat("picked")

        debug_line = ",".join(my_actions)
        if debug_line and debug_line != self._last_action_debug:
            self._last_action_debug = debug_line
            self.log(f"Champ select actions: {debug_line}")

    def _hover_attempt_due(self, action_id):
        """A hover not yet confirmed is sent again at most every 2 s."""
        if action_id in self._hovered_actions:
            return False
        now = time.monotonic()
        if now - self._last_hover_attempt.get(action_id, -HOVER_RETRY_S) < HOVER_RETRY_S:
            return False
        self._last_hover_attempt[action_id] = now
        return True

    def _available_champions(self, kind):
        """Champion ids the client lets this player ban or pick (owned and
        enabled), or None when unknown. Fetched once per champ select."""
        cache_key = (self._cs_epoch, kind)
        if self._available_cache_key == cache_key:
            return self._available_cache
        endpoint = (
            "/lol-champ-select/v1/bannable-champions" if kind == "ban"
            else "/lol-champ-select/v1/pickable-champions"
        )
        list_name = "bannable" if kind == "ban" else "pickable"
        available = None
        try:
            r = self.client.get(endpoint)
            if r.status_code == 200:
                available = self._extract_champion_ids(r.json()) or None
            else:
                self.log(f"Cannot fetch {list_name} champion list ({r.status_code})")
        except Exception as e:
            self.log(f"Cannot fetch {list_name} champion list ({e})")
        # An empty list (asked too early) is not cached: ask again next time.
        if available:
            self._available_cache_key, self._available_cache = cache_key, available
        return available

    def _mark_kind_done(self, kind):
        status = dict(self._cs_kind_status)
        status[kind] = "done"
        self._cs_kind_status = status

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

    def _log_once(self, message):
        """Log a message once per champ select (session updates repeat)."""
        key = (self._cs_epoch, message)
        if key not in self._warned_names:
            self._warned_names.add(key)
            self.log(message)

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
        # Champions this player cannot use (not owned, disabled) are skipped
        # like banned ones, instead of failing on the same entry every update.
        available = self._available_champions(kind)
        verb = "banned" if kind == "ban" else "picked"

        if priority_entries:
            for entry, label in priority_entries:
                if entry == BRAVERY_KEY:
                    if kind == "pick" and self._perform_bravery(action_id, timer_ms, instant=instant):
                        return True
                    continue
                if entry in unavailable:
                    self.log(f"{label} unavailable - checking next {kind} priority.")
                    continue
                if available is not None and entry not in available:
                    self._log_once(f"{label} cannot be {verb} (not owned or disabled) - "
                                   f"checking next {kind} priority.")
                    continue
                if instant:
                    return self._instant_action(action_id, kind, entry)
                if not self._set_action_champion(action_id, entry, kind):
                    self.log(f"Failed to set champion for action '{kind}'")
                    return False
                if not self._sleep_before_action(timer_ms, kind):
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

        chosen = None
        if available is None:
            pool = {cid for cid in self.champion_data.name_to_id.values() if cid > 0 and cid not in unavailable}
            if pool:
                chosen = random.choice(list(pool))
        else:
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

        if not self._sleep_before_action(timer_ms, kind):
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

    def _sleep_before_action(self, timer_ms, kind="pick"):
        """Wait out the confirm delay before locking in.

        Settings are re-read every slice, so changing the delay, switching to
        Instant, or turning Auto Pick/Ban off takes effect on the running
        timer. A dodge (new champ select epoch) aborts the wait."""
        stop_event = self._stop_event
        epoch = self._cs_epoch
        started = time.monotonic()
        # Keep a one-second safety margin before the phase ends.
        latest = started + max(0, timer_ms - 1000) / 1000
        while True:
            if not self._is_active(stop_event) or epoch != self._cs_epoch:
                return False
            if not self.config.get(f"auto_{kind}"):
                self.log(f"Auto {kind.title()} was turned off - not locking in.")
                return False
            if kind == "pick" and self.config.get("pick_mode") == "instant":
                target = started
            else:
                try:
                    delay_ms = max(0, int(self.config.get("delay_ms", 500)))
                except (TypeError, ValueError):
                    delay_ms = 500
                target = min(started + delay_ms / 1000, latest)
            remaining = target - time.monotonic()
            if remaining <= 0:
                return True
            if stop_event.wait(min(remaining, 0.1)):
                return False

    def _perform_bravery(self, action_id, timer_ms, instant=False):
        if not self._is_active():
            return False
        if not instant:
            if not self._sleep_before_action(timer_ms, "pick"):
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
