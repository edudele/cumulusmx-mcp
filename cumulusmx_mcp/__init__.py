"""cumulusmx-mcp: servidor MCP para estaciones meteorológicas CumulusMX.

Estructura del paquete (de abajo arriba; cada capa solo importa las anteriores):

    config           variables de entorno y constantes
    utils            funciones puras (fechas, números, dicts)
    safety           guardas de escritura, ámbito del token y auditoría
    backups          copias previas a cada escritura y restauración
    client           cliente HTTP hacia CumulusMX (reintentos, timeouts)
    app              instancia FastMCP, middlewares ASGI, decoradores de tools
    dayrows          histórico diario (dayfile): columnas, caché, estadísticas
    analysis         cálculos climáticos puros sobre filas del dayfile
    settings_engine  lectura-fusión-escritura genérica de ajustes
    alerts, reporting  lógica pura de alertas y de informes periódicos
    tools/           las tools MCP, agrupadas por dominio
    resources, prompts  recursos y prompts MCP
    cli              arranque (HTTP o stdio)
"""

__version__ = "1.8.1"

import os as _os

# Este servidor guarda credenciales de la estación: que FastMCP no consulte PyPI por su cuenta al arrancar.
# (Se puede reactivar con FASTMCP_CHECK_FOR_UPDATES=stable.) Debe fijarse antes de que se importe fastmcp.
_os.environ.setdefault("FASTMCP_CHECK_FOR_UPDATES", "off")
