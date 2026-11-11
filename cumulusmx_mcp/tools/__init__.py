"""Tools MCP agrupadas por dominio. Importar este paquete las registra todas en `app.mcp`."""
from . import (  # noqa: F401  (el import registra las tools)
    alerts,
    climate,
    diary,
    editors,
    graphs,
    maintenance,
    observations,
    period_report,
    records,
    reports,
    safety_tools,
    settings,
)
