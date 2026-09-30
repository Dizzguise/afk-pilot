# AFK Pilot

AFK Pilot is a lightweight Windows utility for sending configurable walking,
sprint, jump, eating, and clicking input to a selected application window.

It was built for Minecraft-style AFK tasks, but window targeting is generic and
can be used with other Windows applications that accept standard window messages.

## Release candidate 2.1.1

2.1.1 fixes hotkey assignment with Num Lock enabled and adds Windows regression
coverage, safer input cleanup, and verified release packaging.

- [Windows builds and test results](https://github.com/Dizzguise/afk-pilot/actions/workflows/windows.yml)
- [Publishing, signing, updates, and rollback](PUBLISHING.md)
- [Changes](CHANGELOG.md)
- [GitHub Releases](https://github.com/Dizzguise/afk-pilot/releases)

Download the artifact for the desired successful commit, extract the inner ZIP,
and run `AFKPilot.exe`. Python is not required. CI builds are unsigned; releases
remain drafts until reviewed. The legacy [2.1.0 package](release/AFK%20Pilot%202.1.0.zip)
is retained only for rollback.

## Features

- Hold W for automatic walking.
- Optional sprint using a delayed Left Ctrl pulse.
- Optional jump by holding Space.
- Optional auto-eat by holding right-click.
- Left- or right-button auto-clicking from 0.1 to 100 CPS.
- Click and hold modes.
- Target a selected window without clicking the window you are actively using.
- Pause automatically when the selected target loses focus.
- Optional start delay and run timer.
- Configurable global toggle plus a dedicated emergency-stop hotkey.
- Automatic release of held input when stopped, paused, finished, or closed.

## Important limitation

Background targeting uses Windows messages. Some games and applications ignore
these messages while unfocused. AFK Pilot can confirm that Windows accepted a
message, but it cannot guarantee that the target application consumed it.

Test automation somewhere safe before leaving it unattended. Keep the emergency
stop hotkey available, and do not use the tool where automation violates the
application or server rules.

## Run from source

AFK Pilot requires Windows 10 or 11 and Python 3.11 or newer. Runtime code uses
only the Python standard library.

```powershell
py -3.11 AFKPilot.py
```

Built-in validation:

```powershell
py -3.12 -m unittest discover -s tests -v
py -3.12 AFKPilot.py --self-test
py -3.12 AFKPilot.py --gui-self-test
```

## Build the executable

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\build.ps1 -Python .\.venv\Scripts\python.exe
```

CI uses Python 3.12.10 x64; local builds also support later 3.12 patches. The build runs tests, creates the
versioned executable, smoke-tests it, and writes the portable ZIP and SHA-256
checksums under `dist/`. See [PUBLISHING.md](PUBLISHING.md) for the complete path.

## Settings

User settings are stored at:

```text
%LOCALAPPDATA%\AFKPilot\settings.json
```

AFK Pilot imports settings from the previous AutoWalk Pro name when present.

## Hotkeys and troubleshooting

Click the toggle shortcut, then press and release a key. A single key works;
Ctrl, Alt, and Shift are optional. Escape, Cancel, or leaving the capture dialog
keeps the previous key. Num Lock and Caps Lock do not become shortcut modifiers.
Reassign a shortcut once after updating if 2.1.0 saved an unintended Alt modifier.
W/Space are reserved for automation and F12 is reserved by Windows. If another
app owns your chosen key, the previous working binding is restored. Startup
alternatives are displayed and use plain function keys. A second AFK Pilot copy
will not take over your shortcuts.

Settings are written atomically. Malformed settings fall back to defaults, and
unexpected UI errors stop automation. Local rotating diagnostics are saved beside
settings as `afkpilot.log`; there is no telemetry or automatic update service.
No software can guarantee input release after forced termination, power loss,
or OS input rejection; use the emergency stop and verify the target before
unattended use.
