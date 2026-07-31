# LeagueArenaTools LCU

<p align="center">
  <img src="league_arena_tools.png" alt="LeagueArenaTools logo" width="128">
</p>

LeagueArenaTools is a tool that connects to League of
Legends client actions through the local League Client API (LCU).

<img width="1076" height="757" alt="obraz" src="https://github.com/user-attachments/assets/f4be21c3-c540-4223-ac55-7135ee914a39" />
<img width="1077" height="751" alt="obraz" src="https://github.com/user-attachments/assets/c5ed3176-30c2-45b3-95d4-4a48a03a8ff5" />

## Warning
- App is fully optimized for Arena Gamemode and not tested in other gamemodes
- LCU is forbidden to use at korean server

## Features
- 👦🏾Auto Accept 
- 👩🏿‍🦯Instant Hover&Pick&Ban (Bravery Pick Option)
- 🛀🏿Automatic champion hover
- 👍Auto Re-Queue
- 👨‍✈️Champ-Select Message
- 🦹‍♂️League Status Editor
- 💬lobby Message
- 👩‍🦽Ntfy phone notifications

## Download

Download `LeagueArenaTools.exe` from the
[latest GitHub release](https://github.com/Naiban/LeagueArenaTools/releases/latest).

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

## Network access

LeagueArenaTools communicates with:

- via official lcu api client;
- `ntfy.sh` only when phone notifications are enabled.
- `api.github.com` and GitHub release download hosts for update checks and
  SHA-256-verified updates.

## ⚠️Disclaimer

LeagueArenaTools is Third-Party Tool. It is not endorsed by
Riot Games, use at your own risk

## License

LeagueArenaTools is released under the [MIT License](LICENSE).
