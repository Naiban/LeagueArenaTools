# LeagueArenaTools distribution

## Package

Distribute the single `dist/LeagueArenaTools.exe` file. The executable is built
from `LeagueArenaTools.py` with `LeagueArenaTools.spec` and does not use UPX.

The executable is unsigned, so Windows SmartScreen or an antivirus may show a
warning until the file is digitally signed or reputation is established.

## Source verification

The application stores multiple named profiles in
`league_arena_tools_config.json`. Existing `shinkansen_config.json` settings
are imported automatically on first launch.

The application connects only to the local League Client API and, when
enabled, `ntfy.sh` for push notifications. It does not run a shell, WMIC,
PowerShell, or a downloaded executable.

For the strongest reputation and lowest false-positive rate, sign the EXE
with a genuine Authenticode code-signing certificate before distribution.
