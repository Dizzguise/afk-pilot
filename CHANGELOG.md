# Changelog

## 2.1.1

- Release as a free MIT-licensed tool with GitHub build attestations and clear
  download verification instructions.
- Size the initial window to its controls so labels and delay units are visible.

- Fix Num Lock being interpreted as Alt when assigning a hotkey on Windows.
- Capture plain keys and Ctrl/Alt/Shift combinations in a focused dialog; save on
  key release. Escape, Cancel, and leaving the dialog preserve the previous key.
- Keep the previous shortcut when a new one conflicts. Fail closed if emergency
  hotkeys are unavailable. Startup alternatives use plain function keys.
- Reject reserved F12 and automation keys; rebuild saved labels from the actual
  binding. Prevent multiple app instances from competing for shortcuts.
- Schedule sprint pulses without blocking the UI and track held Ctrl and mouse
  buttons for cleanup after a stop or input error.
- Recover from malformed settings, save atomically, and record diagnostic errors.
- Add Windows regression tests, executable smoke tests, version metadata, pinned
  build dependencies, checksummed release packages, and a draft release workflow.

## 2.1.0

Initial repository release, including window targeting, walking, sprint, jump,
auto-eat, click/hold, focus handling, and timers.
