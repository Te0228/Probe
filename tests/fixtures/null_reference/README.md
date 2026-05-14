# null_reference

Bug: A function returns `None` under certain conditions, but the caller does not check for `None` before accessing an attribute, causing `AttributeError`.

Bug type: `None`/`AttributeError`
