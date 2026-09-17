from datetime import datetime

from oob_monitor import compute_next_scheduled_run, _parse_hhmm


def test_parse_hhmm_valid():
    assert _parse_hhmm("13:45") == (13, 45)


def test_parse_hhmm_invalid_falls_back_to_default():
    assert _parse_hhmm("not-a-time") == (1, 0)
    assert _parse_hhmm("25:99") == (1, 0)  # gio/phut ngoai khoang hop le


def test_daily_schedule_later_today():
    now = datetime(2026, 1, 5, 8, 0, 0)  # thu 2
    result = compute_next_scheduled_run("daily", "10:00", "mon", now=now)
    assert result == datetime(2026, 1, 5, 10, 0, 0)


def test_daily_schedule_already_passed_rolls_to_tomorrow():
    now = datetime(2026, 1, 5, 12, 0, 0)
    result = compute_next_scheduled_run("daily", "10:00", "mon", now=now)
    assert result == datetime(2026, 1, 6, 10, 0, 0)


def test_weekly_schedule_same_day_later():
    now = datetime(2026, 1, 5, 8, 0, 0)  # thu 2 = mon
    result = compute_next_scheduled_run("weekly", "10:00", "mon", now=now)
    assert result == datetime(2026, 1, 5, 10, 0, 0)


def test_weekly_schedule_same_day_already_passed_rolls_to_next_week():
    now = datetime(2026, 1, 5, 12, 0, 0)  # thu 2
    result = compute_next_scheduled_run("weekly", "10:00", "mon", now=now)
    assert result == datetime(2026, 1, 12, 10, 0, 0)


def test_weekly_schedule_future_weekday_this_week():
    now = datetime(2026, 1, 5, 8, 0, 0)  # thu 2 (mon)
    result = compute_next_scheduled_run("weekly", "10:00", "wed", now=now)
    assert result == datetime(2026, 1, 7, 10, 0, 0)


def test_weekly_schedule_defaults_to_monday_on_invalid_weekday():
    now = datetime(2026, 1, 5, 8, 0, 0)  # thu 2
    result = compute_next_scheduled_run("weekly", "10:00", "not-a-day", now=now)
    assert result == datetime(2026, 1, 5, 10, 0, 0)
