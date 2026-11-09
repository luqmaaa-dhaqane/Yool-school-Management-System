"""CSV helpers."""


def safe_cell(value):
    """Neutralise spreadsheet formula injection (a name like '=HYPERLINK(...)')."""
    text = "" if value is None else str(value)
    if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def safe_row(values):
    return [safe_cell(v) for v in values]
