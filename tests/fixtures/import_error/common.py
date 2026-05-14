"""Common utility module — used by other modules in the package."""


def normalize_string(value):
    """Normalize a string by lowering and stripping."""
    return value.lower().strip()
