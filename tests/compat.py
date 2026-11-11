"""Ayudas para que los tests funcionen igual con FastMCP 3.x y 4.x."""


def is_read_only(tool) -> bool:
    """FastMCP 4 expone `read_only_hint`; la 3.x, `readOnlyHint`."""
    ann = tool.annotations
    if ann is None:
        return False
    value = getattr(ann, "read_only_hint", None)
    if value is None:
        value = getattr(ann, "readOnlyHint", None)
    return bool(value)


def destructive_hint(tool):
    ann = tool.annotations
    value = getattr(ann, "destructive_hint", None)
    return getattr(ann, "destructiveHint", None) if value is None else value
