"""Pricing module — calculate discounted prices.

Bug: apply_bulk_discount returns the original price instead of the
discounted price when the quantity exactly matches the threshold.
"""


def apply_bulk_discount(unit_price, quantity):
    """Apply a bulk discount for large orders.

    BUG: The condition uses `>` instead of `>=`, so when quantity exactly
    equals the threshold (10), no discount is applied. The function returns
    the full total instead of the discounted total.
    """
    total = unit_price * quantity
    if quantity > 10:  # BUG: should be >= 10
        discount_rate = 0.15
        total = total * (1 - discount_rate)
    return total


def get_price_breakdown(unit_price, quantity):
    """Return a breakdown string for the customer."""
    final = apply_bulk_discount(unit_price, quantity)
    formatted = f"${final:.2f}"
    return formatted
