"""Unit tests for the time-overlap primitives in schedule.py that back the
overlapping-shift conflict detection: _parse_time_range, _times_overlap and
_get_shift_time_range (weekday-specific with STARTEND0 fallback)."""

import api.routers.schedule as sched


class TestParseTimeRange:
    def test_basic(self):
        assert sched._parse_time_range("08:00-16:00") == (480, 960)

    def test_overnight_wraps(self):
        # end <= start → +24h
        assert sched._parse_time_range("22:00-06:00") == (1320, 1800)

    def test_empty_or_no_dash(self):
        assert sched._parse_time_range("") is None
        assert sched._parse_time_range("0800 1600") is None

    def test_too_many_parts(self):
        assert sched._parse_time_range("08:00-16:00-20:00") is None

    def test_non_numeric(self):
        assert sched._parse_time_range("ab:cd-ef:gh") is None


class TestTimesOverlap:
    def test_overlapping(self):
        assert sched._times_overlap((480, 960), (900, 1000)) is True

    def test_adjacent_not_overlapping(self):
        assert sched._times_overlap((480, 960), (960, 1000)) is False

    def test_none_never_overlaps(self):
        assert sched._times_overlap(None, (480, 960)) is False
        assert sched._times_overlap((480, 960), None) is False


class TestGetShiftTimeRange:
    def test_weekday_specific_wins(self):
        shift = {"STARTEND0": "08:00-16:00", "STARTEND2": "14:00-22:00"}
        assert sched._get_shift_time_range(shift, 2) == (840, 1320)

    def test_falls_back_to_startend0(self):
        # weekday 3 has no STARTEND3 → fall back to STARTEND0
        assert sched._get_shift_time_range({"STARTEND0": "08:00-16:00"}, 3) == (480, 960)

    def test_no_time_data(self):
        assert sched._get_shift_time_range({}, 0) is None
