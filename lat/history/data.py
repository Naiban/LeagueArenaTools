"""Match history data: external data sources, tolerant parsing of LCU and
Live Client payloads, Arena statistics and ratings. No UI code."""

import time


LIVE_CLIENT_BASE_URL = "https://127.0.0.1:2999/liveclientdata"
LIVE_CLIENT_STATS_URL = LIVE_CLIENT_BASE_URL + "/gamestats"
LIVE_CLIENT_PLAYERS_URL = LIVE_CLIENT_BASE_URL + "/playerlist"
LIVE_CLIENT_ACTIVE_NAME_URL = LIVE_CLIENT_BASE_URL + "/activeplayername"

ARENASWEATS_BASE_URL = "https://arenasweats.lol"
ARENASWEATS_REGIONS = (
    "euw", "na", "eune", "kr", "jp", "br", "las", "lan",
    "oce", "tr", "ru", "sea", "tw", "vn", "me",
)
CDRAGON_BASE_URL = "https://raw.communitydragon.org/latest"
ARENA_DATA_URL = CDRAGON_BASE_URL + "/cdragon/arena/en_us.json"
ITEMS_DATA_URL = (
    CDRAGON_BASE_URL + "/plugins/rcp-be-lol-game-data/global/default/v1/items.json"
)
CHERRY_AUGMENTS_URL = (
    CDRAGON_BASE_URL + "/plugins/rcp-be-lol-game-data/global/default/v1/cherry-augments.json"
)
CHAMPION_ICON_URL = (
    CDRAGON_BASE_URL + "/plugins/rcp-be-lol-game-data/global/default/v1/champion-icons/{}.png"
)
# Riot platform id -> arenasweats region. LA1 is LAN (north), LA2 is LAS
# (south); PH2/SG2/TH2 were merged into the SEA server.
PLATFORM_TO_AS_REGION = {
    "EUW1": "euw", "EUN1": "eune", "NA1": "na", "BR1": "br", "LA1": "lan",
    "LA2": "las", "OC1": "oce", "TR1": "tr", "RU": "ru", "JP1": "jp",
    "KR": "kr", "ME1": "me", "SG2": "sea", "PH2": "sea", "TH2": "sea",
    "TW2": "tw", "VN2": "vn",
}
# Queue ids from Riot's queues.json (static.developer.riotgames.com). That
# list lags behind the client: current Arena games use 1740/1750, so Arena
# is detected by the CHERRY game mode first and the queue id second.
ARENA_QUEUE_IDS = frozenset({1700, 1710, 1740, 1750})
ARENA_GAME_MODE = "CHERRY"
QUEUE_LABELS = {
    1700: "Arena", 1710: "Arena", 1740: "Arena", 1750: "Arena",
    420: "Ranked Solo/Duo", 440: "Ranked Flex",
    400: "Draft Pick", 430: "Blind Pick", 450: "ARAM",
    480: "Swiftplay", 490: "Quickplay", 900: "ARURF", 1020: "One for All",
    1090: "TFT", 1100: "Ranked TFT", 1300: "Nexus Blitz",
    1400: "Spellbook", 1900: "Pick URF", 2300: "Brawl", 2400: "ARAM Mayhem",
    700: "Clash", 720: "ARAM Clash",
}

# Pseudo game id of the game in progress in the history list.
LIVE_GAME_ID = "live"


def select_local_player(players, account_ids=frozenset(), puuid=""):
    """Pick the local player from a parsed match; falls back to first row."""
    if account_ids:
        for player in players:
            if player.get("account_id") in account_ids:
                return player
    if puuid:
        for player in players:
            if player.get("puuid") == puuid:
                return player
    return players[0] if players else {}


def order_players_by_placement(players):
    """Duo teams ordered by the placement they achieved; singles last."""
    duos, singles = {}, []
    for player in players:
        subteam = player.get("subteam_id")
        if isinstance(subteam, int):
            duos.setdefault(subteam, []).append(player)
        else:
            singles.append(player)

    def duo_key(group):
        placements = [g.get("placement") for g in group
                      if isinstance(g.get("placement"), int)]
        best = min(placements) if placements else 999
        return (best, group[0].get("participant_id") or 0)

    ordered = []
    for group in sorted(duos.values(), key=duo_key):
        ordered.extend(sorted(group,
                              key=lambda p: p.get("participant_id") or 0))
    ordered.extend(sorted(singles,
                          key=lambda p: p.get("participant_id") or 0))
    return ordered


