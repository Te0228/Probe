"""CLI entry point for the data analyzer.

Usage:
    python -m tests.demo_projects.data_cli.cli <command> [args]
    python cli.py revenue data.csv
    python cli.py bestseller data.csv

The CSV format is: product,quantity,price
"""

import sys
from pathlib import Path

from .processor import calculate_revenue, find_best_seller, parse_sales


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python cli.py <command> <csv_file>")
        print("Commands: revenue, bestseller")
        sys.exit(1)

    command = sys.argv[1]

    if len(sys.argv) >= 3:
        csv_path = Path(sys.argv[2])
        if not csv_path.exists():
            print(f"Error: file not found: {csv_path}")
            sys.exit(1)
        lines = csv_path.read_text().splitlines()
    else:
        lines = sys.stdin.read().splitlines()

    records = parse_sales(lines)

    if command == "revenue":
        revenue = calculate_revenue(records)
        for product, total in sorted(revenue.items()):
            print(f"  {product}: ${total:.2f}")

    elif command == "bestseller":
        revenue = calculate_revenue(records)
        # BUG: if records is empty, this crashes
        best = find_best_seller(revenue)
        print(f"Best seller: {best} (${revenue[best]:.2f})")

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
