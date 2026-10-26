"""Match export - one plain model feeds the clipboard text, CSV and JSON."""

import io
import csv
from datetime import datetime

from lat.history.data import _format_duration, order_players_by_placement, QUEUE_LABELS, _thousands


def build_match_export(view, champion_label, resolve_name, ratings=None):
    """Plain, JSON-safe description of a parsed match.

    champion_label(id) -> name, resolve_name(kind, id) -> name ("item" or
    "augment"), ratings: participant id -> {"rating", "rank"} | None."""
    ratings = ratings or {}
    me_id = (view.get("me") or {}).get("participant_id")
    creation = view.get("creation")
    players = []
    team_ordinal, previous_subteam = 0, None
    for player in order_players_by_placement(view.get("players") or []):
        subteam = player.get("subteam_id")
        if subteam is not None and subteam != previous_subteam:
            team_ordinal += 1
        previous_subteam = subteam
        rating = ratings.get(player.get("participant_id"))
        rating = rating if isinstance(rating, dict) else {}
        name, tag = player.get("name") or "", player.get("tag") or ""
        players.append({
            "team": team_ordinal if subteam is not None else None,
            "placement": player.get("placement"),
            "riot_id": f"{name}#{tag}" if tag else name,
            "champion": champion_label(player.get("champion_id") or 0),
            "is_me": player.get("participant_id") == me_id,
            "win": player.get("win"),
            "kills": player.get("kills", 0),
            "deaths": player.get("deaths", 0),
            "assists": player.get("assists", 0),
            "damage": player.get("damage"),
            "gold": player.get("gold"),
            "heal": player.get("heal"),
            "taken": player.get("taken"),
            "level": player.get("level"),
            "items": [resolve_name("item", i) for i in player.get("items") or []],
            "augments": [resolve_name("augment", a)
                         for a in player.get("augments") or []],
            "rating": rating.get("rating"),
            "rank": rating.get("rank"),
        })
    return {
        "game_id": view.get("game_id"),
        "queue": QUEUE_LABELS.get(view.get("queue_id"), ""),
        "queue_id": view.get("queue_id"),
        "date": (datetime.fromtimestamp(creation / 1000).strftime("%Y-%m-%d %H:%M")
                 if isinstance(creation, (int, float)) and creation > 0 else ""),
        "duration": _format_duration(view.get("duration_s")),
        "duration_s": view.get("duration_s"),
        "players": players,
    }


def format_match_text(export):
    """Readable plain-text summary for the clipboard or a .txt file."""
    header = " · ".join(bit for bit in (
        export.get("queue") or "Match", export.get("date"), export.get("duration"))
        if bit)
    lines = [header, f"Game ID: {export.get('game_id')}"]
    me = next((p for p in export["players"] if p["is_me"]), None)
    if me:
        place = f"#{me['placement']}" if me.get("placement") else "-"
        lines.append(f"Me: {me['champion']} {place}  "
                     f"{me['kills']}/{me['deaths']}/{me['assists']}  "
                     f"{_thousands(me.get('damage'))} dmg")
    current_team = object()
    for player in export["players"]:
        if player["team"] != current_team:
            current_team = player["team"]
            place = f"#{player['placement']}" if player.get("placement") else "#-"
            label = f"TEAM {current_team}" if current_team is not None else "PLAYERS"
            lines += ["", f"{place}  {label}"]
        rating = ""
        if player.get("rating") is not None:
            rating = f"  ELO {player['rating']}"
            if isinstance(player.get("rank"), int):
                rating += f" (#{player['rank']})"
        marker = " (me)" if player["is_me"] else ""
        lines.append(f"  {player['riot_id'] or '-'}{marker} - {player['champion']}  "
                     f"{player['kills']}/{player['deaths']}/{player['assists']}  "
                     f"{_thousands(player.get('damage'))} dmg{rating}")
        if player["augments"]:
            lines.append("    Augments: " + ", ".join(player["augments"]))
        if player["items"]:
            lines.append("    Items: " + ", ".join(player["items"]))
    return "\n".join(lines) + "\n"


MATCH_CSV_COLUMNS = (
    "game_id", "date", "queue", "team", "placement", "riot_id", "champion",
    "is_me", "kills", "deaths", "assists", "damage", "gold", "heal", "taken",
    "level", "rating", "rank", "augments", "items",
)


def format_match_csv(export):
    """One row per player; list columns are joined with ' | '."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(MATCH_CSV_COLUMNS)
    for player in export["players"]:
        row = dict(player, game_id=export.get("game_id"), date=export.get("date"),
                   queue=export.get("queue"))
        writer.writerow([
            " | ".join(row[c]) if isinstance(row.get(c), list)
            else ("" if row.get(c) is None else row[c])
            for c in MATCH_CSV_COLUMNS
        ])
    return buffer.getvalue()
