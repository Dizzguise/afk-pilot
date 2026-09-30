# Publishing AFK Pilot

## Release state

2.1.1 is the release candidate for the hotkey fix. CI builds an **unsigned Windows
x64 portable ZIP**. A green build verifies tests, source GUI startup, packaged
startup, and package integrity. It does not certify compatibility with every game
or replace interactive testing on a clean Windows machine.

The first reconciliation found source and readme identical to GitHub commit
`6a83f8a5755b647b67a453e17c3b910050a3d58e` after line-ending normalization. Both
2.1.0 ZIPs had SHA-256
`3F805FA8B9587E31ACA7FAA74556BA1664831EED4DA0722131C422BF1CDF8CB2`.

## Build locally

Use 64-bit Windows and Python 3.12.10 (the version pinned in CI). From the repo:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\build.ps1 -Python .\.venv\Scripts\python.exe
```

Outputs: `dist/AFKPilot.exe`, `dist/AFK-Pilot-2.1.1-windows-x64.zip`, and its
`.zip.sha256` checksum. Each ZIP includes file hashes and `BUILD-INFO.json` with
the source commit, dirty-worktree flag, source hash, binary hash, and tool versions.
Publish only from a clean, committed checkout. Rebuild after changing any source.
Never distribute a stale executable beside newer Python source.

## Validate before publication

1. Download the artifact for the exact successful GitHub commit under
   [Actions](https://github.com/Dizzguise/afk-pilot/actions/workflows/windows.yml).
2. Extract on a clean Windows 10/11 x64 test machine with no Python installed.
   Check initial startup, restart/settings persistence, and a second launch.
3. Assign a single key with Num Lock on/off and Caps Lock on/off. Test Ctrl, Alt,
   Shift combinations; Escape/cancel, window close, focus loss, and a key owned by
   another app. The displayed chord must match what activates automation.
4. Against a disposable target, verify walking, sprint, jump, eat, both click
   buttons and hold mode; confirm stop during the sprint warmup/hold, F7, timer,
   target closure, focus pause/resume, target change, and app exit release input.
   Test the actual supported target application in foreground and background modes.
5. Verify SHA-256 using `Get-FileHash -Algorithm SHA256 <zip-path>` and inspect
   `Get-AuthenticodeSignature <exe-path>` if distributing a signed build.

## Signing and broad distribution

Use a code-signing certificate or a managed signing service owned by the publisher.
The build accepts a certificate thumbprint from the Windows certificate store:

```powershell
.\build.ps1 -Python .\.venv\Scripts\python.exe -CertificateThumbprint '<thumbprint>' -SignTool '<Windows SDK path>\signtool.exe'
```

Signing and verification happen before packaging/checksums. Do not commit private
keys or passwords. For hardware/cloud-backed keys, use the provider's supported
signing integration, then run the packaged smoke checks and regenerate the ZIP.
The stock GitHub workflow intentionally has no signing credentials. A valid
signature identifies the publisher; it is not a promise that SmartScreen will
never prompt. See Microsoft's [SignTool reference](https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool).

Choose AFK Pilot's distribution license/terms and a support channel before a broad
launch. Third-party runtime license texts are already bundled. For initial private
testing, the unsigned ZIP can be shared with testers who understand its provenance.

## Create a draft, then publish

After the exact main commit passes the workflow and manual checks:

```powershell
git status --short
git pull --ff-only origin main
git tag -a v2.1.1 -m 'AFK Pilot 2.1.1'
git push origin v2.1.1
```

The tag workflow verifies the version, rebuilds/tests, and creates a **draft**
GitHub Release with the ZIP and checksum. Review notes and asset hashes. If using
signing, replace both unsigned assets with the verified signed ZIP and its checksum
before publication. Publish from GitHub's release page, or explicitly run:

```powershell
gh release edit v2.1.1 --draft=false --repo Dizzguise/afk-pilot
```

Use GitHub Releases for binaries; leave the old `release/2.1.0` package as history.
Future updates require bumping `APP_VERSION`, changelog, and release notes, then
tagging the matching version. CI never publishes a release automatically.

## Updates and rollback

Close AFK Pilot, back up `%LOCALAPPDATA%\AFKPilot\settings.json`, extract the new
ZIP into a new folder, then run its executable. Settings live outside the install
folder and migrate from the older AutoWalk Pro/MCAFKPilot names. No administrator
install or auto-updater is needed. If rollback is necessary, close the new build
and run the retained previous ZIP; restore the settings backup if needed. Do not
overwrite a version tag or replace a published release silently.

## Keeping the folder and Git aligned

Use a real clone as the project folder, not a directory of loose release files:

```powershell
git clone https://github.com/Dizzguise/afk-pilot.git '<new-writable-folder>'
git -C '<new-writable-folder>' status --short --branch
git -C '<new-writable-folder>' rev-parse HEAD
gh api repos/Dizzguise/afk-pilot/commits/main --jq .sha
```

The two SHAs should agree and tracked files should be clean. Build/download the
artifact for that SHA. Preserve the original folder before migration. A drive
reported by Windows as write-protected must be made writable or replaced with a
writable destination before any synchronization can succeed.
