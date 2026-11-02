"""Owned skins per champion (Skin Applier tab) and the saved skin choices
the bot applies in champ select."""

from lat.history.data import cdragon_icon_candidates


SKINS_PATH = "/lol-game-data/assets/v1/skins.json"
INVENTORY_PATHS = (
    "/lol-inventory/v2/inventory/CHAMPION_SKIN",
    '/lol-inventory/v1/inventory?inventoryTypes=["CHAMPION_SKIN"]',
)


def champion_of_skin(skin_id):
    """Skin and chroma ids are championId * 1000 + n."""
    return int(skin_id) // 1000


def _item_ids(payload):
    if isinstance(payload, dict):
        payload = payload.get("items") or payload.get("CHAMPION_SKIN") or []
    ids = set()
    for item in payload if isinstance(payload, list) else []:
        if isinstance(item, dict) and item.get("owned") is not False:
            value = item.get("itemId", item.get("id"))
            if isinstance(value, int) and value > 0:
                ids.add(value)
    return ids


def fetch_owned_skin_ids(client):
    """Ids of every skin and chroma the account owns (inventory first, the
    champion inventory as fallback)."""
    owned = set()
    for path in INVENTORY_PATHS:
        try:
            response = client.get(path)
            if response.status_code == 200:
                owned |= _item_ids(response.json())
        except Exception:
            continue
        if owned:
            return owned
    try:
        response = client.get("/lol-summoner/v1/current-summoner")
        summoner_id = response.json().get("summonerId") if response.status_code == 200 else None
        if summoner_id:
            response = client.get(f"/lol-champions/v1/inventories/{summoner_id}/skins-minimal")
            if response.status_code == 200:
                for skin in response.json() or []:
                    if (skin.get("ownership") or {}).get("owned") and skin.get("id"):
                        owned.add(int(skin["id"]))
    except Exception:
        pass
    return owned


def _chroma_color(chroma):
    for color in chroma.get("colors") or []:
        if isinstance(color, str) and len(color) == 7 and color.startswith("#"):
            return color
    return "#91a0bd"


def parse_skin_catalog(skins_json, owned_ids):
    """{champion id: [skin, ...]} with the base skin first, then owned skins
    by id. A skin is {"id", "name", "is_base", "image_urls", "chromas"}, a
    chroma {"id", "name", "color"} (only owned chromas are listed)."""
    entries = skins_json.values() if isinstance(skins_json, dict) else (skins_json or [])
    catalog = {}
    for skin in entries:
        if not isinstance(skin, dict) or not isinstance(skin.get("id"), int):
            continue
        skin_id = skin["id"]
        is_base = bool(skin.get("isBase")) or skin_id % 1000 == 0
        if not is_base and skin_id not in owned_ids:
            continue
        chromas = [
            {"id": chroma["id"], "name": chroma.get("name") or "Chroma",
             "color": _chroma_color(chroma)}
            for chroma in skin.get("chromas") or []
            if isinstance(chroma, dict) and chroma.get("id") in owned_ids
        ]
        urls = []
        for key in ("splashPath", "uncenteredSplashPath", "tilePath", "loadScreenPath"):
            for url in cdragon_icon_candidates(skin.get(key)):
                if url not in urls:
                    urls.append(url)
        catalog.setdefault(champion_of_skin(skin_id), []).append({
            "id": skin_id,
            "name": skin.get("name") or str(skin_id),
            "is_base": is_base,
            "image_urls": urls,
            "chromas": chromas,
        })
    # Ids without a base skin are not League champions (other game modes).
    catalog = {cid: skins for cid, skins in catalog.items()
               if any(skin["is_base"] for skin in skins)}
    for skins in catalog.values():
        skins.sort(key=lambda s: (not s["is_base"], s["id"]))
    return catalog


def load_skin_catalog(client):
    """(catalog, error text or None) from the running client."""
    try:
        response = client.get(SKINS_PATH)
        if response.status_code != 200:
            return {}, f"skin data unavailable (HTTP {response.status_code})"
        skins_json = response.json()
    except Exception as error:
        return {}, f"skin data unavailable ({error})"
    owned = fetch_owned_skin_ids(client)
    if not owned:
        return parse_skin_catalog(skins_json, owned), "owned skins could not be read"
    return parse_skin_catalog(skins_json, owned), None


def sanitize_skin_choices(value):
    """{"<champion id>": {"skin_id": int, "name": str}} - anything else dropped."""
    choices = {}
    if not isinstance(value, dict):
        return choices
    for key, choice in value.items():
        if not str(key).isdigit() or not isinstance(choice, dict):
            continue
        skin_id = choice.get("skin_id")
        if not isinstance(skin_id, int) or skin_id <= 0:
            continue
        if champion_of_skin(skin_id) != int(key):
            continue
        choices[str(int(key))] = {"skin_id": skin_id, "name": str(choice.get("name") or "")}
    return choices
