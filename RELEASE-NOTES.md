AFK Pilot is a free, MIT-licensed Windows AFK button, mainly for Minecraft-style
tasks and other games or applications that accept standard Windows input.

2.1.1 fixes hotkey assignment with Num Lock enabled and improves input cleanup,
settings recovery, and release validation. A single key works without adding Ctrl
or Alt; intentional Ctrl/Alt/Shift combinations remain supported.

Download the Windows x64 ZIP, verify its SHA-256 against the companion checksum,
extract it, and run AFKPilot.exe. Python is not required. Close any older copy
before starting. Existing settings are retained in %LOCALAPPDATA%\AFKPilot.
Reassign a shortcut captured incorrectly in an older version once after updating.

This build is unsigned and Windows may show an unknown-publisher or SmartScreen
prompt. It is built from the public source by GitHub Actions. Verify the download:

```powershell
gh attestation verify .\AFK-Pilot-2.1.1-windows-x64.zip --repo Dizzguise/afk-pilot
```

The package includes the MIT license, third-party notices, checksums, and source
revision. There are no accounts, subscriptions, ads, or telemetry. Source code,
changes, and support are available in this repository and its Issues tab.

Background input support depends on the selected application. Follow application
and server rules when using automation.
