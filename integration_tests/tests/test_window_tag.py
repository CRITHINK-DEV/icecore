"""column_anomaly's window_tag reaches SQL as a literal, so only plain tags are accepted (SQL safety rule)."""
import pytest

from helpers.macros import MacroFailed, run_macro


@pytest.mark.parametrize("tag", [None, "orders_daily", "A1", "x" * 64])
def test_plain_window_tags_are_accepted(tag):
    assert run_macro("it_validate_window_tag", {"tag": tag}) is True


@pytest.mark.parametrize("tag", ["x'; drop table t; --", "a$$b", "a\nb", "with space", "", "x" * 65])
def test_other_window_tags_fail_at_compile_time(tag):
    with pytest.raises(MacroFailed, match="window_tag must be"):
        run_macro("it_validate_window_tag", {"tag": tag})
