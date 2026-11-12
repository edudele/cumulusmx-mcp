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


def saved_settings_reader(initial, post_mock):
    """`_json` simulado de un CumulusMX que GUARDA lo que se le envía: devuelve `initial` hasta el primer POST y después el
    cuerpo enviado (el MCP vuelve a leer la sección para comprobar que se guardó)."""
    import json
    import urllib.parse

    async def read(*_args, **_kwargs):
        if not post_mock.await_count:
            return initial
        body = post_mock.await_args.kwargs.get("content", "")
        text = urllib.parse.unquote(body)
        return json.loads(text[5:] if text.startswith("json=") else text)

    return read


def destructive_hint(tool):
    ann = tool.annotations
    value = getattr(ann, "destructive_hint", None)
    return getattr(ann, "destructiveHint", None) if value is None else value
