"""Test for pricing module — fails due to wrong return value."""

from pricing import apply_bulk_discount


def test_bulk_discount_applied_at_threshold():
    """BUG: Discount should apply when quantity equals the threshold (10),
    but it does not because > is used instead of >=."""
    # 10 items at $5 each = $50, minus 15% = $42.50
    result = apply_bulk_discount(5.0, 10)
    assert result == 42.50


def test_bulk_discount_not_applied_below_threshold():
    """Small order should not get discount."""
    result = apply_bulk_discount(5.0, 5)
    assert result == 25.0
