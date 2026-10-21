from __future__ import annotations

import inspect
import unittest
from unittest.mock import Mock, patch

from app import transitions
from app.navigation import Navigation


class CoverPaintWaitTests(unittest.TestCase):
    def test_wait_budget_is_at_most_350ms_and_cover_remains(self) -> None:
        self.assertLessEqual(transitions.COVER_PAINT_WAIT_S, 0.35)
        self.assertGreater(transitions.COVER_PAINT_WAIT_S, 0.0)
        self.assertTrue(callable(transitions.SnapshotTransition.cover))
        reveal = inspect.getsource(transitions.SnapshotTransition.reveal)
        self.assertIn("COVER_PAINT_WAIT_S", reveal)
        self.assertNotIn("10.0", reveal)

    def make_active_transition(self):
        root = Mock()
        root.bindtags.return_value = ()
        root.winfo_viewable.return_value = True
        transition = transitions.SnapshotTransition(root, Mock())
        transition.active = True
        transition._paint_activity = 10.0
        transition._owner_is_foreground = Mock(return_value=True)
        return transition

    def test_late_first_ready_quiet_check_still_starts_fade(self) -> None:
        transition = self.make_active_transition()
        with patch.object(transitions.time, "perf_counter", return_value=10.0) as clock, \
                patch.object(transitions, "_native_paint_pending", return_value=False), \
                patch.object(transitions, "_dwm_flush"):
            transition.reveal(120, ready=lambda: True)
            clock.return_value = 10.0 + transitions.COVER_PAINT_WAIT_S + .1
            transition.root.after.call_args.args[1]()
            self.assertTrue(transition.active)
            transition.animator.animate.assert_not_called()
            self.assertEqual(transition.root.after.call_count, 2)

            clock.return_value += transitions.anim.FRAME_MS / 1000
            transition.root.after.call_args.args[1]()
            transition.animator.animate.assert_called_once()
            self.assertEqual(transition.animator.animate.call_args.args[:2],
                             (transition._key, 120))
            self.assertEqual(transition.animator.animate.call_args.kwargs["done"],
                             transition.finish)

    def test_pending_paint_past_hard_cap_finishes(self) -> None:
        transition = self.make_active_transition()
        with patch.object(transitions.time, "perf_counter", return_value=10.0) as clock, \
                patch.object(transitions, "_native_paint_pending", return_value=True), \
                patch.object(transitions, "_request_native_paint") as request_paint:
            transition.reveal(120, ready=lambda: True)
            clock.return_value = 10.0 + transitions.COVER_PAINT_WAIT_S + .1
            transition.root.after.call_args.args[1]()
            self.assertTrue(transition.active)
            request_paint.assert_called_once_with(transition.root)

            clock.return_value = 10.0 + transitions.COVER_PAINT_WAIT_S + 1.01
            transition.root.after.call_args.args[1]()
            self.assertFalse(transition.active)
            self.assertIsNone(transition._job)
            self.assertEqual(transition.root.after.call_count, 2)
            transition.animator.cancel.assert_called_once_with(transition._key)
            transition.animator.animate.assert_not_called()

    def test_request_show_does_not_wait_dur_fast(self) -> None:
        source = inspect.getsource(Navigation.request_show)
        self.assertNotIn("DUR_FAST", source)
        self.assertIn("after(0, self._commit_navigation)", source)


if __name__ == "__main__":
    unittest.main()
