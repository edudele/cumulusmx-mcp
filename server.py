"""Compatibilidad: `python server.py` e `import server` siguen funcionando.

El código vive en el paquete `cumulusmx_mcp/` (ver su docstring para la estructura). Este fichero solo arranca el
servidor o, al importarse, expone la fachada `server` con todos los nombres del paquete.
"""
import sys

if __name__ == "__main__":
    from cumulusmx_mcp.cli import main

    main()
else:
    from cumulusmx_mcp import _facade

    sys.modules[__name__] = _facade.install(__name__)
