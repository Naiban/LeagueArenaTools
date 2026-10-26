"""Push notifications - sent to the phone via ntfy.sh (free, no account
needed). The "topic" is a random, private identifier - it acts as a
simple verification: only a device subscribed to that exact topic
receives notifications from this app instance."""

import secrets
import threading
from urllib.parse import quote

from lat.constants import APP_TITLE
from lat.http import _http


NTFY_BASE_URL = "https://ntfy.sh"


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
        "game_end": ("Game finished", "trophy"),
    }

    def __init__(self, config, log_callback):
        self.config = config
        self.log = log_callback

    def wants(self, event_key):
        """True when a notification for event_key would actually be sent."""
        if not self.config.get("push_enabled"):
            return False
        if not (self.config.get("push_events") or {}).get(event_key, False):
            return False
        return bool((self.config.get("push_topic") or "").strip())

    def notify(self, event_key, message, title=None):
        if not self.wants(event_key):
            return
        topic = self.config.get("push_topic").strip()
        default_title, tag = self.EVENT_META.get(event_key, (APP_TITLE, "bell"))
        threading.Thread(target=self._send, args=(topic, title or default_title, message, tag),
                         daemon=True).start()

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
            # The topic is typed by the user: keep "/", "?" or "#" in it
            # from changing which URL the message is posted to.
            resp = _http().post(
                f"{NTFY_BASE_URL}/{quote(topic, safe='')}",
                data=message.encode("utf-8"),
                headers={"Title": title, "Priority": "default", "Tags": tag},
                timeout=5,
            )
            if resp.status_code >= 300:
                self.log(f"Push notification failed ({resp.status_code}) for topic '{topic}'")
        except Exception as e:
            self.log(f"Push notification error: {e}")
