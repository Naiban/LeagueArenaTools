"""Disk caches under %LOCALAPPDATA%/LeagueArenaTools: item/augment icons and
CommunityDragon JSON."""

import io
import os
import json
import time
import hashlib
import threading

from lat.constants import APP_TITLE
from lat.http import _http


ICON_CACHE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), APP_TITLE, "icon-cache")
ICON_FETCH_WORKERS = 6

# A failed icon (network error or 5xx) is retried after this many seconds.
ICON_RETRY_AFTER_S = 60
# CommunityDragon name data (~1.3 MB) is kept on disk and downloaded again
# after this long; an older copy is still used when the download fails.
DATA_CACHE_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), APP_TITLE, "data-cache")
METADATA_MAX_AGE_S = 12 * 3600


def icon_cache_path(url):
    return os.path.join(ICON_CACHE_DIR,
                        hashlib.sha1(url.encode("utf-8")).hexdigest() + ".img")


def _is_image(data):
    try:
        from PIL import Image as PILImage
        with PILImage.open(io.BytesIO(data)) as image:
            image.verify()
        return True
    except Exception:
        return False


def fetch_icon_bytes(urls, dead_urls=frozenset(), session=None):
    """Bytes of the first candidate that is a real image, or None.

    Returns (data, newly_dead): URLs that answered 404 or with something that
    is not an image are reported so they are skipped next time. Network
    errors and 5xx answers are not - the caller retries those later."""
    session = session or _http()
    newly_dead = set()
    for url in urls:
        if url in dead_urls:
            continue
        path = icon_cache_path(url)
        try:
            with open(path, "rb") as cached:
                data = cached.read()
            if data and _is_image(data):
                return data, newly_dead
        except OSError:
            pass
        try:
            response = session.get(url, timeout=(5, 15))
        except Exception:
            continue
        if response.status_code in (403, 404, 410):
            newly_dead.add(url)
            continue
        if response.status_code != 200:
            continue
        data = response.content
        if not _is_image(data):
            newly_dead.add(url)
            continue
        try:
            os.makedirs(ICON_CACHE_DIR, exist_ok=True)
            temporary = f"{path}.{threading.get_ident()}.tmp"
            with open(temporary, "wb") as output:
                output.write(data)
            os.replace(temporary, path)
        except OSError:
            pass                    # cache is an optimization only
        return data, newly_dead
    return None, newly_dead


def data_cache_path(url):
    return os.path.join(DATA_CACHE_DIR,
                        hashlib.sha1(url.encode("utf-8")).hexdigest() + ".json")


def fetch_cached_json(url, max_age_s=METADATA_MAX_AGE_S, session=None, force=False):
    """JSON from url, kept on disk between sessions -> (data, source).

    A copy younger than max_age_s is used without downloading ("cache");
    otherwise the URL is downloaded ("network"), and when that fails an
    older copy is used ("stale"). Raises when neither works. force skips
    the fresh-copy shortcut (a new patch added ids the copy lacks)."""
    path = data_cache_path(url)
    cached, age = None, None
    try:
        age = time.time() - os.path.getmtime(path)
        with open(path, "rb") as source:
            cached = json.loads(source.read().decode("utf-8"))
    except (OSError, ValueError):
        cached = None
    if cached is not None and not force and 0 <= age < max_age_s:
        return cached, "cache"
    try:
        response = (session or _http()).get(
            url, timeout=(5, 30), headers={"User-Agent": APP_TITLE})
        response.raise_for_status()
        data = response.json()
    except Exception:
        if cached is not None:
            return cached, "stale"
        raise
    try:
        os.makedirs(DATA_CACHE_DIR, exist_ok=True)
        temporary = f"{path}.{threading.get_ident()}.tmp"
        with open(temporary, "wb") as output:
            output.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
        os.replace(temporary, path)
    except OSError:
        pass                        # cache is an optimization only
    return data, "network"
