"""Tools MCP agrupadas por dominio. Importar este paquete las registra todas en `app.mcp`."""
from . import (  # noqa: F401  (el import registra las tools)
    climate,
    diary,
    editors,
    graphs,
    maintenance,
    observations,
    records,
    reports,
    settings,
)
