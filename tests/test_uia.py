"""What the UI Automation script looks for and how it presses (the script itself runs on Windows)."""

import unittest

from sergulath import profiles
from sergulath.commands import app as app_command
from sergulath.core import uia


class UiaScriptTest(unittest.TestCase):
    def test_radio_buttons_and_tabs_are_found(self):
        # ASTAROT 0.7.0-beta.2's "one line" / "multi-line" caption choice is a radio group.
        for control in ("Button", "CheckBox", "RadioButton", "TabItem", "Edit"):
            self.assertIn(f"[Windows.Automation.ControlType]::{control}", uia._SCRIPT)

    def test_a_selection_item_is_selected_once(self):
        self.assertIn("SelectionItemPattern", uia._SCRIPT)
        self.assertIn("already selected", uia._SCRIPT)

    def test_each_step_says_which_window_it_used(self):
        self.assertIn("window = $role", uia._SCRIPT)


class WindowsTest(unittest.TestCase):
    def test_the_main_window_comes_first_then_the_settings_window(self):
        # ASTAROT 0.7.0-beta.3 shows its settings in a window of their own (#1236).
        windows = uia.windows_for(profiles.load("astarot")["app"])
        self.assertEqual(windows[0], {"title": "ASTAROT", "role": "main"})
        self.assertIn({"title": "ASTAROTの設定", "role": "settings"}, windows[1:])

    def test_a_profile_without_other_windows_has_the_main_one_only(self):
        self.assertEqual(uia.windows_for({"main_window_title": "X"}), [{"title": "X", "role": "main"}])


class HomeStepsTest(unittest.TestCase):
    def test_home_closes_the_settings_window_before_checking_the_main_page(self):
        labels = profiles.load("astarot")["ui"]["ja"]
        names = [step.name for step in app_command.home_steps(labels)]
        self.assertLess(names.index("設定を閉じる"), names.index("設定を開く"))
        self.assertIn("戻る", names)  # still there for 0.7.0-beta.2 and earlier
        self.assertTrue(all(step.optional for step in app_command.home_steps(labels)[:-1]))


if __name__ == "__main__":
    unittest.main()
