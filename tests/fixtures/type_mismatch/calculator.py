"""Calculator module with a deliberate type-mismatch bug.

The 'add' function accepts two arguments but does not validate types.
When strings are passed (e.g., from parsed input), it returns a concatenated
string instead of raising TypeError or converting to int. The calling code
then tries to compare the result with an int, causing a TypeError.

Bug: comparing str to int after add() returns a string because of
      unintentional string concatenation.
"""


def add(a, b):
    """Add two numbers. Bug: does not cast to int, so strings concatenate."""
    return a + b


def calculate_total(items):
    """Sum a list of values. Bug: if items are strings, this silently
    concatenates instead of converting to int first."""
    total = 0
    for item in items:
        total = add(total, item)  # total is int, item might be str
    return total


def is_valid_total(total):
    """Check if total is within a valid range. Bug: total could be a string."""
    if total > 0:  # TypeError if total is str
        return True
    return False
