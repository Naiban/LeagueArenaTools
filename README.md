# LeagueArenaTools

<p align="center">
  <img src="league_arena_tools.png" alt="LeagueArenaTools logo" width="128">
</p>

LeagueArenaTools is a Windows desktop utility for automating common League of
Legends client actions through the local League Client API (LCU).

## Features

- Automatic ready-check acceptance with configurable timing
- Ordered champion ban and pick priorities
- Normal and instant champion selection
- Arena Bravery selection using `championId=-3`
- Automatic champion hover
- Automatic matchmaking restart after joining a lobby
- Champ-select chat messages
- Custom League status editor without an application-side character limit
- Multiple named configuration profiles
- Optional ntfy phone notifications
- Notification-area operation
- Automatic update checks with SHA-256-verified installation

Profile creation, activation, and deletion are disabled while the bot is
running. Stop the bot before switching configurations.

## Download

Download `LeagueArenaTools.exe` from the
[latest GitHub release](https://github.com/Naiban/LeagueArenaTools/releases/latest).
The executable is currently unsigned, so Windows SmartScreen or antivirus
software may show a warning until the project establishes reputation or the
binary is digitally signed.

Automatic update checks use the public GitHub Releases API and do not contain
or request a GitHub access token. They therefore become available when this
repository is public. While it is private, the application reports that the
update service is unavailable. Automatic installation is supported by the
packaged EXE; source runs can check releases but do not replace source files.

## Run from source

Requirements:

- Windows 10 or later
- Python 3.10 or later
- The League of Legends client

```powershell
python -m pip install -r requirements.txt
python LeagueArenaTools.py
```

## Build the executable

Install PyInstaller and build from the checked-in specification:

```powershell
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean LeagueArenaTools.spec
```

The executable is written to `dist/LeagueArenaTools.exe`.

## Configuration

Settings are stored next to the source file or executable in
`league_arena_tools_config.json`. The file supports multiple named profiles and
is excluded from Git because it contains personal preferences and notification
topics.

## Network access

LeagueArenaTools communicates with:

- the local League Client API on `127.0.0.1`;
- the local Live Client API on `127.0.0.1`;
- `ntfy.sh` only when phone notifications are enabled.
- `api.github.com` and GitHub release download hosts for update checks and
  SHA-256-verified updates.

It only downloads executable updates after confirmation, and installs them
when their size and GitHub-provided SHA-256 digest match the release metadata.

## Disclaimer

LeagueArenaTools is an independent, unofficial project. It is not endorsed by
Riot Games. League of Legends and Riot Games are trademarks or registered
trademarks of Riot Games, Inc.

## License

LeagueArenaTools is released under the [MIT License](LICENSE).
