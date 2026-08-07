# AFK Pilot

AFK Pilot is a lightweight Windows utility for sending configurable walking,
sprint, jump, eating, and clicking input to a selected application window.

It was built for Minecraft-style AFK tasks, but window targeting is generic and
can be used with other Windows applications that accept standard window messages.

## Download

The ready-to-run Windows package is available here:

- [AFK Pilot 2.1.0.zip](release/AFK%20Pilot%202.1.0.zip)

Extract the ZIP and run `AFKPilot.exe`. The executable is unsigned, so Windows
may display a SmartScreen warning.

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
- Configurable global toggle and emergency-stop hotkeys.
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
py -3.11 AFKPilot.py --self-test
py -3.11 AFKPilot.py --gui-self-test
```

## Build the executable

```powershell
py -3.11 -m pip install -r requirements-build.txt
.\build.ps1
```

The packaged executable is written to `dist\AFKPilot.exe`.

## Settings

User settings are stored at:

```text
%LOCALAPPDATA%\AFKPilot\settings.json
```

AFK Pilot imports settings from the previous AutoWalk Pro name when present.
