import tempfile
import unittest
from pathlib import Path

from bot_app.updater import read_new_events


class LifecycleTests(unittest.TestCase):
    def test_invalid_numbers_and_run_identity_are_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "events"
            p.write_text(
                '{"event":"start","version":"bad"}\n{"event":"future","version":2}\n{"event":"start","version":3,"run_id":"actual"}\n'
            )
            events, offset = read_new_events(p, 0)
            self.assertEqual(3, offset)
            self.assertEqual(["future", "start"], [e.event for e in events])
            self.assertEqual("actual", events[-1].run_id)


class IntegrationContracts(unittest.TestCase):
    def test_opt_in_configuration_is_default_off(self):
        from bot_app.config import Config

        self.assertIn("portrait_delivery", Config.__dataclass_fields__)
        self.assertEqual(
            {}, Config.__dataclass_fields__["portrait_delivery"].default_factory()
        )

    def test_watcher_exposes_consumer_handoff(self):
        import inspect

        from bot_app.watcher import Watcher

        self.assertIn("portrait_consumer", inspect.signature(Watcher).parameters)


class PhysicalLineTests(unittest.TestCase):
    def test_oversize_line_does_not_change_physical_offset(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "events"
            p.write_text(
                "x" * 70000 + "\n" + '{"event":"start","version":3,"run_id":"next"}\n'
            )
            events, offset = read_new_events(p, 1)
            self.assertEqual(2, offset)
            self.assertEqual(2, events[0].line_number)


class PageLimitTests(unittest.TestCase):
    def test_default_optin_bound_and_strict_explicit_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "events"
            p.write_text(
                "{}\n" * 128 + '{"event":"start","version":5}\n', encoding="utf-8"
            )
            events, offset = read_new_events(p, 0)
            self.assertEqual(([], 128), (events, offset))
            events, offset = read_new_events(p, 0, limit=None)
            self.assertEqual(129, offset)
            self.assertEqual(129, events[0].line_number)
            for limit in (True, 0, -1, 129, 1.5, "128"):
                with self.assertRaises(ValueError):
                    read_new_events(p, 0, limit=limit)