def cdragon_icon_candidates(icon_path):
    """Ordered CDN URLs for a game asset (plugins tree + game tree)."""
    if not isinstance(icon_path, str) or not icon_path.strip():
        return []
    lowered = icon_path.replace("\\", "/").strip().lower()
    marker = "/lol-game-data/assets/"
    if marker in lowered:
        rel = lowered.split(marker, 1)[1]
    else:
        rel = lowered.lstrip("/")
    if not rel:
        return []
    base = CDRAGON_BASE_URL + "/plugins/rcp-be-lol-game-data/global/default/"
    return [base + rel, CDRAGON_BASE_URL + "/game/" + rel]


def _first_text(*values):
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _identity_name(player):
    if not isinstance(player, dict):
        return ""
    return _first_text(player.get("gameName"), player.get("riotIdGameName"),
                       player.get("summonerName"), player.get("displayName"))


def _identity_tag(player):
    if not isinstance(player, dict):
        return ""
    return _first_text(player.get("tagLine"), player.get("riotIdTagline")).upper()


def _walk_nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_nodes(child)


def find_placement(participant):
    # Arena stores the DUO placement in stats.subteamPlacement.
    for key in ("subteamPlacement", "placement", "finalPlacement", "placing"):
        value = participant.get(key)
        if isinstance(value, int) and 0 < value <= 20:
            return value
    stats = participant.get("stats")
    if isinstance(stats, dict):
        for key in ("subteamPlacement", "placement", "finalPlacement", "placing"):
            value = stats.get(key)
            if isinstance(value, int) and 0 < value <= 20:
                return value
    return None


