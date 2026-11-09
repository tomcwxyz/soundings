from soundings.adapters.base import _cron_to_window_days


def test_quarterly_month_list_is_not_treated_as_annual() -> None:
    assert _cron_to_window_days("0 4 20 2,5,8,11 *") == 90


def test_fixed_annual_month_remains_annual() -> None:
    assert _cron_to_window_days("0 2 1 7 *") == 365
