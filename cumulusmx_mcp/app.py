"""Instancia FastMCP, middlewares ASGI (prefijo y autenticación Bearer) y decoradores de tools."""
import hmac

from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__, config
from .config import logger


class StripPrefix:
    """Middleware ASGI: si la ruta llega con un prefijo externo sin quitar
    (proxy que no reescribe de verdad), lo elimina antes de enrutar."""

    def __init__(self, app, prefix: str):
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope, receive, send):
        if self.prefix and scope["type"] == "http":
            path = scope["path"]
            if path == self.prefix:
                scope["path"] = "/"
            elif path.startswith(self.prefix + "/"):
                scope["path"] = path[len(self.prefix):]
        await self.app(scope, receive, send)


class BearerAuth:
    """Middleware ASGI: exige 'Authorization: Bearer ***' (excepto /health)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] != "/health":
            got = dict(scope["headers"]).get(b"authorization", b"")
            if not config.TOKEN or not (got.startswith(b"Bearer ") and hmac.compare_digest(got[7:], config.TOKEN)):
                logger.warning("Peticion no autorizada (401) a %s desde %s", scope.get("path"), scope.get("client"))
                resp = JSONResponse({"error": "unauthorized"}, status_code=401,
                                    headers={"WWW-Authenticate": "Bearer"})
                return await resp(scope, receive, send)
        await self.app(scope, receive, send)


SERVER_INSTRUCTIONS = """\
MCP server for a CumulusMX weather station. Never invent weather values: every number must come from a tool.

Which tool:
- Now: snapshot (compact) or current_data. Today vs yesterday: today_all. Full morning overview: daily_briefing.
- History (daily): count_days, find_streaks, rankings, climate_summary, monthly_summary, year_records,
  compare_periods, dayfile_range (use summary_only=true first). Normals and anomalies: climatology, anomaly.
- Gardening/agronomy: frost_dates, degree_days. Wind: wind_rose. Trustworthiness of the data: data_quality.
- Configuration: get_settings(section) / update_settings(section, settings, dry_run=true first); dedicated
  get/update_mysql_settings, get/update_mqtt_settings and mysql_table. Real time: live_sample. Fine-grained logs:
  interval_data, daily_data.
- Short ranges / intervals: graph_summary (prefer it over graph_data). Reports: noaa_report, simple_report.

Conventions: dates are yyyy-mm-dd; day-file columns have Spanish names (temp_max, lluvia_total, racha_max...) and accept
English aliases. get_tags/snapshot return plain numbers; text produced by CumulusMX itself (records, today/yesterday,
reports) follows the station's regional format and may use decimal commas ("10,0" = 10.0). CumulusMX uses -9999/9999
for "no data" and 0 % humidity is almost always a sensor fault: run data_quality before quoting extremes.

Write tools (edit_*, update_*, clear_*, trigger_*, generate_/upload_reports...) change the station: describe the change
and get explicit confirmation first; preview settings changes with dry_run=true. Settings can contain passwords:
do not echo them back. Back up before editing history (edit_dayfile, edit_datalog, edit_records). They are absent when the server
runs with ENABLE_WRITE_TOOLS=false.
"""


mcp = FastMCP("cumulusmx-mcp", version=__version__, instructions=SERVER_INSTRUCTIONS)


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    """Comprobación de salud para Docker / orquestador (sin autenticación)."""
    return JSONResponse({"status": "healthy", "service": "cumulusmx-mcp", "version": __version__})


# Anotaciones MCP para categorización de permisos en clientes (Claude, etc.)
READ_ONLY = {"readOnlyHint": True, "destructiveHint": False}


WRITE_ACTION = {"readOnlyHint": False, "destructiveHint": True}


def write_tool(fn):
    """Registra una tool que modifica la estación. Si el servidor arranca con ENABLE_WRITE_TOOLS=false la tool ni
    siquiera se publica (el cliente no la ve). Además cada tool sigue comprobando _check_write_enabled() al ejecutarse."""
    if not config.WRITE_TOOLS_ENABLED:
        return fn
    return mcp.tool(annotations=WRITE_ACTION)(fn)


def build_http_app():
    """Aplicación ASGI HTTP con el prefijo opcional y la autenticación Bearer."""
    return mcp.http_app(
        path="/mcp",
        middleware=[Middleware(StripPrefix, prefix=config.STRIP_PREFIX), Middleware(BearerAuth)],
    )
