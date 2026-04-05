from pathlib import Path

from core.trading_day import load_manual_holiday_set, should_run_bot_today_kst
from types import SimpleNamespace


def _settings_with_holidays(tmp_path: Path, content: str) -> SimpleNamespace:
    p = tmp_path / "h.txt"
    p.write_text(content, encoding="utf-8")
    return SimpleNamespace(holiday_dates_path=p)


def test_weekend_saturday_blocked_without_file(tmp_path: Path):
    s = _settings_with_holidays(tmp_path, "")
    (tmp_path / "h.txt").unlink()
    ok, msg = should_run_bot_today_kst("20260404", s)
    assert ok is False
    assert "휴장일" in msg


def test_weekday_in_list_blocked(tmp_path: Path):
    s = _settings_with_holidays(tmp_path, "20250402\n")
    ok, msg = should_run_bot_today_kst("20250402", s)
    assert ok is False


def test_weekday_not_in_list_runs(tmp_path: Path):
    s = _settings_with_holidays(tmp_path, "20250402\n")
    ok, msg = should_run_bot_today_kst("20250403", s)
    assert ok is True
    assert msg == ""


def test_comments_and_blank_skipped(tmp_path: Path):
    s = _settings_with_holidays(
        tmp_path,
        "# comment\n\n20250402\n  20250403  \n",
    )
    h = load_manual_holiday_set(s.holiday_dates_path)
    assert h == {"20250402", "20250403"}


def test_sunday_blocked_even_if_in_file(tmp_path: Path):
    s = _settings_with_holidays(tmp_path, "")
    ok, msg = should_run_bot_today_kst("20260405", s)
    assert ok is False
