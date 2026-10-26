"""HTTP access through requests, imported on first use. Tests replace
_requests_module with a fake."""

import threading


# requests, psutil, websocket-client, pystray and Pillow are imported on first
# use. Together they cost more than half of the interpreter start-up time, and
# none of them is needed before the window is on screen.
_requests_module = None
_requests_lock = threading.Lock()


def _http():
    """The requests module, imported once on first use (any thread)."""
    global _requests_module
    if _requests_module is None:
        with _requests_lock:
            if _requests_module is None:
                import requests
                import urllib3
                # The League client only offers a self-signed certificate on
                # 127.0.0.1; without this every LCU call emits a warning.
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                _requests_module = requests
    return _requests_module
