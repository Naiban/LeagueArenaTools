# Changelog

## 1.02

- Added a Match History tab listing the last 50 games.
- Match details show items, augments, duo placements and KDA for every player.
- Item and augment icons resolve through CommunityDragon with hover tooltips.
- Every player's full build is one click away inside the lobby table.
- Lobby ratings (ELO and rank) come from the public arenasweats.lol API using
  an in-app cache; the region is detected from the connected League client.
- Loading progress is reported while ratings are fetched.

## 1.01

- Added automatic update checks through GitHub Releases.
- Added verified in-app EXE downloads and restart-based installation.
- Required GitHub-provided SHA-256 digests before installing an update.

## 1.00

- Renamed the application to LeagueArenaTools.
- Added multiple named configuration profiles with legacy-config migration.
- Added a custom League status editor and LCU save verification.
- Added automatic ready check, ban, pick, hover, re-queue, lobby messaging,
  phone notifications, and notification-area controls.
- Added Arena Bravery selection through `championId=-3`.
