"""Test for calculator module — fails due to type mismatch bug."""

import pytest
from calculator import add, calculate_total, is_valid_total


def test_add_with_ints():
    """Basic addition works."""
    assert add(2, 3) == 5


def test_calculate_total_with_mixed_types():
    """calculate_total should handle mixed types correctly.

    BUG: When any item is a string, add() does string concatenation
    instead of numeric addition, producing a string result.  The subsequent
    is_valid_total() call then compares str > int, raising TypeError.
    """
    items = [1, 2, "3"]
    total = calculate_total(items)  # Returns "0123" instead of 6
    # This will fail: total > 0 with str and int
    assert is_valid_total(total) is True


def test_is_valid_total_with_string():
    """is_valid_total should throw TypeError or return False for non-int input."""
    with pytest.raises(TypeError):
        is_valid_total("hello")