def _win_flag(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text == "win":
            return True
        if text in ("fail", "loss", "lose"):
            return False
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    return None


def prettify_augment(raw):
    if isinstance(raw, bool) or raw is None:
        return ""
    if isinstance(raw, int):
        return f"Augment {raw}"
    text = str(raw).replace("\\", "/").split("/")[-1]
    lowered = text.lower()
    for prefix in ("arenaaugment", "augment_", "augment"):
        if lowered.startswith(prefix):
            text = text[len(prefix):]
            lowered = text.lower()
    text = text.replace("_", " ").replace("-", " ").strip()
    return text or str(raw)


def collect_augments(participant):
    """Ordered raw augment identifiers for a participant.

    Arena matches carry stats.playerAugment1..6 (ints, 0 = empty slot); the
    generic scan is only a fallback for other payload shapes.
    """
    ids = []
    seen = set()
    stats = participant.get("stats") if isinstance(participant, dict) else {}
    if isinstance(stats, dict):
        for slot in range(1, 7):
            raw = stats.get(f"playerAugment{slot}")
            if isinstance(raw, int) and raw > 0 and raw not in seen:
                seen.add(raw)
                ids.append(raw)
    for node in _walk_nodes(participant):
        for key, value in node.items():
            lowered = key.lower()
            if "augment" not in lowered or lowered.startswith("playeraugment"):
                continue
            candidates = value if isinstance(value, (list, tuple)) else [value]
            for raw in candidates:
                if isinstance(raw, bool):
                    continue
                if isinstance(raw, int):
                    if raw > 0 and raw not in seen:
                        seen.add(raw)
                        ids.append(raw)
                elif isinstance(raw, str) and raw.strip():
                    text = raw.strip()
                    if text not in seen:
                        seen.add(text)
                        ids.append(text)
    return ids[:8]


def parse_history_games(payload):
    games = ((payload or {}).get("games") or {}).get("games")
    return [game for game in (games or []) if isinstance(game, dict)]


def _stats_int(stats, *keys, default=None):
    if not isinstance(stats, dict):
        return default
    for key in keys:
        value = stats.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    return default


def participant_items(stats):
    items = []
    for slot in range(7):
        value = stats.get(f"item{slot}")
        if isinstance(value, int) and value > 0:
            items.append(value)
    return items


RIOT_ID_DISPLAY_CHARS = 18


def shorten_riot_id(name, tag, limit=RIOT_ID_DISPLAY_CHARS):
    """Riot ID for the lobby table; long game names are cut, the tag kept."""
    if not tag:
        return name if len(name) <= limit else name[:limit - 1] + "…"
    full = f"{name}#{tag}"
    if len(full) <= limit:
        return full
    keep = max(4, limit - len(tag) - 2)
    return f"{name[:keep]}…#{tag}"


def is_arena_game(game):
    """True for an Arena match (summary, detail view or raw LCU game)."""
    if not isinstance(game, dict):
        return False
    mode = game.get("game_mode", game.get("gameMode"))
    if isinstance(mode, str) and mode:
        return mode.upper() == ARENA_GAME_MODE
    return game.get("queue_id", game.get("queueId")) in ARENA_QUEUE_IDS


def summarize_history_game(game):
    participants = game.get("participants") or []
    me = participants[0] if isinstance(participants[0] if participants else None, dict) else {}
    stats = me.get("stats") or {}
    kills = _stats_int(stats, "kills", default=0)
    deaths = _stats_int(stats, "deaths", default=0)
    assists = _stats_int(stats, "assists", default=0)
    return {
        "game_id": game.get("gameId"),
        "queue_id": game.get("queueId"),
        "game_mode": game.get("gameMode"),
        "creation": game.get("gameCreation"),
        "duration_s": game.get("gameDuration"),
        "champion_id": me.get("championId"),
        "placement": find_placement(me),
        "win": _win_flag(stats.get("win")),
        "kda": f"{kills}/{deaths}/{assists}",
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "damage": _stats_int(stats, "totalDamageDealtToChampions"),
        "items": participant_items(stats),
        "augments": [a for a in collect_augments(me) if isinstance(a, int)],
    }


def match_history_path(begin, end):
    return ("/lol-match-history/v1/products/lol/current-summoner/matches"
            f"?begIndex={int(begin)}&endIndex={int(end)}")


def is_finished_game(game, game_id, previous_newest, armed_at):
    """Whether game (newest in match history) is the one that just ended.

    game_id comes from the gameflow session when the game started;
    previous_newest is the newest history game at that time. With neither
    known, a game created shortly before the start counts."""
    newest_id = game.get("gameId")
    if newest_id is None:
        return False
    if game_id is not None:
        return newest_id == game_id
    if previous_newest is not None:
        return newest_id != previous_newest
    creation = game.get("gameCreation")
    return isinstance(creation, (int, float)) and creation / 1000 >= armed_at - 15 * 60


def format_game_end_message(summary, champion_name):
    """(title, body) of the game-finished phone notification."""
    win, placement = summary.get("win"), summary.get("placement")
    result = "Victory" if win else ("Defeat" if win is False else "Game over")
    title = f"{result} - #{placement}" if placement else result
    queue = "Arena" if is_arena_game(summary) else QUEUE_LABELS.get(summary.get("queue_id"))
    parts = [queue] if queue else []
    parts += [champion_name, f"{summary.get('kda') or '0/0/0'} KDA"]
    if summary.get("damage"):
        parts.append(f"{summary['damage']:,} damage".replace(",", " "))
    duration = _format_duration(summary.get("duration_s"))
    if duration:
        parts.append(duration)
    return title, " · ".join(parts)


def compute_arena_stats(summaries, min_augment_games=2):
    """Placement statistics over the Arena games in summaries (newest first).

    Returns None without Arena games. Champions are ordered by games played,
    augments (taken at least min_augment_games times) by average placement."""
    games = [s for s in summaries
             if is_arena_game(s) and isinstance(s.get("placement"), int)]
    if not games:
        return None

    def bucket():
        return {"games": 0, "wins": 0, "firsts": 0, "placement_sum": 0}

    def add(entry, game):
        entry["games"] += 1
        entry["wins"] += 1 if game.get("win") else 0
        entry["firsts"] += 1 if game["placement"] == 1 else 0
        entry["placement_sum"] += game["placement"]

    def finish(key_name, key, entry):
        return {key_name: key, "games": entry["games"],
                "avg_placement": round(entry["placement_sum"] / entry["games"], 2),
                "win_rate": entry["wins"] / entry["games"],
                "firsts": entry["firsts"]}

    total, champions, augments = bucket(), {}, {}
    damage = [g["damage"] for g in games if isinstance(g.get("damage"), int)]
    kills = deaths = assists = 0
    for game in games:
        add(total, game)
        add(champions.setdefault(game.get("champion_id") or 0, bucket()), game)
        for augment in set(game.get("augments") or []):
            add(augments.setdefault(augment, bucket()), game)
        kills += game.get("kills") or 0
        deaths += game.get("deaths") or 0
        assists += game.get("assists") or 0
    champion_rows = sorted(
        (finish("champion_id", cid, entry) for cid, entry in champions.items()),
        key=lambda row: (-row["games"], row["avg_placement"]))
    augment_rows = sorted(
        (finish("augment_id", aid, entry) for aid, entry in augments.items()
         if entry["games"] >= min_augment_games),
        key=lambda row: (row["avg_placement"], -row["games"]))
    count = len(games)
    return {
        "games": count,
        "avg_placement": round(total["placement_sum"] / count, 2),
        "win_rate": total["wins"] / count,
        "firsts": total["firsts"],
        "avg_damage": round(sum(damage) / len(damage)) if damage else None,
        "kda": round((kills + assists) / max(1, deaths), 2),
        "recent": [(g["placement"], bool(g.get("win"))) for g in games[:15]],
        "champions": champion_rows,
        "augments": augment_rows,
    }


def rating_from_response(data):
    """The parts of an arenasweats player_rank answer the app shows."""
    if not isinstance(data, dict):
        return None
    top = [data.get(f"top_champ{index}") for index in (1, 2, 3)]
    return {
        "rating": data.get("rating"),
        "rank": data.get("player_rank"),
        "league": data.get("league_rank") if isinstance(data.get("league_rank"), str) else "",
        "games": data.get("games_played") if isinstance(data.get("games_played"), int) else None,
        "percentile": data.get("percentile")
                      if isinstance(data.get("percentile"), (int, float)) else None,
        "top_champions": [name for name in top if isinstance(name, str) and name],
    }


def format_rating_tooltip(rating):
    """Hover text for a lobby rating cell (None / "error" / rating dict)."""
    if rating == "error":
        return "arenasweats.lol did not answer - the rating is retried next time."
    if not isinstance(rating, dict):
        return "No Arena rating on arenasweats.lol for this season."
    lines = []
    head = " · ".join(str(part) for part in (
        rating.get("league"),
        f"{rating['rating']} rating" if rating.get("rating") is not None else None,
    ) if part)
    if head:
        lines.append(head)
    rank = rating.get("rank")
    percentile = rating.get("percentile")
    if isinstance(rank, int):
        line = f"Ladder rank #{rank:,}".replace(",", " ")
        if isinstance(percentile, (int, float)) and 0 < percentile < 100:
            line += f" (top {max(0.1, round(100 - percentile, 1)):g}%)"
        lines.append(line)
    if isinstance(rating.get("games"), int):
        lines.append(f"{rating['games']} games this season")
    if rating.get("top_champions"):
        lines.append("Best champions: " + ", ".join(rating["top_champions"]))
    return "\n".join(lines) or "Rated on arenasweats.lol"


def parse_live_players(payload, resolve_champion):
    """Live Client Data playerlist -> lobby rows shaped like match players."""
    players = []
    for index, entry in enumerate(payload if isinstance(payload, list) else [], 1):
        if not isinstance(entry, dict) or entry.get("isBot"):
            continue
        riot_id = _first_text(entry.get("riotId"))
        name = _first_text(entry.get("riotIdGameName"), riot_id.split("#", 1)[0],
                           entry.get("summonerName"))
        tag = _first_text(entry.get("riotIdTagLine"),
                          riot_id.split("#", 1)[1] if "#" in riot_id else "").upper()
        raw_champion = str(entry.get("rawChampionName") or "").rsplit("_", 1)[-1]
        champion_id = (resolve_champion(entry.get("championName") or "")
                       or resolve_champion(raw_champion))
        scores = entry.get("scores") if isinstance(entry.get("scores"), dict) else {}
        items = [item.get("itemID") for item in entry.get("items") or []
                 if isinstance(item, dict) and isinstance(item.get("itemID"), int)
                 and item["itemID"] > 0]
        players.append({
            "participant_id": index, "name": name, "tag": tag,
            "account_id": None, "puuid": None, "champion_id": champion_id,
            "subteam_id": None, "placement": None, "win": None,
            "kills": _stats_int(scores, "kills", default=0),
            "deaths": _stats_int(scores, "deaths", default=0),
            "assists": _stats_int(scores, "assists", default=0),
            "damage": None, "gold": None, "heal": None, "taken": None,
            "level": _stats_int(entry, "level"),
            "items": items, "augments": [],
        })
    return players


def build_live_view(players, active_name, game_stats):
    """Detail view for a game in progress (rendered like a finished match)."""
    stats = game_stats if isinstance(game_stats, dict) else {}
    wanted = (active_name or "").strip().lower()
    me = next((p for p in players
               if wanted and wanted in (f"{p['name']}#{p['tag']}".lower(), p["name"].lower())),
              players[0] if players else {})
    return {
        "game_id": LIVE_GAME_ID, "live": True, "queue_id": None,
        "game_mode": stats.get("gameMode"),
        "creation": int(time.time() * 1000),
        "duration_s": stats.get("gameTime"),
        "players": players, "me": me,
    }


def parse_match_detail(payload):
    """Normalize an LCU match-detail payload into a simple view model."""
    if not isinstance(payload, dict):
        return None
    identities = {}
    for entry in payload.get("participantIdentities") or []:
        if isinstance(entry, dict) and isinstance(entry.get("player"), dict):
            identities[entry.get("participantId")] = entry["player"]
    players = []
    for part in payload.get("participants") or []:
        if not isinstance(part, dict):
            continue
        stats = part.get("stats") or {}
        player = identities.get(part.get("participantId"), {})
        players.append({
            "participant_id": part.get("participantId"),
            "name": _identity_name(player),
            "tag": _identity_tag(player),
            "account_id": player.get("currentAccountId", player.get("accountId")),
            "puuid": player.get("puuid"),
            "champion_id": part.get("championId"),
            "subteam_id": stats.get("playerSubteamId")
                          if isinstance(stats.get("playerSubteamId"), int) else None,
            "placement": find_placement(part),
            "win": _win_flag(stats.get("win")),
            "kills": _stats_int(stats, "kills", default=0),
            "deaths": _stats_int(stats, "deaths", default=0),
            "assists": _stats_int(stats, "assists", default=0),
            "damage": _stats_int(stats, "totalDamageDealtToChampions"),
            "gold": _stats_int(stats, "goldEarned"),
            "heal": _stats_int(stats, "totalHeal"),
            "taken": _stats_int(stats, "totalDamageTaken"),
            "level": _stats_int(stats, "champLevel"),
            "items": participant_items(stats),
            "augments": collect_augments(part),
        })
    return {
        "game_id": payload.get("gameId"),
        "queue_id": payload.get("queueId"),
        "game_mode": payload.get("gameMode"),
        "creation": payload.get("gameCreation"),
        "duration_s": payload.get("gameDuration"),
        "players": players,
        "me": players[0] if players else {},
    }


# -----------------------------------------------------------------------------
# Match export - one plain model feeds the clipboard text, CSV and JSON.
# -----------------------------------------------------------------------------

def _format_duration(seconds):
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return ""
    return f"{int(seconds // 60)}m {int(seconds % 60):02d}s"


def _thousands(value):
    return f"{value:,}".replace(",", " ") if isinstance(value, int) else "-"
