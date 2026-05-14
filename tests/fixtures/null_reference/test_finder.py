"""Test for finder module — fails due to None/AttributeError bug."""

import pytest
from finder import get_and_format_name


def test_get_and_format_name_existing_user():
    """Existing user should be formatted correctly."""
    users = [
        {"id": 1, "name": "Alice"},
        {"id": 2, "name": "Bob"},
    ]
    result = get_and_format_name(users, 1)
    assert result == "ALICE"


def test_get_and_format_name_missing_user():
    """BUG: Missing user should be handled gracefully, but raises AttributeError."""
    users = [
        {"id": 1, "name": "Alice"},
    ]
    result = get_and_format_name(users, 999)
    assert result is None
