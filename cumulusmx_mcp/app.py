"""Instancia FastMCP, middlewares ASGI (prefijo y autenticación Bearer) y decoradores de tools."""
import functools
import hmac
import inspect

from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__, config, safety
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
    """Middleware ASGI: exige 'Authorization: Bearer ***' (excepto /health).

    Acepta el token principal (MCP_AUTH_TOKEN, acceso completo) y, si está definido, el de solo lectura
    (MCP_AUTH_TOKEN_READONLY). Las tools de escritura comprueban después el ámbito del token (safety.current_scope)."""

    def __init__(self, app):
        self.app = app

    @staticmethod
    def _valid(token: bytes) -> bool:
        return any(expected and hmac.compare_digest(token, expected) for expected in (config.TOKEN, config.TOKEN_READONLY))

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] != "/health":
            got = dict(scope["headers"]).get(b"authorization", b"")
            if not (got.startswith(b"Bearer ") and self._valid(got[7:])):
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
- Alerts now: weather_alerts. Shareable Markdown report (week/month/year): period_report.
- Safety net: list_backups / restore_backup undo configuration and day-file edits; get_audit_log shows past writes.

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
    """Registra una tool que modifica la estación.

    Si el servidor arranca con ENABLE_WRITE_TOOLS=false la tool ni siquiera se publica. Si se publica, cada llamada pasa por
    la guarda de escritura (interruptor y ámbito del token) y queda en la auditoría, tanto si tiene éxito como si falla."""
    if not config.WRITE_TOOLS_ENABLED:
        return fn
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    async def guarded(*args, **kwargs):
        try:
            arguments = dict(signature.bind_partial(*args, **kwargs).arguments)
        except TypeError:
            arguments = dict(kwargs)
        if (err := safety._check_write_enabled()):
            safety.audit(fn.__name__, arguments, "denegado", err)
            return err
        try:
            result = await fn(*args, **kwargs)
        except Exception as exc:
            safety.audit(fn.__name__, arguments, "error", f"{type(exc).__name__}: {exc}")
            raise
        safety.audit(fn.__name__, arguments, "fallo" if safety.result_failed(result) else "ok", result)
        return result

    return mcp.tool(annotations=WRITE_ACTION)(guarded)


def build_http_app():
    """Aplicación ASGI HTTP con el prefijo opcional y la autenticación Bearer."""
    return mcp.http_app(
        path="/mcp",
        middleware=[Middleware(StripPrefix, prefix=config.STRIP_PREFIX), Middleware(BearerAuth)],
    )
