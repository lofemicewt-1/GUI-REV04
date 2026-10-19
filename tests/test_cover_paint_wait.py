from __future__ import annotations

import inspect
import unittest

from app import transitions
from app.navigation import Navigation


class CoverPaintWaitTests(unittest.TestCase):
    def test_wait_cap_is_at_most_350ms_and_cover_remains(self) -> None:
        self.assertLessEqual(transitions.COVER_PAINT_WAIT_S, 0.35)
        self.assertGreater(transitions.COVER_PAINT_WAIT_S, 0.0)
        self.assertTrue(callable(transitions.SnapshotTransition.cover))
        reveal = inspect.getsource(transitions.SnapshotTransition.reveal)
        self.assertIn("COVER_PAINT_WAIT_S", reveal)
        self.assertNotIn("10.0", reveal)

    def test_request_show_does_not_wait_dur_fast(self) -> None:
        source = inspect.getsource(Navigation.request_show)
        self.assertNotIn("DUR_FAST", source)
        self.assertIn("after(0, self._commit_navigation)", source)


if __name__ == "__main__":
    unittest.main()
