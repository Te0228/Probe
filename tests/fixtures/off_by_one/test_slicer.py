"""Test for slicer module — fails due to off-by-one error."""

from slicer import get_slice, get_middle_three


def test_get_slice_returns_correct_count():
    """get_slice should return exactly `count` items."""
    items = [10, 20, 30, 40, 50]
    result = get_slice(items, 1, 3)
    assert result == [20, 30, 40]


def test_get_middle_three_returns_three_items():
    """BUG: get_middle_three should return 3 items, but off-by-one
    causes it to return only 2."""
    items = [1, 2, 3, 4, 5]
    result = get_middle_three(items)
    assert len(result) == 3
    assert result == [2, 3, 4]
