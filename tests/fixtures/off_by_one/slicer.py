"""Slicer module — extracts slice from a list.

Bug: get_slice uses `end = start + count - 1` instead of `end = start + count`,
causing the function to return one fewer item than requested.
"""


def get_slice(items, start, count):
    """Return up to `count` items starting from index `start`.

    Args:
        items: List of items.
        start: Starting index (inclusive).
        count: Number of items to return.

    BUG: off-by-one — computes end as start + count - 1 instead of start + count,
    so the returned slice always has one fewer item than requested.
    """
    end = start + count - 1  # BUG: should be start + count
    result = []
    for i in range(start, end):
        if i < len(items):
            result.append(items[i])
    return result


def get_middle_three(items):
    """Return the three middle items from a list.

    BUG: Because get_slice has an off-by-one error, this returns only 2 items
    instead of 3 from a 5-item list.
    """
    if len(items) < 3:
        return []
    mid = len(items) // 2
    return get_slice(items, mid - 1, 3)
