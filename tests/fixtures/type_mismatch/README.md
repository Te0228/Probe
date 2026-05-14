# Type Mismatch Bug Fixture

A calculator module with a deliberate type-mismatch bug.

## The Bug

The `add()` function does not validate or cast its arguments. When a string
is passed (e.g., from parsed external input), Python's `+` operator performs
string concatenation instead of numeric addition.

### Reproduction

```
cd tests/fixtures/type_mismatch
pytest test_calculator.py
```

Expected: Tests pass
Actual: TypeError on `total > 0` because `total` is a string like "0123"

### Root Cause

`calculate_total()` calls `add()` with a list that can contain strings.
Since `add()` does `a + b` without type coercion, mixing int (0) with str
("1") produces str concatenation: "0" + "1" = "01", then "01" + "2" = "012",
then "012" + "3" = "0123". The resulting string "0123" is then compared
with int 0 in `is_valid_total()`, raising TypeError.

### Fix

In `add()`, cast both arguments to int:
```python
def add(a, b):
    return int(a) + int(b)
```

Or in `calculate_total()`, cast items to int before summing:
```python
total = add(total, int(item))
```
