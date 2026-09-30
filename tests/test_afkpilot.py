import ctypes
import json
import os
from pathlib import Path
import queue
import tempfile
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

import AFKPilot as pilot


def key(keysym, keycode, state=0):
    return SimpleNamespace(keysym=keysym, keycode=keycode, state=state)


class HotkeyParsingTests(unittest.TestCase):
    def test_num_caps_scroll_lock_do_not_add_modifiers(self):
        for state in (0, 0x8, 0x2, 0x20, 0x2A):
            with self.subTest(state=state):
                self.assertEqual(pilot.hotkey_from_event(key("F8", 0x77, state)),
                                 {"modifiers": 0, "vk": 0x77, "display": "F8"})

    def test_single_letter_needs_no_ctrl(self):
        self.assertEqual(pilot.hotkey_from_event(key("k", 0x4B, 0x8))["display"], "K")

    def test_actual_alt_masks_and_chords(self):
        for state in (0x10, 0x20000, 0x20010):
            self.assertEqual(pilot.hotkey_from_event(key("F8", 0x77, state))["display"], "ALT + F8")
        self.assertEqual(pilot.hotkey_from_event(key("K", 0x4B, 0x2001D))["display"], "CTRL + ALT + SHIFT + K")

    def test_shifted_digit_keeps_physical_key(self):
        self.assertEqual(pilot.hotkey_from_event(key("exclam", 0x31, 1))["display"], "SHIFT + 1")

    def test_unsupported_and_modifier_keys_are_ignored(self):
        for keysym, code in (("Control_L", 0x11), ("Alt_R", 0xA5), ("Win_L", 0x5B), ("space", 0x20)):
            self.assertIsNone(pilot.hotkey_from_event(key(keysym, code)))

    def test_reserved_keys_are_rejected(self):
        for code in (pilot.VK_W, 0x7B):
            with self.assertRaises(ValueError):
                pilot.make_hotkey(0, code)

    def test_saved_display_is_derived_from_binding(self):
        app = object.__new__(pilot.AFKPilotApp)
        app.config = {"hotkey": {"modifiers": 0, "vk": 0x77, "display": "CTRL + WRONG"}}
        self.assertEqual(app._load_hotkey()["display"], "F8")
        for corrupt in (None, [], "bad", {"modifiers": 8, "vk": 0x77}, {"modifiers": 0, "vk": pilot.VK_W}):
            app.config = {"hotkey": corrupt}
            self.assertEqual(app._load_hotkey(), pilot.DEFAULT_HOTKEY)


