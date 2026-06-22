#!/usr/bin/env python3
import unittest

import tui


class TuiScrollTests(unittest.TestCase):
    def test_clamp_scroll_bounds_offset(self):
        self.assertEqual(tui.clamp_scroll(-5, 20, 10), 0)
        self.assertEqual(tui.clamp_scroll(5, 20, 10), 5)
        self.assertEqual(tui.clamp_scroll(50, 20, 10), 10)

    def test_clamp_scroll_handles_short_content(self):
        self.assertEqual(tui.clamp_scroll(3, 5, 10), 0)
        self.assertEqual(tui.clamp_scroll(3, 5, 0), 3)

    def test_page_step_keeps_one_line_overlap(self):
        self.assertEqual(tui.page_step(1), 1)
        self.assertEqual(tui.page_step(10), 9)


if __name__ == "__main__":
    unittest.main()
