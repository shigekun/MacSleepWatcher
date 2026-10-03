#!/usr/bin/env python3
"""sleepwatch のパースと帯描画のテスト。"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

import sleepwatch

SAMPLE = """\
2026-10-03 07:00:14 +0900 Notification         \x1b[01;31m\x1b[KDisplay is turned\x1b[m\x1b[K off
2026-10-03 07:00:20 +0900 Sleep               \tEntering Sleep state due to 'Idle Sleep':TCPKeepAlive=active Using AC (Charge:80%) 948 secs
2026-10-03 07:00:21 +0900 Wake Requests       \t[process=mDNSResponder request=Maintenance deltaSecs=7198 wakeAt=2026-10-03 09:00:20]
2026-10-03 07:16:08 +0900 DarkWake            \tDarkWake from Deep Idle [CDNPB] : due to rtc/Maintenance Using AC (Charge:80%) 3573 secs
2026-10-03 08:15:32 +0900 Notification         Display is turned on
2026-10-03 08:15:41 +0900 Wake                 DarkWake to FullWake from Deep Idle [CDNVA] : due to UserActivity Assertion Using AC (Charge:80%) 646 secs
noise without a timestamp
"""


class ParseTests(unittest.TestCase):
    def test_parse_strips_ansi_and_drops_darkwake(self) -> None:
        events = sleepwatch.parse_events(SAMPLE)
        self.assertEqual(
            [(event.ts, event.kind) for event in events],
            [
                (datetime(2026, 10, 3, 7, 0, 14), "display_off"),
                (datetime(2026, 10, 3, 7, 0, 20), "sleep"),
                (datetime(2026, 10, 3, 8, 15, 32), "display_on"),
                (datetime(2026, 10, 3, 8, 15, 41), "wake"),
            ],
        )

    def test_wake_requests_and_darkwake_do_not_end_sleep(self) -> None:
        events = sleepwatch.parse_events(SAMPLE)
        self.assertNotIn("wake", [event.kind for event in events[:3]])
        power = sleepwatch.power_changes(events)
        during = datetime(2026, 10, 3, 7, 30)
        self.assertEqual(
            sleepwatch.dominant(power, during, during + timedelta(seconds=1), "asleep", "awake"),
            "asleep",
        )

    def test_entering_darkwake_is_not_sleep(self) -> None:
        text = "2026-09-28 12:18:13 +0900 Sleep               \tEntering DarkWake state due to 'Idle Sleep'\n"
        self.assertEqual(sleepwatch.parse_events(text), [])

    def test_darkwake_stays_asleep_until_full_wake(self) -> None:
        events = sleepwatch.parse_events(SAMPLE)
        power = sleepwatch.power_changes(events)
        display = sleepwatch.display_changes(events)
        during_darkwake = datetime(2026, 10, 3, 7, 30)
        after_display_on = datetime(2026, 10, 3, 8, 15, 35)
        after_wake = datetime(2026, 10, 3, 8, 16)
        self.assertEqual(sleepwatch.dominant(power, during_darkwake, during_darkwake + timedelta(seconds=1), "asleep", "awake"), "asleep")
        self.assertEqual(sleepwatch.dominant(display, after_display_on, after_display_on + timedelta(seconds=1), "off", "on"), "on")
        self.assertEqual(sleepwatch.dominant(power, after_display_on, after_display_on + timedelta(seconds=1), "asleep", "awake"), "asleep")
        self.assertEqual(sleepwatch.dominant(power, after_wake, after_wake + timedelta(seconds=1), "asleep", "awake"), "awake")


class IntervalTests(unittest.TestCase):
    def test_short_sleep_fills_its_cell_without_inflating_the_total(self) -> None:
        events = [
            sleepwatch.Event(datetime(2026, 10, 3, 3, 0, 0), "sleep"),
            sleepwatch.Event(datetime(2026, 10, 3, 3, 0, 8), "wake"),
        ]
        power = sleepwatch.power_changes(events)
        now = datetime(2026, 10, 4)
        states = sleepwatch.bar_states(power, date(2026, 10, 3), 24, "asleep", "awake", now)
        self.assertEqual(states[2], "unknown")
        self.assertEqual(states[3], "asleep")
        self.assertEqual(states[4], "awake")
        start, end = sleepwatch.day_window(date(2026, 10, 3), now)
        self.assertEqual(sleepwatch.covered_seconds(power, start, end, "asleep"), 8)

    def test_sleep_crossing_midnight_splits_by_day(self) -> None:
        events = [
            sleepwatch.Event(datetime(2026, 10, 3, 23, 30), "sleep"),
            sleepwatch.Event(datetime(2026, 10, 4, 0, 30), "wake"),
        ]
        power = sleepwatch.power_changes(events)
        now = datetime(2026, 10, 4, 12)
        day3 = sleepwatch.bar_states(power, date(2026, 10, 3), 24, "asleep", "awake", now)
        day4 = sleepwatch.bar_states(power, date(2026, 10, 4), 24, "asleep", "awake", now)
        self.assertEqual(day3[23], "asleep")
        self.assertEqual(day4[0], "asleep")
        self.assertEqual(day4[1], "awake")
        start3, end3 = sleepwatch.day_window(date(2026, 10, 3), now)
        start4, end4 = sleepwatch.day_window(date(2026, 10, 4), now)
        self.assertEqual(sleepwatch.covered_seconds(power, start3, end3, "asleep"), 30 * 60)
        self.assertEqual(sleepwatch.covered_seconds(power, start4, end4, "asleep"), 30 * 60)

    def test_today_does_not_draw_the_future(self) -> None:
        events = [
            sleepwatch.Event(datetime(2026, 10, 3, 1, 0), "display_off"),
            sleepwatch.Event(datetime(2026, 10, 3, 2, 0), "display_on"),
        ]
        text = sleepwatch.render(
            events,
            [date(2026, 10, 3)],
            width=30,
            color=False,
            now=datetime(2026, 10, 3, 5, 0),
        )
        self.assertIn("2026-10-03（土）  画面OFF 1.0h  スリープ 0.0h", text)
        self.assertIn("      00   06    12    18   24", text)
        self.assertIn("画面  ·░███" + " " * 19, text)
        self.assertIn("電源  ·····" + " " * 19, text)
        self.assertNotIn("\033[", text)

    def test_color_marks_display_on_and_awake(self) -> None:
        events = sleepwatch.parse_events(SAMPLE)
        text = sleepwatch.render(
            events,
            [date(2026, 10, 3)],
            width=30,
            color=True,
            now=datetime(2026, 10, 3, 12, 0),
        )
        self.assertIn("\033[33m█", text)
        self.assertIn("\033[36m█", text)


class AxisTests(unittest.TestCase):
    def test_narrow_axis_keeps_endpoints(self) -> None:
        self.assertEqual(sleepwatch.render_axis(24), "00   06    12    18   24")
        axis = sleepwatch.render_axis(72)
        self.assertTrue(axis.startswith("00"))
        self.assertTrue(axis.endswith("24"))
        self.assertEqual(len(axis), 72)
        self.assertIn("12", axis)


if __name__ == "__main__":
    unittest.main()
