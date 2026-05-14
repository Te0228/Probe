"""Test for parser module — fails due to import error."""

import pytest


def test_parse_record():
    """BUG: parser module has a faulty import from non-existent 'formatter',
    so importing it raises ImportError."""
    try:
        from parser import parse_record
    except ImportError as e:
        pytest.fail(f"ImportError: {e}")

    # If we somehow got here, the parse should work
    result = parse_record("name:Alice")
    assert result is not None
