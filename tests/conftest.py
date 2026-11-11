"""Configuración de pytest: expone la fachada `server` (módulo único) sobre el paquete modular.

Los tests existentes usan `import server` / `patch.object(server, "_get", ...)`; la fachada resuelve cada nombre en el
módulo del paquete que lo define, de modo que los parches funcionan igual que antes de modularizar.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("MCP_AUTH_TOKEN", "test-secret-token-12345")

from cumulusmx_mcp import _facade  # noqa: E402

_facade.install("server")
