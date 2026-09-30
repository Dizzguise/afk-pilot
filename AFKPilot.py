"""AFK Pilot - a dependency-free Windows automation utility."""

from __future__ import annotations

import atexit
import ctypes
from ctypes import wintypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import sys
import threading
import tempfile
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any


APP_NAME = "AFK Pilot"
APP_VERSION = "2.1.1"
LOGGER = logging.getLogger("afkpilot")

# Give the target time to observe W before sending a distinct sprint-key pulse.
SPRINT_WARMUP_SECONDS = 0.20
SPRINT_HOLD_SECONDS = 0.15
BACKGROUND_REASSERT_DELAY_SECONDS = 0.75

VK_W = 0x57
VK_SPACE = 0x20
VK_LCONTROL = 0xA2
VK_F7 = 0x76

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002


def read_settings(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def write_settings(path: Path, data: dict[str, Any]) -> None:
    """Replace settings atomically, keeping the previous file on write failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds + 0.999))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def keyboard_lparam(scan_code: int, key_up: bool) -> int:
    value = 1 | (scan_code << 16)
    if key_up:
        value |= 0xC0000000
    return value


def focus_reassert_delay(
    previous_focused: bool | None,
    currently_focused: bool,
    target_mode: bool,
) -> float | None:
    """Return the one-shot delay required for a target-window focus transition."""
    if not target_mode or previous_focused is None or previous_focused == currently_focused:
        return None
    return 0.0 if currently_focused else BACKGROUND_REASSERT_DELAY_SECONDS


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", wintypes.DWORD), ("data", _INPUTUNION)]


class KeyController:
    """Sends foreground or window-targeted input and guarantees cleanup."""

    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_SCANCODE = 0x0008
    INPUT_KEYBOARD = 1
    INPUT_MOUSE = 0
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010

    def __init__(self) -> None:
        self._held: list[int] = []
        self._mouse_held: list[str] = []
        self._target_hwnd: int | None = None
        self._sprint_enabled = False
        self._clock = time.monotonic
        self._sprint_at: float | None = None
        self._sprint_release_at: float | None = None
        self._lock = threading.Lock()
        self._user32 = ctypes.WinDLL("user32", use_last_error=True) if os.name == "nt" else None
        if self._user32 is not None:
            self._user32.SendInput.argtypes = [
                wintypes.UINT,
                ctypes.POINTER(_INPUT),
                ctypes.c_int,
            ]
            self._user32.SendInput.restype = wintypes.UINT
            self._user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
            self._user32.MapVirtualKeyW.restype = wintypes.UINT
            self._user32.PostMessageW.argtypes = [
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            ]
            self._user32.PostMessageW.restype = wintypes.BOOL
            self._user32.IsWindow.argtypes = [wintypes.HWND]
            self._user32.IsWindow.restype = wintypes.BOOL
            self._user32.GetClientRect.argtypes = [
                wintypes.HWND,
                ctypes.POINTER(wintypes.RECT),
            ]
            self._user32.GetClientRect.restype = wintypes.BOOL
            self._user32.GetForegroundWindow.argtypes = []
            self._user32.GetForegroundWindow.restype = wintypes.HWND
            self._user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
            self._user32.GetWindowTextLengthW.restype = ctypes.c_int
            self._user32.GetWindowTextW.argtypes = [
                wintypes.HWND,
                wintypes.LPWSTR,
                ctypes.c_int,
            ]
            self._user32.GetWindowTextW.restype = ctypes.c_int

    def _send_foreground_key(self, vk: int, key_up: bool) -> None:
        if self._user32 is None:
            raise RuntimeError("AFK Pilot only supports Windows.")
        scan = self._user32.MapVirtualKeyW(vk, 0)
        flags = self.KEYEVENTF_SCANCODE | (self.KEYEVENTF_KEYUP if key_up else 0)
        event = _INPUT(
            type=self.INPUT_KEYBOARD,
            ki=_KEYBDINPUT(0, scan, flags, 0, 0),
        )
        sent = self._user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_INPUT))
        if sent != 1:
            raise ctypes.WinError(ctypes.get_last_error())

    def _post_key(self, hwnd: int, vk: int, key_up: bool) -> None:
        if not self._user32.IsWindow(hwnd):
            raise RuntimeError("The selected target window is no longer open.")
        scan = self._user32.MapVirtualKeyW(vk, 0)
        message = WM_KEYUP if key_up else WM_KEYDOWN
        if not self._user32.PostMessageW(hwnd, message, vk, keyboard_lparam(scan, key_up)):
            raise ctypes.WinError(ctypes.get_last_error())

    def _send_key(self, vk: int, key_up: bool, target_hwnd: int | None) -> None:
        if target_hwnd is None:
            self._send_foreground_key(vk, key_up)
        else:
            self._post_key(target_hwnd, vk, key_up)

    def _mouse_lparam(self, hwnd: int) -> int:
        rect = wintypes.RECT()
        if not self._user32.GetClientRect(hwnd, ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
        x = max(0, (rect.right - rect.left) // 2)
        y = max(0, (rect.bottom - rect.top) // 2)
        return (y << 16) | (x & 0xFFFF)

    def _send_mouse(self, button: str, down: bool, target_hwnd: int | None) -> None:
        if target_hwnd is None:
            if button == "left":
                flags = self.MOUSEEVENTF_LEFTDOWN if down else self.MOUSEEVENTF_LEFTUP
            else:
                flags = self.MOUSEEVENTF_RIGHTDOWN if down else self.MOUSEEVENTF_RIGHTUP
            event = _INPUT(
                type=self.INPUT_MOUSE,
                mi=_MOUSEINPUT(0, 0, 0, flags, 0, 0),
            )
            sent = self._user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_INPUT))
            if sent != 1:
                raise ctypes.WinError(ctypes.get_last_error())
            return

        if not self._user32.IsWindow(target_hwnd):
            raise RuntimeError("The selected target window is no longer open.")
        if button == "left":
            message = WM_LBUTTONDOWN if down else WM_LBUTTONUP
            wparam = MK_LBUTTON if down else 0
        else:
            message = WM_RBUTTONDOWN if down else WM_RBUTTONUP
            wparam = MK_RBUTTON if down else 0
        if not self._user32.PostMessageW(
            target_hwnd, message, wparam, self._mouse_lparam(target_hwnd)
        ):
            raise ctypes.WinError(ctypes.get_last_error())

    def apply_movement(
        self,
        walk: bool,
        sprint: bool,
        jump: bool,
        target_hwnd: int | None,
    ) -> None:
        desired = [VK_W] if walk else []
        if walk and jump:
            desired.append(VK_SPACE)

        with self._lock:
            if self._target_hwnd != target_hwnd and (self._held or self._mouse_held):
                self._release_all_locked()
            self._target_hwnd = target_hwnd
            w_was_held = VK_W in self._held
            for vk in reversed(self._held):
                if vk not in desired:
                    self._send_key(vk, True, self._target_hwnd)
                    self._held.remove(vk)
            for vk in desired:
                if vk not in self._held:
                    self._send_key(vk, False, self._target_hwnd)
                    self._held.append(vk)

            # The target needs to observe W first, then a distinct Ctrl pulse.
            if walk and sprint and (not w_was_held or not self._sprint_enabled):
                self._pulse_sprint(self._target_hwnd)
            self._sprint_enabled = walk and sprint

    def _pulse_sprint(self, target_hwnd: int | None) -> None:
        if VK_LCONTROL in self._held:
            self._send_key(VK_LCONTROL, True, target_hwnd)
            self._held.remove(VK_LCONTROL)
        self._sprint_release_at = None
        self._sprint_at = self._clock() + SPRINT_WARMUP_SECONDS

    def advance_sprint(self) -> None:
        """Advance the pulse without blocking the UI or emergency-stop handling."""
        with self._lock:
            now = self._clock()
            if self._sprint_at is not None and now >= self._sprint_at:
                self._send_key(VK_LCONTROL, False, self._target_hwnd)
                self._held.append(VK_LCONTROL)
                self._sprint_at = None
                self._sprint_release_at = now + SPRINT_HOLD_SECONDS
            if self._sprint_release_at is not None and now >= self._sprint_release_at:
                self._send_key(VK_LCONTROL, True, self._target_hwnd)
                self._held.remove(VK_LCONTROL)
                self._sprint_release_at = None

    def reassert_background(self, sprint: bool, target_hwnd: int) -> None:
        """Reapply held input once after the target finishes its focus-loss reset."""
        with self._lock:
            if self._target_hwnd != target_hwnd:
                return
            for vk in self._held:
                self._send_key(vk, False, target_hwnd)
            for button in self._mouse_held:
                self._send_mouse(button, True, target_hwnd)
            if sprint and VK_W in self._held:
                self._pulse_sprint(target_hwnd)

    def click(self, button: str, target_hwnd: int | None) -> None:
        with self._lock:
            if self._target_hwnd != target_hwnd and (self._held or self._mouse_held):
                self._release_all_locked()
            self._target_hwnd = target_hwnd
            if button in self._mouse_held:
                return
            self._send_mouse(button, True, target_hwnd)
            self._mouse_held.append(button)
            self._send_mouse(button, False, target_hwnd)
            self._mouse_held.remove(button)

    def hold_mouse(self, button: str, target_hwnd: int | None) -> None:
        with self._lock:
            if self._target_hwnd != target_hwnd and (self._held or self._mouse_held):
                self._release_all_locked()
            self._target_hwnd = target_hwnd
            if button not in self._mouse_held:
                self._send_mouse(button, True, target_hwnd)
                self._mouse_held.append(button)

    def release_mouse(self, button: str) -> None:
        with self._lock:
            if button in self._mouse_held:
                self._send_mouse(button, False, self._target_hwnd)
                self._mouse_held.remove(button)

    def _release_all_locked(self) -> None:
        self._sprint_at = None
        self._sprint_release_at = None
        for button in reversed(self._mouse_held):
            try:
                self._send_mouse(button, False, self._target_hwnd)
            except Exception:
                LOGGER.exception("Could not release mouse button %s", button)
        self._mouse_held.clear()
        for vk in reversed(self._held):
            try:
                self._send_key(vk, True, self._target_hwnd)
            except Exception:
                LOGGER.exception("Could not release key %s", vk)
        self._held.clear()
        self._sprint_enabled = False

    def release_all(self) -> None:
        with self._lock:
            self._release_all_locked()
            self._target_hwnd = None


class HotkeyListener:
    """Own registrations on one thread, with deterministic cancellation/cleanup."""

    def __init__(self, events: queue.Queue[str]) -> None:
        self.events = events
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._user32 = ctypes.WinDLL("user32", use_last_error=True) if os.name == "nt" else None
        self.last_error = ""
        if self._user32 is not None:
            self._user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            self._user32.RegisterHotKey.restype = wintypes.BOOL
            self._user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            self._user32.UnregisterHotKey.restype = wintypes.BOOL
            self._user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
            self._user32.PeekMessageW.restype = wintypes.BOOL

    def start(self, modifiers: int, vk: int, stop_modifiers: int = 0, stop_vk: int = VK_F7) -> bool:
        self.stop()
        self.last_error = ""
        if self._user32 is None:
            self.last_error = "Global hotkeys require Windows."
            return False
        if self._thread is not None:
            self.last_error = "The previous hotkey listener has not stopped."
            return False
        if (modifiers, vk) == (stop_modifiers, stop_vk):
            self.last_error = "Toggle and emergency stop must be different."
            return False

        ready = threading.Event()
        stopped = threading.Event()
        self._stop_event = stopped
        result = {"ok": False}

        def run() -> None:
            registered: list[int] = []
            try:
                for identifier, mods, key, label in (
                    (1, modifiers, vk, "Toggle"), (2, stop_modifiers, stop_vk, "Emergency stop")
                ):
                    if not self._user32.RegisterHotKey(None, identifier, mods | MOD_NOREPEAT, key):
                        self.last_error = f"{label} registration failed (Windows error {ctypes.get_last_error()})."
                        return
                    registered.append(identifier)
                result["ok"] = True
                ready.set()
                msg = wintypes.MSG()
                while not stopped.is_set():
                    while self._user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                        if stopped.is_set():
                            break
                        if msg.message == WM_HOTKEY and msg.wParam in (1, 2):
                            self.events.put("toggle" if msg.wParam == 1 else "stop")
                    stopped.wait(0.01)
            except Exception:
                LOGGER.exception("Global hotkey listener failed")
                self.last_error = "Global hotkey listener failed; see the diagnostic log."
                if result["ok"]:
                    self.events.put("hotkeys_failed")
                result["ok"] = False
            finally:
                for identifier in registered:
                    self._user32.UnregisterHotKey(None, identifier)
                ready.set()

        self._thread = threading.Thread(target=run, name="global-hotkeys", daemon=True)
        self._thread.start()
        if not ready.wait(2.0):
            self.last_error = "Global hotkey registration timed out."
            self.stop()
            return False
        if not result["ok"]:
            self.stop()
        return result["ok"]

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                LOGGER.error("Global hotkey thread did not stop")
                return
        self._thread = None


SPECIAL_KEYS = {
    **{f"F{i}": 0x6F + i for i in range(1, 25)},
    "Insert": 0x2D, "Delete": 0x2E, "Home": 0x24, "End": 0x23,
    "Prior": 0x21, "Next": 0x22, "Pause": 0x13, "Scroll_Lock": 0x91,
}
KEY_NAMES = {
    vk: {"Prior": "PAGE UP", "Next": "PAGE DOWN"}.get(name, name.replace("_", " ").upper())
    for name, vk in SPECIAL_KEYS.items()
}
KEY_NAMES.update({vk: chr(vk) for vk in (*range(0x30, 0x3A), *range(0x41, 0x5B))})
DEFAULT_HOTKEY = {"modifiers": 0, "vk": 0x75, "display": "F6"}


def make_hotkey(modifiers: int, vk: int) -> dict[str, Any]:
    if vk not in KEY_NAMES or modifiers & ~7:
        raise ValueError("Choose a letter, digit, navigation key, or function key, optionally with Ctrl, Alt, or Shift.")
    if vk == VK_W:
        raise ValueError("W is used for auto-walk. Choose a different key.")
    if vk == 0x7B:
        raise ValueError("Windows reserves F12 for the debugger. Choose a different key.")
    names = [name for mask, name in ((MOD_CONTROL, "CTRL"), (MOD_ALT, "ALT"), (MOD_SHIFT, "SHIFT")) if modifiers & mask]
    return {"modifiers": modifiers, "vk": vk, "display": " + ".join([*names, KEY_NAMES[vk]])}


def hotkey_from_event(event: tk.Event) -> dict[str, Any] | None:
    # Tk on Windows uses Mod1 (0x8) for NUM LOCK, not Alt. Alt uses
    # Mod2 (0x10), and native events can additionally carry AltMask (0x20000).
    # Keycodes preserve the physical digit when Shift changes its keysym to ! etc.
    vk = int(getattr(event, "keycode", 0))
    if vk not in KEY_NAMES:
        keysym = event.keysym
        if len(keysym) == 1 and keysym.isascii() and keysym.isalnum():
            vk = ord(keysym.upper())
        else:
            vk = SPECIAL_KEYS.get(keysym, 0)
    if not vk:
        return None
    modifiers = 0
    for mask, modifier in ((0x4, MOD_CONTROL), (0x20010, MOD_ALT), (0x1, MOD_SHIFT)):
        if event.state & mask:
            modifiers |= modifier
    return make_hotkey(modifiers, vk)


def list_target_windows(exclude_hwnd: int = 0) -> list[tuple[int, str]]:
    """Return visible titled top-level windows sorted by title."""
    if os.name != "nt":
        return []
    user32 = ctypes.windll.user32
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    windows: list[tuple[int, str]] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL

    def callback(hwnd: int, _lparam: int) -> bool:
        if int(hwnd) == exclude_hwnd or not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()
        if title and title not in {"Program Manager", "Default IME"}:
            windows.append((int(hwnd), title))
        return True

    user32.EnumWindows(callback_type(callback), 0)
    windows.sort(key=lambda item: item[1].lower())
    return windows


class AFKPilotApp:
    BG = "#10151c"
    CARD = "#19212b"
    INPUT = "#222d39"
    TEXT = "#f4f7fb"
    MUTED = "#94a4b7"
    GREEN = "#3ddc97"
    RED = "#ff647c"
    AMBER = "#ffca5c"
    BLUE = "#4da3ff"

    def __init__(self, root: tk.Tk, test_mode: bool = False) -> None:
        self.root = root
        self.test_mode = test_mode
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.geometry("540x760")
        self.root.minsize(540, 740)
        self.root.configure(bg=self.BG)

        self.events: queue.Queue[str] = queue.Queue()
        self.keys = KeyController()
        self.hotkeys = HotkeyListener(self.events)
        self.requested = False
        self.active = False
        self.start_at = 0.0
        self.remaining: float | None = None
        self.last_tick = time.monotonic()
        self.next_click_at = 0.0
        self.click_interval = 1.0
        self.target_hwnd: int | None = None
        self.input_target_hwnd: int | None = None
        self.background_reassert_at = 0.0
        self.background_reassert_pending = False
        self.last_target_focus: bool | None = None
        self.window_map: dict[str, int] = {}
        self.window_titles: dict[str, str] = {}
        self.capture_active = False
        self.capture_dialog: tk.Toplevel | None = None
        self.capture_candidate: dict[str, Any] | None = None
        self._capture_focus_after: str | None = None
        self._tick_after: str | None = None
        self.hotkeys_ready = False
        self.closed = False
        self.test_failure: BaseException | None = None
        self.fatal_error = False
        self._save_warning_shown = False
        self.config_path = self._config_path()
        self.config = self._load_config()
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(self.config_path.parent / "afkpilot.log", maxBytes=500_000, backupCount=2, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            LOGGER.addHandler(handler)
            self.log_handler: logging.Handler | None = handler
            LOGGER.setLevel(logging.INFO)
        except OSError:
            self.log_handler = None
        self.hotkey = self._load_hotkey()
        self.emergency_hotkey = {"modifiers": 0, "vk": VK_F7, "display": "F7"}

        self._build_style()
        self._build_ui()
        self.root.update_idletasks()
        width = max(540, self.root.winfo_reqwidth())
        height = max(760, self.root.winfo_reqheight())
        self.root.minsize(width, height)
        self.root.geometry(f"{width}x{height}")
        self._load_vars()
        self.refresh_windows()
        self._register_hotkey_or_warn()

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.report_callback_exception = self._callback_failed
        atexit.register(self.keys.release_all)
        self._tick_after = self.root.after(50, self._tick)

    def _config_path(self) -> Path:
        override = (
            os.environ.get("AFKPILOT_CONFIG_DIR")
            or os.environ.get("MCAFKPILOT_CONFIG_DIR")
            or os.environ.get("AUTOWALKPRO_CONFIG_DIR")
        )
        if override:
            return Path(override) / "settings.json"
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        current_path = base / "AFKPilot" / "settings.json"
        legacy_paths = (
            base / "MCAFKPilot" / "settings.json",
            base / "AutoWalkPro" / "settings.json",
        )
        if not current_path.exists():
            for legacy_path in legacy_paths:
                if not legacy_path.exists():
                    continue
                try:
                    current_path.parent.mkdir(parents=True, exist_ok=True)
                    current_path.write_bytes(legacy_path.read_bytes())
                except OSError:
                    return legacy_path
                break
        return current_path

    def _load_config(self) -> dict[str, Any]:
        return read_settings(self.config_path)

    def _load_hotkey(self) -> dict[str, Any]:
        data = self.config.get("hotkey", {})
        try:
            modifiers = int(data["modifiers"])
            vk = int(data["vk"])
            return make_hotkey(modifiers, vk)
        except (KeyError, TypeError, ValueError, OverflowError):
            return dict(DEFAULT_HOTKEY)

    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TCheckbutton", background=self.CARD, foreground=self.TEXT)
        style.map("TCheckbutton", background=[("active", self.CARD)])
        style.configure(
            "TButton", background=self.INPUT, foreground=self.TEXT, padding=(10, 7), borderwidth=0
        )
        style.map("TButton", background=[("active", "#2f3d4d"), ("pressed", "#16202b")])
        style.configure(
            "TCombobox",
            fieldbackground=self.INPUT,
            background=self.INPUT,
            foreground=self.TEXT,
            arrowcolor=self.TEXT,
            borderwidth=0,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", self.INPUT)],
            foreground=[("readonly", self.TEXT)],
        )
        style.configure(
            "Accent.TButton", background=self.BLUE, foreground="#07111d", padding=(10, 10), borderwidth=0
        )
        style.map("Accent.TButton", background=[("active", "#77baff"), ("pressed", "#328de8")])

    def _card(self, parent: tk.Widget, title: str) -> tk.Frame:
        card = tk.Frame(parent, bg=self.CARD, highlightthickness=1, highlightbackground="#263341")
        card.pack(fill="x", pady=(0, 10))
        tk.Label(
            card, text=title.upper(), bg=self.CARD, fg=self.MUTED, font=("Segoe UI", 9, "bold")
        ).pack(anchor="w", padx=14, pady=(11, 6))
        return card

    def _entry(self, parent: tk.Widget, variable: tk.StringVar, width: int) -> tk.Entry:
        return tk.Entry(
            parent,
            textvariable=variable,
            width=width,
            bg=self.INPUT,
            fg=self.TEXT,
            insertbackground=self.TEXT,
            relief="flat",
            justify="center",
            font=("Segoe UI", 10),
        )

    def _build_ui(self) -> None:
        body = tk.Frame(self.root, bg=self.BG)
        body.pack(fill="both", expand=True, padx=16, pady=14)

        header = tk.Frame(body, bg=self.BG)
        header.pack(fill="x", pady=(0, 12))
        tk.Label(
            header, text="AFK PILOT", bg=self.BG, fg=self.TEXT, font=("Segoe UI", 17, "bold")
        ).pack(side="left")
        self.status_label = tk.Label(
            header, text="STOPPED", bg=self.RED, fg="#16070a", font=("Segoe UI", 9, "bold"), padx=9, pady=4
        )
        self.status_label.pack(side="right")

        movement = self._card(body, "Movement")
        row = tk.Frame(movement, bg=self.CARD)
        row.pack(fill="x", padx=14, pady=(0, 5))
        self.walk_var = tk.BooleanVar()
        self.sprint_var = tk.BooleanVar()
        self.jump_var = tk.BooleanVar()
        self.eat_var = tk.BooleanVar()
        ttk.Checkbutton(
            row, text="Auto-walk (hold W)", variable=self.walk_var, command=self._settings_changed
        ).pack(side="left")
        ttk.Checkbutton(
            row, text="Sprint (tap Ctrl once)", variable=self.sprint_var, command=self._settings_changed
        ).pack(side="left", padx=(18, 0))
        ttk.Checkbutton(
            row, text="Auto-eat (hold right)", variable=self.eat_var, command=self._settings_changed
        ).pack(side="right")

        row2 = tk.Frame(movement, bg=self.CARD)
        row2.pack(fill="x", padx=14, pady=(2, 12))
        self.focus_var = tk.BooleanVar()
        ttk.Checkbutton(
            row2, text="Auto-jump (hold Space)", variable=self.jump_var, command=self._settings_changed
        ).pack(side="left")
        ttk.Checkbutton(
            row2, text="Pause if the selected target loses focus", variable=self.focus_var
        ).pack(side="left", padx=(18, 0))
        tk.Label(row2, text="Delay", bg=self.CARD, fg=self.MUTED, font=("Segoe UI", 9)).pack(side="left", padx=(14, 5))
        self.delay_var = tk.StringVar()
        self._entry(row2, self.delay_var, 4).pack(side="left")
        tk.Label(row2, text="sec", bg=self.CARD, fg=self.MUTED, font=("Segoe UI", 9)).pack(side="left", padx=(4, 0))

        clicking = self._card(body, "Auto clicker")
        click_row = tk.Frame(clicking, bg=self.CARD)
        click_row.pack(fill="x", padx=14, pady=(0, 12))
        self.click_var = tk.BooleanVar()
        ttk.Checkbutton(
            click_row, text="Enable", variable=self.click_var, command=self._settings_changed
        ).pack(side="left", padx=(0, 12))
        tk.Label(click_row, text="CPS", bg=self.CARD, fg=self.MUTED, font=("Segoe UI", 9)).pack(side="left", padx=(0, 5))
        self.cps_var = tk.StringVar()
        self._entry(click_row, self.cps_var, 6).pack(side="left", padx=(0, 14))
        tk.Label(click_row, text="Button", bg=self.CARD, fg=self.MUTED, font=("Segoe UI", 9)).pack(side="left", padx=(0, 5))
        self.click_button_var = tk.StringVar()
        self.click_button_combo = ttk.Combobox(
            click_row,
            textvariable=self.click_button_var,
            values=("Left", "Right"),
            state="readonly",
            width=8,
        )
        self.click_button_combo.pack(side="left", padx=(0, 14))
        self.click_button_combo.bind("<<ComboboxSelected>>", lambda _event: self._settings_changed())
        tk.Label(click_row, text="Mode", bg=self.CARD, fg=self.MUTED, font=("Segoe UI", 9)).pack(side="left", padx=(0, 5))
        self.click_mode_var = tk.StringVar()
        self.click_mode_combo = ttk.Combobox(
            click_row,
            textvariable=self.click_mode_var,
            values=("Click", "Hold"),
            state="readonly",
            width=8,
        )
        self.click_mode_combo.pack(side="left")
        self.click_mode_combo.bind("<<ComboboxSelected>>", lambda _event: self._settings_changed())

        target = self._card(body, "Target window")
        target_toggle_row = tk.Frame(target, bg=self.CARD)
        target_toggle_row.pack(fill="x", padx=14, pady=(0, 7))
        self.target_mode_var = tk.BooleanVar()
        ttk.Checkbutton(
            target_toggle_row,
            text="Background target mode — do not send clicks to my active window",
            variable=self.target_mode_var,
            command=self._target_mode_changed,
        ).pack(side="left")
        target_row = tk.Frame(target, bg=self.CARD)
        target_row.pack(fill="x", padx=14, pady=(0, 6))
        self.target_var = tk.StringVar()
        self.target_combo = ttk.Combobox(
            target_row,
            textvariable=self.target_var,
            state="readonly",
            width=53,
        )
        self.target_combo.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.target_combo.bind("<<ComboboxSelected>>", lambda _event: self._target_changed())
        ttk.Button(target_row, text="Refresh", command=self.refresh_windows, width=9).pack(side="right")
        tk.Label(
            target,
            text="With Pause off, Alt+Tab keeps sending only to this target. Some applications may reject background input.",
            bg=self.CARD,
            fg=self.AMBER,
            font=("Segoe UI", 8),
        ).pack(anchor="w", padx=14, pady=(0, 11))

        timer = self._card(body, "Optional active-time timer")
        timer_row = tk.Frame(timer, bg=self.CARD)
        timer_row.pack(fill="x", padx=14, pady=(0, 12))
        self.timer_var = tk.BooleanVar()
        ttk.Checkbutton(timer_row, text="Stop after", variable=self.timer_var).pack(side="left", padx=(0, 12))
        self.hours_var = tk.StringVar()
        self.minutes_var = tk.StringVar()
        self.seconds_var = tk.StringVar()
        for var, suffix in ((self.hours_var, "h"), (self.minutes_var, "m"), (self.seconds_var, "s")):
            self._entry(timer_row, var, 4).pack(side="left")
            tk.Label(timer_row, text=suffix, bg=self.CARD, fg=self.MUTED, font=("Segoe UI", 9)).pack(side="left", padx=(3, 8))
        self.countdown_label = tk.Label(
            timer_row, text="", bg=self.CARD, fg=self.AMBER, font=("Consolas", 10, "bold")
        )
        self.countdown_label.pack(side="right")

        hotkeys = self._card(body, "Controls")
        hotkey_row = tk.Frame(hotkeys, bg=self.CARD)
        hotkey_row.pack(fill="x", padx=14, pady=(0, 7))
        tk.Label(hotkey_row, text="Toggle automation", bg=self.CARD, fg=self.TEXT, font=("Segoe UI", 10)).pack(side="left")
        self.hotkey_button = ttk.Button(hotkey_row, text="F6", command=self.begin_hotkey_capture, width=15)
        self.hotkey_button.pack(side="right")
        stop_row = tk.Frame(hotkeys, bg=self.CARD)
        stop_row.pack(fill="x", padx=14, pady=(0, 12))
        tk.Label(stop_row, text="Emergency stop", bg=self.CARD, fg=self.TEXT, font=("Segoe UI", 10)).pack(side="left")
        self.stop_hotkey_label = tk.Label(
            stop_row, text="F7", bg=self.CARD, fg=self.RED, font=("Segoe UI", 10, "bold")
        )
        self.stop_hotkey_label.pack(side="right", padx=9)

        self.action_button = ttk.Button(body, text="START AUTOMATION", style="Accent.TButton", command=self.toggle)
        self.action_button.pack(fill="x", pady=(1, 8))
        self.detail_label = tk.Label(
            body,
            text="All held keyboard and mouse input releases on stop, timer completion, pause, or exit.",
            bg=self.BG,
            fg=self.MUTED,
            font=("Segoe UI", 9),
        )
        self.detail_label.pack()

    def _load_vars(self) -> None:
        self.walk_var.set(bool(self.config.get("walk", True)))
        self.sprint_var.set(bool(self.config.get("sprint", False)))
        self.jump_var.set(bool(self.config.get("jump", False)))
        self.eat_var.set(bool(self.config.get("auto_eat", False)))
        self.focus_var.set(bool(self.config.get("focus_guard", True)))
        self.delay_var.set(str(self.config.get("delay", "0")))
        self.click_var.set(bool(self.config.get("click_enabled", False)))
        self.cps_var.set(str(self.config.get("cps", "1.6")))
        self.click_button_var.set("Right" if self.config.get("click_button") == "Right" else "Left")
        self.click_mode_var.set("Hold" if self.config.get("click_mode") == "Hold" else "Click")
        self.target_mode_var.set(bool(self.config.get("target_mode", False)))
        self.target_var.set(str(self.config.get("target_title", "")))
        self.timer_var.set(bool(self.config.get("timer_enabled", False)))
        self.hours_var.set(str(self.config.get("hours", "0")))
        self.minutes_var.set(str(self.config.get("minutes", "0")))
        self.seconds_var.set(str(self.config.get("seconds", "0")))
        self.hotkey_button.configure(text=self.hotkey["display"])

    def _save_config(self) -> None:
        data = {
            "walk": self.walk_var.get(),
            "sprint": self.sprint_var.get(),
            "jump": self.jump_var.get(),
            "auto_eat": self.eat_var.get(),
            "focus_guard": self.focus_var.get(),
            "delay": self.delay_var.get(),
            "click_enabled": self.click_var.get(),
            "cps": self.cps_var.get(),
            "click_button": self.click_button_var.get(),
            "click_mode": self.click_mode_var.get(),
            "target_mode": self.target_mode_var.get(),
            "target_title": self.window_titles.get(
                self.target_var.get(), self.target_var.get()
            ),
            "timer_enabled": self.timer_var.get(),
            "hours": self.hours_var.get(),
            "minutes": self.minutes_var.get(),
            "seconds": self.seconds_var.get(),
            "hotkey": self.hotkey,
            "emergency_hotkey": self.emergency_hotkey,
        }
        try:
            write_settings(self.config_path, data)
        except OSError:
            LOGGER.exception("Could not save settings")
            if not self._save_warning_shown and not self.test_mode:
                self._save_warning_shown = True
                messagebox.showwarning(APP_NAME, f"Settings could not be saved to {self.config_path}. Check folder permissions.")

    def refresh_windows(self) -> None:
        resume_after_refresh = self.requested
        if resume_after_refresh:
            self.stop()
        previous_display = self.target_var.get()
        previous_hwnd = self.window_map.get(previous_display)
        wanted_title = self.window_titles.get(
            previous_display,
            str(self.config.get("target_title", previous_display)),
        )
        windows = list_target_windows(int(self.root.winfo_id()))
        self.window_map.clear()
        self.window_titles.clear()
        values: list[str] = []
        selected = ""
        for hwnd, title in windows:
            display = f"{title}  [0x{hwnd:X}]"
            values.append(display)
            self.window_map[display] = hwnd
            self.window_titles[display] = title
            if previous_hwnd == hwnd:
                selected = display
            elif not selected and wanted_title and title == wanted_title:
                selected = display
        if not selected:
            selected = values[0] if values else "No targetable windows found"
        self.target_combo.configure(values=values)
        self.target_var.set(selected)
        if resume_after_refresh and previous_hwnd in self.window_map.values():
            self.start()

    def _target_changed(self) -> None:
        resume_after_change = self.requested
        if resume_after_change:
            self.stop()
        self._save_config()
        if resume_after_change:
            self.start()

    def _target_mode_changed(self) -> None:
        resume_after_change = self.requested
        if resume_after_change:
            self.stop()
        self._save_config()
        if resume_after_change:
            self.start()

    def _selected_target(self) -> int:
        hwnd = self.window_map.get(self.target_var.get(), 0)
        if not hwnd or not self.keys._user32.IsWindow(hwnd):
            raise ValueError("Choose an open target window and press Refresh if needed.")
        return hwnd

    def _register_hotkey_or_warn(self) -> None:
        requested_display = self.hotkey["display"]
        toggle_candidates = [self.hotkey,
            {"modifiers": 0, "vk": 0x77, "display": "F8"},
            {"modifiers": 0, "vk": 0x78, "display": "F9"},
            {"modifiers": 0, "vk": 0x79, "display": "F10"},
            {"modifiers": 0, "vk": 0x7A, "display": "F11"},
        ]
        emergency_candidates = [
            {"modifiers": 0, "vk": VK_F7, "display": "F7"},
            {"modifiers": 0, "vk": 0x77, "display": "F8"},
            {"modifiers": 0, "vk": 0x78, "display": "F9"},
        ]
        first_error = ""
        seen: set[tuple[int, int]] = set()
        for toggle_candidate in toggle_candidates:
            pair = (toggle_candidate["modifiers"], toggle_candidate["vk"])
            if pair in seen:
                continue
            seen.add(pair)
            for emergency_candidate in emergency_candidates:
                if self.hotkeys.start(
                    toggle_candidate["modifiers"],
                    toggle_candidate["vk"],
                    emergency_candidate["modifiers"],
                    emergency_candidate["vk"],
                ):
                    self.hotkeys_ready = True
                    self.hotkey = toggle_candidate
                    self.emergency_hotkey = emergency_candidate
                    self.hotkey_button.configure(text=toggle_candidate["display"])
                    self.stop_hotkey_label.configure(text=emergency_candidate["display"])
                    self._save_config()
                    changed = (
                        toggle_candidate["display"] != requested_display
                        or emergency_candidate["display"] != "F7"
                    )
                    if changed and not self.test_mode:
                        self.root.after(
                            100,
                            lambda: messagebox.showinfo(
                                APP_NAME,
                                "A default hotkey is already used by another app.\n\n"
                                f"Toggle: {self.hotkey['display']}\n"
                                f"Emergency stop: {self.emergency_hotkey['display']}",
                            ),
                        )
                    return
                if not first_error:
                    first_error = self.hotkeys.last_error

        self.hotkeys_ready = False
        if self.test_mode:
            raise RuntimeError(f"Could not register the test hotkeys: {first_error}")
        self.root.after(
            100,
            lambda: messagebox.showwarning(
                APP_NAME,
                f"No global toggle key could be registered ({first_error}).\n\n"
                "Close the conflicting app or choose a different toggle key.",
            ),
        )

    def _parse_number(self, value: str, label: str, maximum: int) -> int:
        try:
            number = int(value or "0")
        except ValueError as exc:
            raise ValueError(f"{label} must be a whole number.") from exc
        if not 0 <= number <= maximum:
            raise ValueError(f"{label} must be between 0 and {maximum}.")
        return number

    def _timer_seconds(self) -> float | None:
        if not self.timer_var.get():
            return None
        hours = self._parse_number(self.hours_var.get(), "Hours", 999)
        minutes = self._parse_number(self.minutes_var.get(), "Minutes", 59)
        seconds = self._parse_number(self.seconds_var.get(), "Seconds", 59)
        total = hours * 3600 + minutes * 60 + seconds
        if total <= 0:
            raise ValueError("Set a timer greater than zero, or turn the timer off.")
        return float(total)

    def _delay_seconds(self) -> float:
        try:
            delay = float(self.delay_var.get() or "0")
        except ValueError as exc:
            raise ValueError("Delay must be a number from 0 to 30 seconds.") from exc
        if not 0 <= delay <= 30:
            raise ValueError("Delay must be from 0 to 30 seconds.")
        return delay

    def _click_interval(self) -> float:
        try:
            cps = float(self.cps_var.get())
        except ValueError as exc:
            raise ValueError("CPS must be a number from 0.1 to 100.") from exc
        if not 0.1 <= cps <= 100:
            raise ValueError("CPS must be from 0.1 to 100.")
        return 1.0 / cps

    def start(self) -> None:
        if self.capture_active:
            return
        if self.fatal_error:
            messagebox.showerror(APP_NAME, "Restart AFK Pilot after the unexpected error.")
            return
        if not self.hotkeys_ready:
            messagebox.showerror(APP_NAME, "Automation is disabled until both hotkeys are available. Close a conflicting app, then assign the toggle key again.")
            return
        try:
            if not (self.walk_var.get() or self.click_var.get() or self.eat_var.get()):
                raise ValueError("Enable auto-walk, auto-click, or auto-eat before starting.")
            if (
                self.eat_var.get()
                and self.click_var.get()
                and self.click_button_var.get().lower() == "right"
            ):
                raise ValueError(
                    "Auto-eat already holds right-click. Choose Left for auto-click, or turn one option off."
                )
            self.remaining = self._timer_seconds()
            delay = self._delay_seconds()
            self.click_interval = self._click_interval() if self.click_var.get() else 1.0
            self.target_hwnd = self._selected_target()
        except ValueError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return

        self.requested = True
        self.start_at = time.monotonic() + delay
        self.last_tick = time.monotonic()
        self.next_click_at = self.start_at
        self.background_reassert_pending = False
        self.last_target_focus = None
        self._save_config()

    def stop(self) -> None:
        self.requested = False
        self.active = False
        self.remaining = None
        self.keys.release_all()
        self.target_hwnd = None
        self.input_target_hwnd = None
        self.background_reassert_pending = False
        self.last_target_focus = None

    def toggle(self) -> None:
        self.stop() if self.requested else self.start()

    def _settings_changed(self) -> None:
        if self.requested:
            self.stop()
        self._save_config()

    def _target_is_focused(self) -> bool:
        hwnd = self.keys._user32.GetForegroundWindow()
        return self.target_hwnd is not None and int(hwnd or 0) == self.target_hwnd

    def _set_status(self, text: str, color: str, detail: str) -> None:
        self.status_label.configure(text=text, bg=color)
        self.detail_label.configure(text=detail)
        self.action_button.configure(text="STOP & RELEASE INPUT" if self.requested else "START AUTOMATION")
        self.countdown_label.configure(
            text=format_duration(self.remaining) if self.remaining is not None and self.requested else ""
        )

    def _tick(self) -> None:
        if self.closed:
            return
        if self._tick_after is not None:
            self.root.after_cancel(self._tick_after)
            self._tick_after = None
        pending: list[str] = []
        try:
            while True:
                pending.append(self.events.get_nowait())
        except queue.Empty:
            pass
        if "hotkeys_failed" in pending:
            self.stop()
            self.hotkeys_ready = False
            messagebox.showerror(APP_NAME, "Global hotkeys stopped working. Automation has stopped. Restart AFK Pilot.")
        elif "stop" in pending:
            self.stop()
        elif not self.capture_active and pending.count("toggle") % 2:
            self.toggle()

        now = time.monotonic()
        delta = max(0.0, now - self.last_tick)
        self.last_tick = now

        if not self.requested:
            if self.active:
                self.keys.release_all()
                self.active = False
            self._set_status("STOPPED", self.RED, "W is safely released.")
        elif now < self.start_at:
            if self.active:
                self.keys.release_all()
                self.active = False
            wait = self.start_at - now
            self._set_status(
                "STARTING",
                self.AMBER,
                f"Automation starts in {wait:.1f} seconds. Press {self.emergency_hotkey['display']} to cancel.",
            )
        elif self.target_hwnd is not None and not self.keys._user32.IsWindow(
            self.target_hwnd
        ):
            if self.active:
                self.keys.release_all()
                self.active = False
            self.stop()
            self._set_status("TARGET CLOSED", self.RED, "The selected target window is no longer open.")
        elif (
            not self.target_mode_var.get()
            and self.focus_var.get()
            and not self._target_is_focused()
        ):
            if self.active:
                self.keys.release_all()
                self.active = False
                self.input_target_hwnd = None
            self._set_status("PAUSED", self.AMBER, "Focus the selected target to resume automatically.")
        else:
            target_is_focused = self._target_is_focused()
            previous_target_focus = self.last_target_focus
            self.last_target_focus = target_is_focused
            desired_input_target = (
                self.target_hwnd
                if self.target_mode_var.get() or not target_is_focused
                else None
            )
            if self.active and self.input_target_hwnd != desired_input_target:
                self.keys.release_all()
                self.active = False
                self.background_reassert_pending = False
            self.input_target_hwnd = desired_input_target

            if not self.active:
                try:
                    self.keys.apply_movement(
                        self.walk_var.get(),
                        self.sprint_var.get(),
                        self.jump_var.get(),
                        self.input_target_hwnd,
                    )
                    if self.eat_var.get():
                        self.keys.hold_mouse("right", self.input_target_hwnd)
                    if self.click_var.get() and self.click_mode_var.get().lower() == "hold":
                        self.keys.hold_mouse(
                            self.click_button_var.get().lower(), self.input_target_hwnd
                        )
                    self.next_click_at = now
                    self.active = True
                    if self.input_target_hwnd is not None and not target_is_focused:
                        self.background_reassert_at = (
                            now + BACKGROUND_REASSERT_DELAY_SECONDS
                        )
                        self.background_reassert_pending = True
                except (OSError, RuntimeError) as exc:
                    self.stop()
                    messagebox.showerror(APP_NAME, f"Windows rejected the targeted input:\n{exc}")

            transition_delay = focus_reassert_delay(
                previous_target_focus,
                target_is_focused,
                self.target_mode_var.get(),
            )
            if self.active and transition_delay is not None:
                self.background_reassert_at = now + transition_delay
                self.background_reassert_pending = True

            if (
                self.active
                and self.click_var.get()
                and self.click_mode_var.get().lower() == "click"
            ):
                button = self.click_button_var.get().lower()
                clicks_sent = 0
                try:
                    while now >= self.next_click_at and clicks_sent < 10:
                        self.keys.click(button, self.input_target_hwnd)
                        self.next_click_at += self.click_interval
                        clicks_sent += 1
                    if clicks_sent == 10 and self.next_click_at < now:
                        self.next_click_at = now + self.click_interval
                except (OSError, RuntimeError) as exc:
                    self.stop()
                    messagebox.showerror(APP_NAME, f"Windows rejected the targeted click:\n{exc}")

            if (
                self.active
                and self.background_reassert_pending
                and self.input_target_hwnd is not None
                and now >= self.background_reassert_at
            ):
                try:
                    self.keys.reassert_background(
                        self.sprint_var.get(), self.input_target_hwnd
                    )
                    self.background_reassert_pending = False
                except (OSError, RuntimeError) as exc:
                    self.stop()
                    messagebox.showerror(APP_NAME, f"Windows rejected background input:\n{exc}")

            if self.active:
                try:
                    self.keys.advance_sprint()
                except (OSError, RuntimeError) as exc:
                    self.stop()
                    messagebox.showerror(APP_NAME, f"Windows rejected sprint input: {exc}")

            if self.input_target_hwnd is not None:
                state_text = "TARGETED" if target_is_focused else "BACKGROUND"
                target_detail = f"Sending only to {self.window_titles.get(self.target_var.get(), 'selected window')}"
            else:
                state_text = "RUNNING"
                target_detail = "Foreground mode"
            if self.active and self.remaining is not None:
                self.remaining -= delta
                if self.remaining <= 0:
                    self.stop()
                    self._set_status("FINISHED", self.GREEN, "Timer complete. All input is released.")
                else:
                    self._set_status(
                        state_text,
                        self.GREEN,
                        f"{target_detail}. Press {self.emergency_hotkey['display']} for an immediate stop.",
                    )
            elif self.active:
                self._set_status(
                    state_text,
                    self.GREEN,
                    f"{target_detail}. Press {self.emergency_hotkey['display']} for an immediate stop.",
                )

        if not self.closed:
            self._tick_after = self.root.after(10, self._tick)

    def begin_hotkey_capture(self) -> None:
        if self.capture_active:
            return
        self.stop()
        self.hotkeys.stop()
        self.hotkeys_ready = False
        self._discard_hotkey_events()
        self.capture_active = True
        self.capture_candidate = None
        dialog = tk.Toplevel(self.root)
        self.capture_dialog = dialog
        dialog.title("Assign toggle hotkey")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.configure(bg=self.CARD)
        tk.Label(dialog, text="Press and release your new hotkey", bg=self.CARD, fg=self.TEXT,
                 font=("Segoe UI", 12, "bold")).pack(padx=24, pady=(22, 10))
        self.capture_hint = tk.Label(dialog, text="A single key works. Ctrl, Alt, and Shift are optional.\nEsc cancels. W, Space, and F12 are unavailable.",
                                     bg=self.CARD, fg=self.MUTED, wraplength=370)
        self.capture_hint.pack(padx=24, pady=(0, 16))
        ttk.Button(dialog, text="Cancel", command=lambda: self._finish_capture(self.hotkey)).pack(pady=(0, 20))
        dialog.protocol("WM_DELETE_WINDOW", lambda: self._finish_capture(self.hotkey))
        # Capture before widget/class bindings can consume plain letters or Space.
        dialog.bindtags((str(dialog),))
        dialog.bind("<KeyPress>", self._capture_hotkey)
        dialog.bind("<KeyRelease>", self._capture_release)
        dialog.bind("<FocusOut>", self._schedule_capture_focus_check)
        dialog.grab_set()
        dialog.focus_force()

    def _schedule_capture_focus_check(self, _event: tk.Event) -> None:
        if self._capture_focus_after is not None:
            self.root.after_cancel(self._capture_focus_after)
        self._capture_focus_after = self.root.after(100, self._capture_focus_lost)

    def _capture_focus_lost(self) -> None:
        self._capture_focus_after = None
        if self.capture_dialog is None:
            return
        focused = self.root.focus_get()
        if focused is None or focused.winfo_toplevel() != self.capture_dialog:
            self._finish_capture(self.hotkey)

    def _capture_hotkey(self, event: tk.Event) -> str:
        if event.keysym == "Escape":
            self._finish_capture(self.hotkey)
            return "break"
        if self.capture_candidate is not None:
            return "break"
        try:
            candidate = hotkey_from_event(event)
            if candidate is None:
                return "break"
            if (candidate["vk"], candidate["modifiers"]) == (self.emergency_hotkey["vk"], self.emergency_hotkey["modifiers"]):
                raise ValueError(f"{self.emergency_hotkey['display']} is reserved for emergency stop.")
        except ValueError as exc:
            self.capture_hint.configure(text=str(exc), fg=self.AMBER)
            return "break"
        self.capture_candidate = candidate
        self.capture_hint.configure(text=f"Release {candidate['display']} to save.", fg=self.GREEN)
        return "break"

    def _capture_release(self, event: tk.Event) -> str:
        candidate = self.capture_candidate
        if candidate is not None and int(event.keycode) == candidate["vk"]:
            self._finish_capture(candidate)
        return "break"

    def _discard_hotkey_events(self) -> None:
        try:
            while True:
                self.events.get_nowait()
        except queue.Empty:
            pass

    def _finish_capture(self, hotkey: dict[str, Any]) -> None:
        old_hotkey = self.hotkey
        dialog = self.capture_dialog
        self.capture_dialog = None
        self.capture_candidate = None
        if dialog is not None:
            dialog.grab_release()
            dialog.destroy()
        self._discard_hotkey_events()
        self.hotkeys_ready = self.hotkeys.start(hotkey["modifiers"], hotkey["vk"],
                                               self.emergency_hotkey["modifiers"], self.emergency_hotkey["vk"])
        if self.hotkeys_ready:
            self.hotkey = hotkey
        else:
            failure = self.hotkeys.last_error
            self.hotkeys_ready = self.hotkeys.start(old_hotkey["modifiers"], old_hotkey["vk"],
                                                   self.emergency_hotkey["modifiers"], self.emergency_hotkey["vk"])
            restoration = f"Kept {old_hotkey['display']}." if self.hotkeys_ready else "Automation is disabled. Restart AFK Pilot after closing the conflicting app."
            messagebox.showwarning(APP_NAME, f"Could not assign {hotkey['display']}. {failure}\n\n{restoration}")
        self.capture_active = False
        self.hotkey_button.configure(text=self.hotkey["display"])
        self._save_config()

    def _callback_failed(self, exc_type: type, exc: BaseException, traceback: Any) -> None:
        self.stop()
        self.fatal_error = True
        LOGGER.error("UI callback failed", exc_info=(exc_type, exc, traceback))
        # Fail closed; an exception in the timer loop must never leave inputs held.
        self.hotkeys_ready = False
        self._set_status("ERROR", self.RED, "Automation stopped. Restart AFK Pilot.")
        if self.test_mode:
            self.test_failure = exc
            self.close()
            return
        messagebox.showerror(APP_NAME, f"Automation stopped after an unexpected error.\n{exc}\n\nDiagnostics: {self.config_path.parent / 'afkpilot.log'}")

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for callback in (self._tick_after, self._capture_focus_after):
            if callback is not None:
                self.root.after_cancel(callback)
        self._tick_after = self._capture_focus_after = None
        try:
            self.stop()
            self.hotkeys.stop()
            self._save_config()
        finally:
            atexit.unregister(self.keys.release_all)
            if self.log_handler is not None:
                LOGGER.removeHandler(self.log_handler)
                self.log_handler.close()
            self.root.destroy()


def self_test() -> None:
    assert format_duration(0) == "00:00:00"
    assert format_duration(61) == "00:01:01"
    assert format_duration(3661) == "01:01:01"
    assert ctypes.sizeof(_INPUT) in {28, 40}
    assert keyboard_lparam(0x11, False) == 0x00110001
    assert keyboard_lparam(0x11, True) == 0xC0110001
    assert focus_reassert_delay(None, True, True) is None
    assert focus_reassert_delay(True, True, True) is None
    assert focus_reassert_delay(True, False, False) is None
    assert focus_reassert_delay(True, False, True) == BACKGROUND_REASSERT_DELAY_SECONDS
    assert focus_reassert_delay(False, True, True) == 0.0

    class FakeUser32:
        def __init__(self) -> None:
            self.messages: list[tuple[int, int, int, int]] = []

        def IsWindow(self, _hwnd: int) -> int:
            return 1

        def MapVirtualKeyW(self, vk: int, _mode: int) -> int:
            return vk

        def PostMessageW(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
            self.messages.append((hwnd, message, wparam, lparam))
            return 1

        def GetClientRect(self, _hwnd: int, rect_pointer: Any) -> int:
            rect = ctypes.cast(rect_pointer, ctypes.POINTER(wintypes.RECT)).contents
            rect.left = 0
            rect.top = 0
            rect.right = 800
            rect.bottom = 600
            return 1

    fake = FakeUser32()
    controller = KeyController()
    controller._user32 = fake
    now = [0.0]
    controller._clock = lambda: now[0]
    controller.apply_movement(True, True, False, 123)
    assert controller._held == [VK_W]
    now[0] += SPRINT_WARMUP_SECONDS
    controller.advance_sprint()
    now[0] += SPRINT_HOLD_SECONDS
    controller.advance_sprint()
    key_messages = [(message, vk) for _hwnd, message, vk, _lp in fake.messages]
    assert key_messages[:3] == [
        (WM_KEYDOWN, VK_W),
        (WM_KEYDOWN, VK_LCONTROL),
        (WM_KEYUP, VK_LCONTROL),
    ]
    assert controller._held == [VK_W]
    before_reassert = len(fake.messages)
    controller.reassert_background(True, 123)
    now[0] += SPRINT_WARMUP_SECONDS
    controller.advance_sprint()
    now[0] += SPRINT_HOLD_SECONDS
    controller.advance_sprint()
    reassert_messages = fake.messages[before_reassert:]
    assert [(message, vk) for _hwnd, message, vk, _lp in reassert_messages] == [
        (WM_KEYDOWN, VK_W),
        (WM_KEYDOWN, VK_LCONTROL),
        (WM_KEYUP, VK_LCONTROL),
    ]
    controller.hold_mouse("right", 123)
    controller.click("left", 123)
    controller.release_all()
    assert controller._held == []
    assert controller._mouse_held == []
    assert any(message == WM_RBUTTONDOWN for _hwnd, message, _wp, _lp in fake.messages)
    assert any(message == WM_RBUTTONUP for _hwnd, message, _wp, _lp in fake.messages)


class SingleInstance:
    """Prevent two copies competing for the same global hotkeys/settings."""

    def __init__(self, name: str = "Local\\AFKPilot") -> None:
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel32.CreateMutexW.restype = wintypes.HANDLE
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel32.CreateMutexW(None, False, name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.already_running = ctypes.get_last_error() == 183

    def close(self) -> None:
        if self.handle:
            self.kernel32.CloseHandle(self.handle)
            self.handle = None


def main() -> None:
    if "--version" in sys.argv:
        print(APP_VERSION)
        return
    if "--self-test" in sys.argv:
        self_test()
        return
    if os.name != "nt":
        raise SystemExit("AFK Pilot requires Windows 10 or 11.")
    gui_self_test = "--gui-self-test" in sys.argv
    with tempfile.TemporaryDirectory(prefix="afkpilot-smoke-") as test_directory:
        if gui_self_test:
            os.environ["AFKPILOT_CONFIG_DIR"] = test_directory
        instance = None if gui_self_test else SingleInstance()
        root = tk.Tk()
        root.withdraw()
        app = None
        try:
            if instance is not None and instance.already_running:
                messagebox.showinfo(APP_NAME, "AFK Pilot is already running. Use or close the existing window before opening another copy.", parent=root)
                return
            app = AFKPilotApp(root, test_mode=gui_self_test)
            if gui_self_test:
                root.after(500, app.close)
            else:
                root.deiconify()
            root.mainloop()
            if app.test_failure is not None:
                raise app.test_failure
        finally:
            if app is not None and not app.closed:
                app.close()
            elif app is None:
                root.destroy()
            if instance is not None:
                instance.close()


if __name__ == "__main__":
    main()
