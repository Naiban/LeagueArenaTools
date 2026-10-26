"""Connection to the local League Client API (LCU) and the champion list
it serves."""

import os
import re
import base64
import threading

from lat.http import _http


PORT_RE = re.compile(r"--app-port=([0-9]+)")
TOKEN_RE = re.compile(r"--remoting-auth-token=([\w-]+)")


class LCUNotFoundError(Exception):
    """Raised when a running LoL client cannot be found."""


LEAGUE_UX_PROCESSES = ("leagueclientux.exe", "leagueclientuxheadless.exe")
DEFAULT_LOCKFILE_DIRS = (
    r"C:\Riot Games\League of Legends",
    r"D:\Riot Games\League of Legends",
)


def parse_lockfile(text):
    """'LeagueClient:<pid>:<port>:<password>:https' -> (port, password)."""
    parts = (text or "").strip().split(":")
    if len(parts) >= 5 and parts[2].isdigit() and parts[3]:
        return parts[2], parts[3]
    return None


class LCUClient:
    """Thread-safe LCU access. Bot workers, the connection monitor and the
    history tab share one instance, so the credentials live in a single
    immutable tuple that is swapped atomically and read once per request."""

    PROBE_PATH = "/lol-gameflow/v1/gameflow-phase"

    def __init__(self):
        self._conn = None            # (port, token, base_url, headers) or None
        self._session = None
        self._session_lock = threading.Lock()

    @property
    def session(self):
        if self._session is None:
            with self._session_lock:
                if self._session is None:
                    session = _http().Session()
                    # The League client uses a local self-signed certificate.
                    # Keep TLS, but only skip verification for this session.
                    session.verify = False
                    self._session = session
        return self._session

    @property
    def is_connected(self):
        return self._conn is not None

    @property
    def port(self):
        conn = self._conn
        return conn[0] if conn else None

    @property
    def token(self):
        conn = self._conn
        return conn[1] if conn else None

    @property
    def base_url(self):
        conn = self._conn
        return conn[2] if conn else None

    def connect(self):
        port, token = self._find_credentials()
        self._set_credentials(port, token)

    def _set_credentials(self, port, token):
        auth = base64.b64encode(f"riot:{token}".encode()).decode()
        self._conn = (port, token, f"https://127.0.0.1:{port}",
                      {"Authorization": f"Basic {auth}"})

    def disconnect(self):
        self._conn = None

    def probe(self, timeout=3):
        try:
            response = self._request("GET", self.PROBE_PATH, timeout=timeout)
        except Exception:
            return False
        return response.status_code not in (401, 403)

    def refresh(self):
        """Make sure the credentials are current and the API answers.

        Keeps working credentials through a slow response and only drops them
        once the client process is really gone, so in-flight bot requests are
        not cut off by a single timeout (common while a game is loading)."""
        if self._conn is not None and self.probe():
            return True
        try:
            port, token = self._find_credentials()
        except LCUNotFoundError:
            self.disconnect()
            return False
        if (port, token) != (self.port, self.token):
            self._set_credentials(port, token)
        return self.probe()

    def _find_credentials(self):
        import psutil

        found_process = False
        lockfile_dirs = []
        # Only the name is fetched for every process; the (slower) command
        # line is read just for the League client itself.
        for process in psutil.process_iter(["name"]):
            name = (process.info.get("name") or "").lower()
            if name not in LEAGUE_UX_PROCESSES:
                continue
            found_process = True
            try:
                arguments = process.cmdline() or []
            except psutil.AccessDenied:
                # The client runs elevated: fall back to its lockfile.
                try:
                    lockfile_dirs.append(os.path.dirname(process.exe()))
                except (psutil.Error, OSError):
                    pass
                continue
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            commandline = " ".join(arguments)
            port_match = PORT_RE.search(commandline)
            token_match = TOKEN_RE.search(commandline)
            if port_match and token_match:
                return port_match.group(1), token_match.group(1)
            for argument in arguments:
                if argument.startswith("--install-directory="):
                    lockfile_dirs.append(argument.split("=", 1)[1].strip('"'))
        if not found_process:
            raise LCUNotFoundError(
                "Cannot find the LeagueClientUx.exe process. Start the League client and try again."
            )
        for directory in (*lockfile_dirs, *DEFAULT_LOCKFILE_DIRS):
            try:
                with open(os.path.join(directory, "lockfile"), "r", encoding="utf-8") as f:
                    credentials = parse_lockfile(f.read())
            except OSError:
                continue
            if credentials:
                return credentials
        raise LCUNotFoundError(
            "No connection data found. Is the League of Legends client running?"
        )

    def _request(self, method, path, json=None, timeout=5):
        conn = self._conn
        if conn is None:
            raise LCUNotFoundError("League client is not connected.")
        return self.session.request(method, conn[2] + path, json=json,
                                    headers=conn[3], timeout=timeout)

    def get(self, path):
        return self._request("GET", path)

    def post(self, path, json=None):
        return self._request("POST", path, json=json)

    def put(self, path, json=None):
        return self._request("PUT", path, json=json)

    def patch(self, path, json=None):
        return self._request("PATCH", path, json=json)

    def websocket_target(self):
        """(url, header) from one credential snapshot, or (None, None)."""
        conn = self._conn
        if conn is None:
            return None, None
        return f"wss://127.0.0.1:{conn[0]}/", f"Authorization: {conn[3]['Authorization']}"


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


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
