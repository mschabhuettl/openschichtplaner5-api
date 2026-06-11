"""Unit tests for the time-overlap primitives in schedule.py that back the
overlapping-shift conflict detection (Befund D9): _startend_windows,
_shift_time_windows (per day index 0..7, no STARTEND0 fallback) and
_windows_overlap — all built on calc.parse_startend."""

import sp5api.routers.schedule as sched


class TestStartendWindows:
    def test_basic(self):
        assert sched._startend_windows("08:00-16:00") == [(480, 960)]

    def test_overnight_wraps(self):
        # end <= start → +24h (D-30)
        assert sched._startend_windows("22:00-06:00") == [(1320, 1800)]

    def test_multi_window(self):
        # bis zu drei Teilfenster (3.8.3 Nr. 10)
        assert sched._startend_windows("06:00-10:00 12:00-16:00") == [
            (360, 600),
            (720, 960),
        ]

    def test_empty_or_unparseable(self):
        assert sched._startend_windows("") == []
        assert sched._startend_windows("0800 1600") == []
        assert sched._startend_windows("ab:cd-ef:gh") == []

    def test_zero_window_means_undefined(self):
        assert sched._startend_windows("00:00-00:00") == []


class TestWindowsOverlap:
    def test_overlapping(self):
        assert sched._windows_overlap([(480, 960)], [(900, 1000)]) is True

    def test_adjacent_not_overlapping(self):
        assert sched._windows_overlap([(480, 960)], [(960, 1000)]) is False

    def test_empty_never_overlaps(self):
        assert sched._windows_overlap([], [(480, 960)]) is False
        assert sched._windows_overlap([(480, 960)], []) is False

    def test_any_pair_counts(self):
        assert sched._windows_overlap(
            [(360, 600), (720, 960)], [(600, 720), (950, 1000)]
        ) is True


class TestShiftTimeWindows:
    def test_day_index_specific(self):
        shift = {"STARTEND0": "08:00-16:00", "STARTEND2": "14:00-22:00"}
        assert sched._shift_time_windows(shift, 2) == [(840, 1320)]

    def test_no_fallback_to_startend0(self):
        # leerer Tagesslot = keine Zeiten definiert (3.4.3 Nr. 6) — kein Fallback
        assert sched._shift_time_windows({"STARTEND0": "08:00-16:00"}, 3) == []

    def test_holiday_index_7(self):
        shift = {"STARTEND0": "08:00-16:00", "STARTEND7": "09:00-13:00"}
        assert sched._shift_time_windows(shift, 7) == [(540, 780)]

    def test_no_time_data(self):
        assert sched._shift_time_windows({}, 0) == []