class SettingsTests(unittest.TestCase):
    def test_invalid_json_and_non_object_recover(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            self.assertEqual(pilot.read_settings(path), {})
            for contents in ("[1]", "null", '"bad"', "{broken"):
                path.write_text(contents, encoding="utf-8")
                self.assertEqual(pilot.read_settings(path), {})

    def test_atomic_save_and_failure_preserves_old_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            pilot.write_settings(path, {"version": 1})
            with patch.object(Path, "replace", side_effect=OSError("read only")):
                with self.assertRaises(OSError):
                    pilot.write_settings(path, {"version": 2})
            self.assertEqual(pilot.read_settings(path), {"version": 1})
            self.assertFalse(path.with_suffix(".tmp").exists())
            pilot.write_settings(path, {"version": 2})
            self.assertEqual(pilot.read_settings(path), {"version": 2})


class InputSafetyTests(unittest.TestCase):
    def setUp(self):
        self.controller = pilot.KeyController()
        self.controller._send_key = Mock()
        self.controller._send_mouse = Mock()
        self.now = 0.0
        self.controller._clock = lambda: self.now

    def test_stop_during_sprint_warmup_sends_no_ctrl(self):
        self.controller.apply_movement(True, True, False, 123)
        self.controller.release_all()
        self.now = 10.0
        self.controller.advance_sprint()
        self.assertEqual(self.controller._send_key.call_args_list,
                         [unittest.mock.call(pilot.VK_W, False, 123), unittest.mock.call(pilot.VK_W, True, 123)])

    def test_stop_during_sprint_hold_releases_ctrl_and_walk(self):
        self.controller.apply_movement(True, True, True, 123)
        self.now = 0.20
        self.controller.advance_sprint()
        self.assertIn(pilot.VK_LCONTROL, self.controller._held)
        self.controller.release_all()
        self.assertEqual(self.controller._held, [])
        for code in (pilot.VK_W, pilot.VK_SPACE, pilot.VK_LCONTROL):
            self.controller._send_key.assert_any_call(code, True, 123)

    def test_mouse_up_failure_remains_tracked_for_cleanup(self):
        self.controller._send_mouse.side_effect = [None, OSError("transient"), None]
        with self.assertRaises(OSError):
            self.controller.click("left", 123)
        self.assertEqual(self.controller._mouse_held, ["left"])
        self.controller.release_all()
        self.assertEqual(self.controller._mouse_held, [])
        self.controller._send_mouse.assert_called_with("left", False, 123)

    def test_target_change_releases_previous_target_first(self):
        self.controller.apply_movement(True, False, False, 123)
        self.controller.hold_mouse("right", 123)
        self.controller.apply_movement(True, False, False, 456)
        self.controller._send_key.assert_any_call(pilot.VK_W, True, 123)
        self.controller._send_mouse.assert_any_call("right", False, 123)
        self.controller._send_key.assert_called_with(pilot.VK_W, False, 456)

    def test_existing_movement_and_focus_regressions(self):
        pilot.self_test()


@unittest.skipUnless(os.name == "nt", "Windows registration required")
class WindowsLifecycleTests(unittest.TestCase):
    def test_listener_rebinds_and_unregisters_without_orphan_threads(self):
        listener = pilot.HotkeyListener(queue.Queue())
        try:
            for _ in range(10):
                self.assertTrue(listener.start(0, 0x86, 0, 0x87), listener.last_error)
                listener.stop()
                self.assertIsNone(listener._thread)
        finally:
            listener.stop()

    def test_registration_conflict_releases_partial_reservation(self):
        first = pilot.HotkeyListener(queue.Queue())
        second = pilot.HotkeyListener(queue.Queue())
        try:
            self.assertTrue(first.start(0, 0x86, 0, 0x87), first.last_error)
            self.assertFalse(second.start(0, 0x85, 0, 0x87))
            first.stop()
            self.assertTrue(first.start(0, 0x85, 0, 0x87), first.last_error)
        finally:
            first.stop()
            second.stop()

    def test_single_instance_guard_releases_handle(self):
        name = "Local\\AFKPilot-Test-" + str(uuid.uuid4())
        first = pilot.SingleInstance(name)
        second = pilot.SingleInstance(name)
        try:
            self.assertFalse(first.already_running)
            self.assertTrue(second.already_running)
        finally:
            first.close()
            second.close()
        third = pilot.SingleInstance(name)
        try:
            self.assertFalse(third.already_running)
        finally:
            third.close()


@unittest.skipUnless(os.name == "nt", "Windows Tk integration required")
class GuiSafetyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"AFKPILOT_CONFIG_DIR": self.directory.name})
        self.environment.start()
        self.root = tk.Tk()
        self.root.withdraw()
        with patch.object(pilot.HotkeyListener, "start", return_value=True):
            self.app = pilot.AFKPilotApp(self.root, test_mode=True)
        # Real registration is covered above; isolate GUI behavior and conflicts.
        self.app.hotkeys.stop()
        self.app.hotkeys = Mock(start=Mock(return_value=True), last_error="Conflict")
        self.warning = patch.object(pilot.messagebox, "showwarning")
        self.warning.start()
        self.error = patch.object(pilot.messagebox, "showerror")
        self.error.start()

    def tearDown(self):
        self.app.close()
        self.warning.stop()
        self.error.stop()
        self.environment.stop()
        self.directory.cleanup()

    def begin(self):
        self.app.begin_hotkey_capture()
        # Keep automated tests hidden; interactive UI is checked separately.
        self.app.capture_dialog.withdraw()

    def test_plain_key_is_saved_only_on_release(self):
        self.begin()
        self.app._capture_hotkey(key("k", 0x4B, 0x8))
        self.app.hotkeys.start.assert_not_called()
        self.app._capture_release(key("k", 0x4B, 0x8))
        self.assertEqual(self.app.hotkey["display"], "K")
        self.app.hotkeys.start.assert_called_once_with(0, 0x4B, 0, pilot.VK_F7)
        saved = json.loads(self.app.config_path.read_text())
        self.assertEqual(saved["hotkey"], self.app.hotkey)
        self.assertFalse(self.app.requested)

    def test_escape_keeps_old_key_and_discards_queued_toggle(self):
        old = self.app.hotkey.copy()
        self.app.events.put("toggle")
        self.begin()
        self.app._capture_hotkey(key("Escape", 0x1B))
        self.assertEqual(self.app.hotkey, old)
        self.assertTrue(self.app.events.empty())
        self.assertFalse(self.app.capture_active)

    def test_conflict_rolls_back_and_preserves_settings(self):
        old = self.app.hotkey.copy()
        self.begin()
        self.app.hotkeys.start.side_effect = [False, True]
        self.app._capture_hotkey(key("k", 0x4B))
        self.app._capture_release(key("k", 0x4B))
        self.assertEqual(self.app.hotkey, old)
        self.assertTrue(self.app.hotkeys_ready)

    def test_failed_rollback_prevents_automation(self):
        self.begin()
        self.app.hotkeys.start.return_value = False
        self.app._finish_capture(pilot.make_hotkey(0, 0x4B))
        self.assertFalse(self.app.hotkeys_ready)
        self.app.start()
        self.assertFalse(self.app.requested)

    def test_emergency_key_is_not_assignable(self):
        self.begin()
        self.app._capture_hotkey(key("F7", pilot.VK_F7))
        self.assertIsNone(self.app.capture_candidate)

    def test_capture_focus_loss_keeps_previous_binding(self):
        old = self.app.hotkey.copy()
        self.begin()
        with patch.object(self.root, "focus_get", return_value=None):
            self.app._capture_focus_lost()
        self.assertFalse(self.app.capture_active)
        self.assertEqual(self.app.hotkey, old)

    def test_closed_target_stops_and_releases(self):
        self.app.keys = Mock()
        self.app.keys._user32.IsWindow.return_value = False
        self.app.requested = self.app.active = True
        self.app.target_hwnd = 123
        self.app._tick()
        self.assertFalse(self.app.requested)
        self.app.keys.release_all.assert_called()

    def test_emergency_stop_wins_over_pending_toggle(self):
        self.app.toggle = Mock()
        self.app.events.put("stop")
        self.app.events.put("toggle")
        self.app._tick()
        self.app.toggle.assert_not_called()
        self.assertFalse(self.app.requested)

    def test_refresh_does_not_resume_on_unrelated_window(self):
        self.app.requested = True
        self.app.window_map = {"Previous": 123}
        self.app.window_titles = {"Previous": "Previous"}
        self.app.target_var.set("Previous")
        self.app.start = Mock()
        with patch.object(pilot, "list_target_windows", return_value=[(456, "Unrelated")]):
            self.app.refresh_windows()
        self.app.start.assert_not_called()
        self.assertFalse(self.app.requested)

    def test_timer_expiry_stops_and_releases(self):
        self.app.keys = Mock()
        self.app.keys._user32.IsWindow.return_value = True
        self.app.keys._user32.GetForegroundWindow.return_value = 123
        self.app.target_hwnd = 123
        self.app.requested = self.app.active = True
        self.app.remaining = 0.001
        self.app.last_tick = 0
        self.app._tick()
        self.assertFalse(self.app.requested)
        self.app.keys.release_all.assert_called()

    def test_focus_pause_releases_input(self):
        self.app.keys = Mock()
        self.app.keys._user32.IsWindow.return_value = True
        self.app.keys._user32.GetForegroundWindow.return_value = 456
        self.app.target_hwnd = 123
        self.app.requested = self.app.active = True
        self.app.focus_var.set(True)
        self.app.target_mode_var.set(False)
        self.app._tick()
        self.assertTrue(self.app.requested)
        self.assertFalse(self.app.active)
        self.app.keys.release_all.assert_called_once()

    def test_background_focus_reasserts_once(self):
        self.app.keys = Mock()
        self.app.keys._user32.IsWindow.return_value = True
        self.app.keys._user32.GetForegroundWindow.return_value = 456
        self.app.target_hwnd = self.app.input_target_hwnd = 123
        self.app.target_mode_var.set(True)
        self.app.requested = self.app.active = True
        self.app.last_target_focus = True
        with patch.object(pilot.time, "monotonic", return_value=1.0):
            self.app._tick()
        self.app.keys.reassert_background.assert_not_called()
        with patch.object(pilot.time, "monotonic", return_value=2.0):
            self.app._tick()
            self.app._tick()
        self.app.keys.reassert_background.assert_called_once()

    def test_unexpected_callback_releases_input_and_fails_closed(self):
        self.app.test_mode = False
        self.app.keys.release_all = Mock()
        self.app._callback_failed(RuntimeError, RuntimeError("test"), None)
        self.app.keys.release_all.assert_called_once()
        self.assertFalse(self.app.requested)
        self.assertFalse(self.app.hotkeys_ready)


if __name__ == "__main__":
    unittest.main()
