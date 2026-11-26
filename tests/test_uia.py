"""What the UI Automation script looks for and how it presses (the script itself runs on Windows)."""

import unittest

from sergulath.core import uia


class UiaScriptTest(unittest.TestCase):
    def test_radio_buttons_and_tabs_are_found(self):
        # ASTAROT 0.7.0-beta.2's "one line" / "multi-line" caption choice is a radio group.
        for control in ("Button", "CheckBox", "RadioButton", "TabItem", "Edit"):
            self.assertIn(f"[Windows.Automation.ControlType]::{control}", uia._SCRIPT)

    def test_a_selection_item_is_selected_once(self):
        self.assertIn("SelectionItemPattern", uia._SCRIPT)
        self.assertIn("already selected", uia._SCRIPT)


if __name__ == "__main__":
    unittest.main()
