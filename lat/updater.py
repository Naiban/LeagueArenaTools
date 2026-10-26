"""Self-update from GitHub Releases: release checks, verified download and
the batch script that swaps the EXE."""

import os
import re
import hashlib
from urllib.parse import unquote, urlparse

from lat.constants import APP_TITLE, __version__
from lat.http import _http
from lat.paths import get_base_dir


GITHUB_OWNER = "Naiban"
GITHUB_REPO = "LeagueArenaTools"
GITHUB_LATEST_RELEASE_API = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)
UPDATE_ASSET_NAME = "LeagueArenaTools.exe"
MAX_UPDATE_SIZE = 250 * 1024 * 1024


class UpdateError(Exception):
    """Raised when release metadata or an update download is unsafe."""


def _version_tuple(value):
    match = re.fullmatch(r"v?([0-9]+(?:\.[0-9]+)*)", str(value).strip(), re.IGNORECASE)
    if not match:
        raise UpdateError(f"Invalid release version: {value!r}")
    return tuple(int(part) for part in match.group(1).split("."))


def _is_newer_version(candidate, current):
    candidate_parts = _version_tuple(candidate)
    current_parts = _version_tuple(current)
    width = max(len(candidate_parts), len(current_parts))
    return candidate_parts + (0,) * (width - len(candidate_parts)) > (
        current_parts + (0,) * (width - len(current_parts))
    )


def _validate_release_asset_url(url):
    parsed = urlparse(str(url))
    expected_prefix = f"/{GITHUB_OWNER}/{GITHUB_REPO}/releases/download/"
    path = unquote(parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or not path.startswith(expected_prefix)
        or path.rsplit("/", 1)[-1] != UPDATE_ASSET_NAME
    ):
        raise UpdateError("The release asset URL is not an expected GitHub download.")
    return url


def _release_update(release, current_version=__version__):
    if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
        raise UpdateError("GitHub did not return a stable published release.")
    tag = str(release.get("tag_name") or "").strip()
    if not _is_newer_version(tag, current_version):
        return None
    assets = [
        asset for asset in release.get("assets", [])
        if isinstance(asset, dict) and asset.get("name") == UPDATE_ASSET_NAME
    ]
    if len(assets) != 1:
        raise UpdateError(f"Release {tag} must contain exactly one {UPDATE_ASSET_NAME} asset.")
    asset = assets[0]
    digest = str(asset.get("digest") or "")
    if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise UpdateError("The release asset has no valid SHA-256 digest.")
    size = asset.get("size")
    if not isinstance(size, int) or size <= 0 or size > MAX_UPDATE_SIZE:
        raise UpdateError("The release asset size is invalid or exceeds the safety limit.")
    url = _validate_release_asset_url(asset.get("browser_download_url"))
    return {
        "version": tag.lstrip("vV"),
        "tag": tag,
        "url": url,
        "digest": digest.split(":", 1)[1].lower(),
        "size": size,
    }


def _download_update(update, destination, session=None):
    session = session or _http()
    hasher = hashlib.sha256()
    downloaded = 0
    try:
        with open(destination, "wb") as output:
            with session.get(
                update["url"], stream=True, timeout=(10, 60), allow_redirects=True,
                headers={"Accept": "application/octet-stream", "User-Agent": APP_TITLE},
            ) as response:
                response.raise_for_status()
                final_host = (urlparse(response.url).hostname or "").lower()
                if final_host != "github.com" and not final_host.endswith(".githubusercontent.com"):
                    raise UpdateError("GitHub redirected the update to an unexpected host.")
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if downloaded > MAX_UPDATE_SIZE or downloaded > update["size"]:
                        raise UpdateError("The update download exceeded its declared size.")
                    hasher.update(chunk)
                    output.write(chunk)
        if downloaded != update["size"]:
            raise UpdateError("The update download is incomplete.")
        if hasher.hexdigest().lower() != update["digest"]:
            raise UpdateError("The downloaded update failed SHA-256 verification.")
        return destination
    except Exception:
        try:
            os.remove(destination)
        except OSError:
            pass
        raise


def build_update_script(pid, executable, downloaded_path, failed_marker, version):
    """Batch script that swaps the EXE once this process has exited.

    If the file stays locked (antivirus scan, second instance) the old
    version is started again and a marker tells it the update failed, so the
    user is never left without a running app."""
    def batch_path(path):
        return os.path.abspath(path).replace("%", "%%")

    batch_executable, batch_download = batch_path(executable), batch_path(downloaded_path)
    batch_marker = batch_path(failed_marker)
    safe_version = re.sub(r"[^0-9A-Za-z.-]", "", str(version)) or "unknown"
    # System tools by full path: a Git/MSYS directory early in PATH would
    # otherwise supply GNU find/timeout. timeout.exe also fails without a
    # console window, so ping provides the one-second pause.
    system32 = "%SystemRoot%\\System32"
    pause = f'"{system32}\\ping.exe" -n 2 127.0.0.1 >NUL'
    return (
        "@echo off\r\n"
        "setlocal\r\n"
        ":wait\r\n"
        f'"{system32}\\tasklist.exe" /FI "PID eq {pid}" /NH 2>NUL'
        f' | "{system32}\\find.exe" "{pid}" >NUL\r\n'
        f"if not errorlevel 1 ({pause} & goto wait)\r\n"
        "set attempts=0\r\n"
        ":replace\r\n"
        f'move /Y "{batch_download}" "{batch_executable}" >NUL 2>&1\r\n'
        "if not errorlevel 1 goto restart\r\n"
        "set /A attempts+=1\r\n"
        "if %attempts% GEQ 20 goto failed\r\n"
        f"{pause}\r\n"
        "goto replace\r\n"
        ":failed\r\n"
        f'>"{batch_marker}" echo {safe_version}\r\n'
        ":restart\r\n"
        f'start "" "{batch_executable}"\r\n'
        f'del /Q "{batch_download}" >NUL 2>&1\r\n'
        'del /Q "%~f0"\r\n'
    )


def update_failed_marker_path():
    return os.path.join(get_base_dir(), f".{APP_TITLE}-update-failed")


def consume_update_failed_marker():
    """Version of an update the last install could not apply, or None."""
    path = update_failed_marker_path()
    try:
        with open(path, "r", encoding="ascii", errors="replace") as marker:
            version = marker.read().strip()
        os.remove(path)
    except OSError:
        return None
    return version or "unknown"
