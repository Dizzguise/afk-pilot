AFK Pilot 2.1.1 fixes hotkey assignment with Num Lock enabled and improves input
cleanup, settings recovery, and release validation.

Download the Windows x64 ZIP, verify its SHA-256 against the companion checksum,
extract it, and run AFKPilot.exe. Python is not required. Close any older copy
before starting. Existing settings are retained in %LOCALAPPDATA%\AFKPilot.
Reassign a shortcut captured incorrectly in an older version once after updating.

The default CI build is unsigned. This draft must be reviewed before publication;
see PUBLISHING.md for signing, manual acceptance checks, and rollback.

Background input support depends on the selected application. Follow application
and server rules when using automation.
