"""Finder module — look up items in a nested structure.

Bug: get_user_name returns None when the key is missing, but the caller
tries to call .upper() on the result without a None check.
"""


def get_setting(settings_dict, key):
    """Look up a key in a settings dict. Returns None if missing."""
    return settings_dict.get(key)


def get_user_name(users, user_id):
    """Look up a user by ID. Returns the user dict or None."""
    for user in users:
        if user.get("id") == user_id:
            return user
    return None


def format_display_name(user):
    """BUG: Calls .upper() on user["name"] without checking if user is None."""
    return user["name"].upper()


def get_and_format_name(users, user_id):
    """Public API: get a user and format their display name.

    BUG: When user_id doesn't exist, get_user_name returns None and
    format_display_name raises AttributeError.
    """
    user = get_user_name(users, user_id)
    return format_display_name(user)
