# Changelog

## 1.03

- Faster start-up: the window appears before the network, process and tray
  libraries load, and the EXE no longer bundles unused packages
  (cryptography, setuptools, chardet, Pillow AVIF/font/colour modules).
- The League connection check runs in the background, so the window never
  freezes while the client is slow or starting.
- A single slow client answer (common while a game loads) no longer stops
  the bot. If the client really disconnects, the bot pauses and resumes
  automatically once the client is back.
- Champ select runs on its own worker: long confirm delays no longer delay
  ready checks or phase changes, and a dodge cancels a pending lock-in.
- The websocket reconnects with back-off, logs an outage once instead of
  repeatedly, and pings idle connections to detect a frozen client.
- Settings changed while the bot runs now take effect on the spot: turning
  off Auto Accept or Auto Pick cancels a pending accept or lock-in, the
  confirm delay and pick mode adjust a running timer, and enabling Auto
  Accept, Auto Re-queue, Hover or the champ select message acts on the
  current ready check, lobby or champ select.
- New Live Settings status (dashboard tile plus a status line on every
  page) shows whether each change is active now, waits for the next ready
  check or champ select, or cannot work as configured (for example an empty
  message, a missing hover champion or an unknown champion name).
- The config file is saved atomically; an unreadable config is kept as a
  `.broken-<timestamp>` backup instead of being overwritten.
- The activity log is capped so long sessions stay responsive.
- Match History: LAN and LAS ratings were looked up in each other's region;
  SEA, TW and VN servers are now detected too. Queue 1090 is labelled TFT
  (it was shown as Ranked Arena), and more queues have names.
- Match History: item/augment names, lobby ratings, icons and your own
  player identity are retried after a network error instead of staying
  blank until restart.
- Match History: icons download on a small shared pool, are fetched once
  when several players own the same item, and are cached on disk.
- Match History: new Arena-only filter; ratings are only looked up for Arena
  games; a slow answer for a previously clicked game no longer replaces the
  game you are viewing.
- Match History: Copy and Export buttons save a match as text, CSV or JSON.
- Match History: the lobby table fits the window (ELO and rank were cut off)
  and full builds wrap onto a second row.
- Updates: if the EXE stays locked (e.g. by antivirus), the previous version
  restarts and reports the failed update; the installer now really waits
  between retries and uses Windows' own system tools.
- Background work no longer calls into the UI from worker threads, which
  could fail during start-up and shutdown.
- Auto Accept: with an accept delay, a ready check you declined yourself
  could still be accepted by the bot; a declined, already answered or closed
  ready check is now left alone.
- Auto Ban/Pick: a priority champion you do not own (or that is disabled)
  is skipped instead of failing on it at every champ select update.
- Hover: the champion is hovered once per champ select instead of on every
  update, so it no longer overrides a champion you hover yourself.
- Starting the bot during a game, or a client reconnect, no longer counts an
  extra game or sends a late "game loaded" notification.
- Notification topics with `/`, `?` or `#` no longer change the address the
  message is sent to.
- The tray menu shows the right check marks after toggling from it, and no
  longer reads UI state from the tray thread.
- The window remembers its size, position and maximized state (an
  off-screen position, e.g. from an unplugged monitor, is not restored).
- Match History: item/augment names are cached on disk (refreshed after
  12 hours or when a game shows a new patch's ids) instead of downloading
  about 1.3 MB every session.
- Match History: the date column is visible again, placements are coloured,
  and Refresh keeps older games already loaded.
- Match History: "Load older games" pages back through up to 500 games.
- Match History: new Arena overview (Stats button) with average placement,
  top-half rate, first places, KDA, damage, recent form, and results per
  champion and augment.
- Match History: during a game, a LIVE entry lists everyone in it with
  their Arena rating (Live Client Data API), and the history refreshes by
  itself once the game is over.
- Match History: hovering a rating shows the player's league, ladder
  position, games this season and best champions.
- New phone notification "Game finished" with your placement, champion, KDA,
  damage and game length.
- Code: the single 7,000-line source file is now the `lat` package (one
  module per area, the main window split into one part per page/feature),
  so features can be added in one place. Code was moved, not changed:
  every function compiles to the same bytecode as before. New
  `test_structure.py` guards the layout.

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
