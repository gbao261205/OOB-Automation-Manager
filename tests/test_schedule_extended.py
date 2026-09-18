"""Test cho WP-H #17: mo rong compute_next_scheduled_run() voi mode
'monthly' va 'once'."""

from datetime import datetime

from oob_monitor import compute_next_scheduled_run, _last_day_of_month


def test_monthly_later_this_month():
    now = datetime(2026, 3, 5, 8, 0, 0)
    result = compute_next_scheduled_run("monthly", "10:00", "mon", now=now, day_of_month=15)
    assert result == datetime(2026, 3, 15, 10, 0, 0)


def test_monthly_already_passed_this_month_rolls_to_next_month():
    now = datetime(2026, 3, 20, 8, 0, 0)
    result = compute_next_scheduled_run("monthly", "10:00", "mon", now=now, day_of_month=15)
    assert result == datetime(2026, 4, 15, 10, 0, 0)


def test_monthly_clamps_to_last_day_when_month_too_short():
    # Thang 2/2026 (khong nhuan) chi co 28 ngay - chon ngay 31 phai clamp ve 28
    now = datetime(2026, 2, 1, 0, 0, 0)
    result = compute_next_scheduled_run("monthly", "10:00", "mon", now=now, day_of_month=31)
    assert result == datetime(2026, 2, 28, 10, 0, 0)


def test_monthly_december_rolls_over_to_january_next_year():
    now = datetime(2026, 12, 20, 8, 0, 0)
    result = compute_next_scheduled_run("monthly", "10:00", "mon", now=now, day_of_month=15)
    assert result == datetime(2027, 1, 15, 10, 0, 0)


def test_monthly_missing_day_of_month_defaults_to_1():
    now = datetime(2026, 3, 5, 8, 0, 0)
    result = compute_next_scheduled_run("monthly", "10:00", "mon", now=now, day_of_month=None)
    assert result == datetime(2026, 4, 1, 10, 0, 0)


def test_once_future_datetime_returned_as_is():
    now = datetime(2026, 3, 5, 8, 0, 0)
    result = compute_next_scheduled_run("once", "01:00", "mon", now=now, once_datetime="2026-03-10 14:30")
    assert result == datetime(2026, 3, 10, 14, 30, 0)


def test_once_past_datetime_returns_none():
    now = datetime(2026, 3, 5, 8, 0, 0)
    result = compute_next_scheduled_run("once", "01:00", "mon", now=now, once_datetime="2026-01-01 00:00")
    assert result is None


def test_once_unset_returns_none():
    now = datetime(2026, 3, 5, 8, 0, 0)
    assert compute_next_scheduled_run("once", "01:00", "mon", now=now, once_datetime=None) is None
    assert compute_next_scheduled_run("once", "01:00", "mon", now=now, once_datetime="") is None


def test_once_malformed_string_returns_none():
    now = datetime(2026, 3, 5, 8, 0, 0)
    result = compute_next_scheduled_run("once", "01:00", "mon", now=now, once_datetime="not-a-date")
    assert result is None


def test_once_accepts_datetime_object_directly():
    now = datetime(2026, 3, 5, 8, 0, 0)
    target = datetime(2026, 3, 6, 9, 0, 0)
    result = compute_next_scheduled_run("once", "01:00", "mon", now=now, once_datetime=target)
    assert result == target


def test_last_day_of_month_leap_year():
    assert _last_day_of_month(2028, 2) == 29  # 2028 la nam nhuan
    assert _last_day_of_month(2026, 2) == 28
    assert _last_day_of_month(2026, 4) == 30


def test_existing_daily_weekly_interval_modes_unaffected():
    now = datetime(2026, 3, 5, 8, 0, 0)  # thu 5
    assert compute_next_scheduled_run("daily", "10:00", "mon", now=now) == datetime(2026, 3, 5, 10, 0, 0)
    assert compute_next_scheduled_run("interval", "10:00", "mon", now=now) is not None
