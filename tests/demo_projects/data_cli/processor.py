"""Core data processing logic."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class SaleRecord:
    product: str
    quantity: int
    price: float


def parse_sales(lines: list[str]) -> list[SaleRecord]:
    """Parse CSV lines into SaleRecord objects."""
    records = []
    for line in lines:
        if line.strip() and not line.startswith("#"):
            parts = line.strip().split(",")
            if len(parts) >= 3:
                records.append(SaleRecord(
                    product=parts[0].strip(),
                    quantity=int(parts[1].strip()),
                    price=float(parts[2].strip()),
                ))
    return records


def calculate_revenue(records: list[SaleRecord]) -> dict[str, float]:
    """Calculate total revenue per product."""
    revenue: dict[str, float] = {}
    for r in records:
        revenue[r.product] = revenue.get(r.product, 0) + r.quantity * r.price
    return revenue


def find_best_seller(revenue: dict[str, float]) -> str:
    """Return the product with the highest revenue.

    BUG: if revenue is empty, max() raises ValueError instead of returning None.
    """
    return max(revenue, key=revenue.get)  # BUG: empty dict → ValueError


def calculate_average_price(records: list[SaleRecord]) -> float:
    """Calculate average price across all products.

    BUG: uses count of distinct products instead of total items, giving
    wrong average when products have different quantities.
    """
    total = sum(r.price for r in records)
    # BUG: should divide by sum of quantities, not number of records
    return total / len(records)
