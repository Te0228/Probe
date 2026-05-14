"""Parser module — parses input data.

Bug: Imports from a non-existent module 'formatter', causing ImportError
when the module is loaded.
"""

from common import normalize_string
from formatter import format_record  # BUG: formatter module does not exist


def parse_record(data):
    """Parse a record string and format it.

    BUG: Raises ImportError because 'formatter' module does not exist.
    """
    key, value = normalize_string(data).split(":", 1)
    return format_record(key, value)
