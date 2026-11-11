"""MCP para CumulusMX (lectura, escritura, informes y mantenimiento). Habla con su API HTTP por la red interna."""
import asyncio
import base64
import calendar
import collections
import datetime
import hmac
import html
import json
import logging
import math
import operator
import os
import re
import statistics
import time
import urllib.parse
from typing import Any, Iterable, Literal

import httpx
from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# Versión semántica del servidor. Debe coincidir con la cabecera de CHANGELOG.md
# y con el tag de git (vX.Y.Z) que dispara la publicación de la imagen Docker.
__version__ = "1.6.0"

LOG_LEVEL = os.environ.get("LOG_LEVEL", "info").lower()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("cumulusmx-mcp")

TOKEN = os.environ.get("MCP_AUTH_TOKEN", "").encode()
BASE = os.environ.get("CUMULUSMX_URL", "http://cumulusmx:8998").rstrip("/")
PORT = int(os.environ.get("PORT", "8000"))
# Prefijo externo opcional (p.ej. detrás de un proxy que no reescribe bien la
# ruta, como Pangolin con "clip prefix"). Si llega /mcp-res/mcp lo tratamos
# como /mcp. Configurable por si el prefijo cambia; vacío = desactivado.
STRIP_PREFIX = os.environ.get("MCP_STRIP_PREFIX", "/mcp-res")
DATA_START_YEAR = int(os.environ.get("CUMULUS_DATA_START_YEAR", "2023"))
FROST_THRESHOLD = float(os.environ.get("CUMULUS_FROST_THRESHOLD", "0.0"))
WARM_THRESHOLDS = (25.0, 30.0)
CURRENT_YEAR_TTL_S = int(os.environ.get("CUMULUS_CACHE_TTL", "600"))
# Peticiones GET a CumulusMX: timeout por intento y reintentos ante fallos transitorios (conexión, timeout, 502/504).
CUMULUS_TIMEOUT = float(os.environ.get("CUMULUS_TIMEOUT", "30"))
CUMULUS_RETRIES = max(0, int(os.environ.get("CUMULUS_RETRIES", "2")))
RETRY_STATUS = frozenset({502, 504})
RETRY_BACKOFF_S = 0.3


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
            if not TOKEN or not (got.startswith(b"Bearer ") and hmac.compare_digest(got[7:], TOKEN)):
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


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?$")
TAG_RE = re.compile(r"^[A-Za-z0-9_]+$")

MAX_CHARS = 60000
_client: httpx.AsyncClient | None = None

WRITE_DISABLED_MSG = "Error: Las operaciones de modificación/escritura están deshabilitadas en la configuración (ENABLE_WRITE_TOOLS=false)."

# Anotaciones MCP para categorización de permisos en clientes (Claude, etc.)
READ_ONLY = {"readOnlyHint": True, "destructiveHint": False}
WRITE_ACTION = {"readOnlyHint": False, "destructiveHint": True}

WRITE_TOOLS_ENABLED = os.environ.get("ENABLE_WRITE_TOOLS", "true").strip().lower() not in ("false", "0", "no", "off")


def write_tool(fn):
    """Registra una tool que modifica la estación. Si el servidor arranca con ENABLE_WRITE_TOOLS=false la tool ni
    siquiera se publica (el cliente no la ve). Además cada tool sigue comprobando _check_write_enabled() al ejecutarse."""
    if not WRITE_TOOLS_ENABLED:
        return fn
    return mcp.tool(annotations=WRITE_ACTION)(fn)


def _check_write_enabled() -> str | None:
    val = os.environ.get("ENABLE_WRITE_TOOLS", "true").strip().lower()
    if val in ("false", "0", "no", "off"):
        return WRITE_DISABLED_MSG
    return None


def _get_auth() -> tuple[str, str] | None:
    user = os.environ.get("CUMULUS_USER")
    pwd = os.environ.get("CUMULUS_PASSWORD")
    if user:
        return (user, pwd or "")
    return None


def _cli() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30)
    return _client


async def _api_request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    content: bytes | str | None = None,
    data: Any = None,
    json_data: Any = None,
    headers: dict | None = None,
    timeout: float = 30.0,
) -> httpx.Response:
    cli = _cli()
    auth = _get_auth()
    req_headers = dict(headers or {})
    if isinstance(content, str):
        content = content.encode("utf-8")
    return await cli.request(
        method=method,
        url=f"{BASE}{path}",
        params=params,
        content=content,
        data=data,
        json=json_data,
        headers=req_headers,
        auth=auth,
        timeout=timeout,
    )


async def _request_get(path: str, params: dict | None = None) -> httpx.Response:
    """GET con reintentos (máx. CUMULUS_RETRIES) ante errores de conexión, timeouts y 502/504. Los POST no se
    reintentan nunca (no son idempotentes). Backoff exponencial corto: 0,3 s, 0,6 s..."""
    for attempt in range(CUMULUS_RETRIES + 1):
        last = attempt == CUMULUS_RETRIES
        try:
            r = await _api_request("GET", path, params=params, timeout=CUMULUS_TIMEOUT)
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            if last:
                raise
            logger.warning("GET %s falló (%s); reintento %d/%d", path, type(e).__name__, attempt + 1, CUMULUS_RETRIES)
        else:
            if r.status_code not in RETRY_STATUS or last:
                return r
            logger.warning("GET %s devolvió %s; reintento %d/%d", path, r.status_code, attempt + 1, CUMULUS_RETRIES)
        await asyncio.sleep(RETRY_BACKOFF_S * 2 ** attempt)
    raise RuntimeError("inalcanzable")  # pragma: no cover


def _clip(t: str) -> str:
    if len(t) > MAX_CHARS:
        return t[:MAX_CHARS] + f"\n…[truncado: {len(t)} caracteres; usa rangos más pequeños o downsample]"
    return t


async def _get(path: str, params: dict | None = None) -> str:
    try:
        r = await _request_get(path, params)
        r.raise_for_status()
        return _clip(r.text)
    except httpx.HTTPStatusError as e:
        return f"ERROR HTTP {e.response.status_code} en {path}: {e.response.text[:300]}"
    except httpx.HTTPError as e:
        return f"ERROR de conexión con CumulusMX ({type(e).__name__}): {e}"


async def _json(path: str, params: dict | None = None):
    try:
        r = await _request_get(path, params)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        raise ValueError(f"HTTP {e.response.status_code} en {path}: {e.response.text[:200]}")
    except httpx.HTTPError as e:
        raise ValueError(f"error de conexión con CumulusMX: {e}")


async def _post(
    path: str,
    *,
    params: dict | None = None,
    content: bytes | str | None = None,
    data: Any = None,
    json_data: Any = None,
    headers: dict | None = None,
    timeout: float = 60.0,
) -> str:
    try:
        r = await _api_request(
            "POST",
            path,
            params=params,
            content=content,
            data=data,
            json_data=json_data,
            headers=headers,
            timeout=timeout,
        )
        r.raise_for_status()
        return _clip(r.text)
    except httpx.HTTPStatusError as e:
        return f"ERROR HTTP {e.response.status_code} en {path}: {e.response.text[:300]}"
    except httpx.HTTPError as e:
        return f"ERROR de conexión con CumulusMX ({type(e).__name__}): {e}"


def _normalize_settings_payload(settings: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(settings, str):
        try:
            parsed = json.loads(settings)
        except Exception as e:
            raise ValueError(f"JSON de configuración inválido: {e}")
        if not isinstance(parsed, dict):
            raise ValueError("El JSON de configuración debe ser un objeto/diccionario")
        return parsed
    if not isinstance(settings, dict):
        raise ValueError("Los ajustes deben proporcionarse como diccionario o cadena JSON")
    return settings


def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    out = dict(target)
    for k, v in source.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _clean(t: str) -> str:
    t = re.sub(r"<br\s*/?>", " ", t)
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(t).replace("\xa0", " ").strip()


def _check_date(d: str, name: str = "fecha") -> datetime.date:
    try:
        return datetime.datetime.strptime(d, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"{name} inválida, usa yyyy-mm-dd")


def _num(x: Any) -> float | None:
    if x is None or x in ("", "-", "None", "null"):
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    # CumulusMX usa -9999 / 9999 (DefaultHiVal / DefaultLoVal) como "sin dato"; ninguna columna del dayfile
    # puede valer legítimamente <= -999.
    if math.isnan(f) or f <= -999 or f >= 9999:
        return None
    return f


def _today() -> datetime.date:
    return datetime.date.today()


# ---------------------------------------------------------------- tools básicas ----

@mcp.tool(annotations=READ_ONLY)
async def ping() -> str:
    """Comprueba que el MCP responde."""
    return "pong"


@mcp.tool(annotations=READ_ONLY)
async def current_data() -> str:
    """Condiciones actuales completas de la estación (JSON): temperatura, humedad, viento, lluvia, presión, etc."""
    return await _get("/api/data/currentdata")


def _tags_path(tags: list[str], plain_numbers: bool = True) -> str:
    """Ruta de /api/tags/process.json. CumulusMX acepta el parámetro especial `rc` (debe ir el primero) que aplica el
    formato 'remove commas' a todos los tags: sin él, una estación con configuración regional española devuelve
    '10,0' (coma decimal), ambiguo y no convertible a número."""
    return "/api/tags/process.json?" + ("rc&" if plain_numbers else "") + "&".join(tags)


@mcp.tool(annotations=READ_ONLY)
async def get_tags(tags: list[str], plain_numbers: bool = True) -> str:
    """Valores de webtags de CumulusMX (p.ej. ['temp','hum','press','wlatest']). Devuelve JSON {tag: valor}.
    Todos los valores llegan como texto. plain_numbers=True (por defecto) aplica el formato 'rc' de CumulusMX:
    decimales con punto ('10.0' en vez de '10,0'). Con False se respeta el formato regional de la estación."""
    if not tags or len(tags) > 50 or not all(TAG_RE.match(t) for t in tags):
        raise ValueError("tags inválidos (solo letras, números y _; máximo 50)")
    return await _get(_tags_path(tags, plain_numbers))


@mcp.tool(annotations=READ_ONLY)
async def today_yesterday(kind: Literal["temp", "hum", "rain", "wind", "pressure", "solar"]) -> str:
    """Valores de hoy y ayer para una magnitud."""
    return await _get(f"/api/todayyest/{kind}.json")


@mcp.tool(annotations=READ_ONLY)
async def graph_data(name: str, start: str = "", end: str = "") -> str:
    """Serie temporal para gráficos (últimas ~24 h por defecto). name (sin .json):
    tempdata, winddata, raindata, pressdata, wdirdata, humdata, solardata, airqualitydata, extratemp, extrahum,
    extradew, soiltemp, soilmoist, soilec, leafwetness, usertemp, co2sensor, laserdepth, snow24h;
    diarias: dailyrain, sunhours, dailytemp; meta: units, graphconfig, availabledata;
    por intervalo (con start/end): intvtemp, intvwind, intvrain, intvpress, intvhum, intvsolar, intvairquality,
    intvextratemp, intvextrahum, intvextradew, intvsoiltemp, intvsoilmoist, intvsoilec, intvleafwetness,
    intvusertemp, intvco2sensor, intvlaserdepth, intvsnow24h. Las series de sensores extra solo existen si el
    sensor está configurado. start/end opcionales: 'yyyy-MM-dd' o 'yyyy-MM-dd HH:mm' (hora local)."""
    if not re.fullmatch(r"[a-z0-9]{3,30}", name):
        raise ValueError("name inválido")
    params = {}
    for k, v in (("start", start), ("end", end)):
        if v:
            if not DATE_RE.match(v):
                raise ValueError(f"{k} con formato inválido")
            params[k] = v
    return await _get(f"/api/graphdata/{name}.json", params)


@mcp.tool(annotations=READ_ONLY)
async def daily_graph_data(name: str) -> str:
    """Series diarias de todo el histórico. name (sin .json): tempdata, winddata, raindata, pressdata, wdirdata,
    humdata, solardata, degdaydata, dailysnow, tempsumdata, chillhrsdata, units, graphconfig."""
    if not re.fullmatch(r"[a-z0-9]{3,30}", name):
        raise ValueError("name inválido")
    return await _get(f"/api/dailygraphdata/{name}.json")


@mcp.tool(annotations=READ_ONLY)
async def records(scope: Literal["alltime", "thismonth", "thisyear", "month"],
                  kind: Literal["temperature", "humidity", "pressure", "wind", "rain"],
                  month: int = 0) -> str:
    """Récords de la estación. Si scope='month' indica month (1-12)."""
    if scope == "month":
        if not 1 <= month <= 12:
            raise ValueError("month debe estar entre 1 y 12")
        return await _get(f"/api/records/month/{month}/{kind}.json")
    return await _get(f"/api/records/{scope}/{kind}.json")


@mcp.tool(annotations=READ_ONLY)
async def records_period(startdate: str, enddate: str) -> str:
    """Récords calculados para un periodo específico (yyyy-mm-dd). Años permitidos: 2000-2050."""
    for d, n in ((startdate, "startdate"), (enddate, "enddate")):
        if not re.fullmatch(r"20[0-5]\d-\d{2}-\d{2}", d):
            raise ValueError(f"{n} debe tener formato yyyy-mm-dd y estar entre 2000 y 2050")
        try:
            datetime.datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            raise ValueError("fecha inválida, usa yyyy-mm-dd")
    return await _get("/api/records/thisperiod", {"startdate": startdate, "enddate": enddate})


@mcp.tool(annotations=READ_ONLY)
async def dayfile(start: int = 0, length: int = 31, search: str = "") -> str:
    """Resumen diario histórico (dayfile), paginado. length máx. 500."""
    length = max(1, min(length, 500))
    return await _get("/api/data/dayfile", {"draw": 1, "start": max(start, 0), "length": length,
                                            "search[value]": search[:100]})


@mcp.tool(annotations=READ_ONLY)
async def station_info(kind: Literal["version", "units", "alarms", "snowinfo", "timeformat", "wsport", "displaylangs",
                                     "dateformat", "csvseparator"]) -> str:
    """Información de la estación: versión, unidades, alarmas, formato de fecha (dateformat), separador CSV
    (csvseparator), idiomas, puerto WebSocket..."""
    ext = "txt" if kind in ("dateformat", "csvseparator") else "json"
    return await _get(f"/api/info/{kind}.{ext}")


@mcp.tool(annotations=READ_ONLY)
async def noaa_report(period: Literal["noaayear", "noaamonth"], year: int, month: int = 1) -> str:
    """Informe NOAA anual o mensual en texto."""
    if not 2000 <= year <= 2050 or not 1 <= month <= 12:
        raise ValueError("year/month fuera de rango")
    return await _get(f"/api/reports/{period}", {"year": year, "month": month})


# ---------------------------------------------------------------- dayfile y analytics ----

DAYFILE_COLS = [
    "n", "fecha", "racha_max", "dir_racha", "hora_racha", "temp_min", "hora_temp_min",
    "temp_max", "hora_temp_max", "pres_min", "hora_pres_min", "pres_max", "hora_pres_max",
    "lluvia_rate_max", "hora_lluvia_rate", "lluvia_total", "temp_media", "recorrido_viento",
    "viento_medio_max", "hora_viento_medio_max", "hum_min", "hora_hum_min", "hum_max", "hora_hum_max",
    "ET", "horas_sol", "indice_calor_max", "hora_ic", "temp_aparente_max", "hora_ta_max",
    "temp_aparente_min", "hora_ta_min", "lluvia_horaria_max", "hora_lluvia_horaria",
    "sensacion_viento_min", "hora_sv_min", "rocio_max", "hora_rocio_max", "rocio_min", "hora_rocio_min",
    "dir_viento_dominante", "grados_dia_calef", "grados_dia_refr", "solar_max", "hora_solar_max",
    "uv_max", "hora_uv_max", "feels_max", "hora_feels_max", "feels_min", "hora_feels_min",
    "humidex_max", "hora_humidex_max", "horas_frio",
    # Añadidas en versiones recientes de CumulusMX (orden oficial: High24hRain, HighBgt, HighWbgt)
    "lluvia_24h_max", "hora_lluvia_24h", "bgt_max", "hora_bgt_max", "wbgt_max", "hora_wbgt_max",
]

CANONICAL_COLUMNS = (
    "fecha", "temp_min", "temp_max", "temp_media",
    "lluvia_total", "racha_max", "pres_min", "pres_max",
    "hum_min", "hum_max", "horas_sol",
)

COLUMN_ALIASES: dict[str, str] = {
    "date": "fecha",
    "mintemp": "temp_min",
    "maxtemp": "temp_max",
    "avgtemp": "temp_media",
    "temp": "temp_media",
    "rain": "lluvia_total",
    "precipitation": "lluvia_total",
    "precip": "lluvia_total",
    "wind": "racha_max",
    "gust": "racha_max",
    "windgust": "racha_max",
    "press": "pres_min",
    "pressure": "pres_min",
    "minpress": "pres_min",
    "maxpress": "pres_max",
    "hum": "hum_min",
    "humidity": "hum_min",
    "minhum": "hum_min",
    "maxhum": "hum_max",
    "sun": "horas_sol",
    "sunhours": "horas_sol",
    "sol": "horas_sol",
    "solar": "solar_max",
    "uv": "uv_max",
    "et": "ET",
    "rain24h": "lluvia_24h_max",
    "bgt": "bgt_max",
    "wbgt": "wbgt_max",
}

_DAYFILE_LOWER_MAP = {c.lower(): c for c in DAYFILE_COLS}

UNITS: dict[str, str] = {
    "temp_min": "°C", "temp_max": "°C", "temp_media": "°C",
    "lluvia_total": "mm", "racha_max": "km/h",
    "pres_min": "hPa", "pres_max": "hPa",
    "hum_min": "%", "hum_max": "%", "horas_sol": "h",
    "ET": "mm", "recorrido_viento": "km", "solar_max": "W/m²",
    "uv_max": "UV",
    "lluvia_24h_max": "mm", "bgt_max": "°C", "wbgt_max": "°C",
}

ADDITIVE_COLUMNS: frozenset[str] = frozenset({"lluvia_total", "horas_sol", "ET", "recorrido_viento"})


def resolve_columns(columns: list[str] | None) -> list[str]:
    """Convierte alias a nombres canónicos.
    - Ignora mayúsculas y espacios.
    - Elimina duplicados conservando el orden.
    - Garantiza que 'fecha' va siempre en primera posición.
    - Lanza ValueError si hay columnas desconocidas, listándolas junto a las válidas.
    """
    if not columns:
        return list(CANONICAL_COLUMNS)

    resolved: list[str] = []
    unknown: list[str] = []
    for raw in columns:
        clean = raw.strip().lower()
        canon = COLUMN_ALIASES.get(clean, clean)
        actual = _DAYFILE_LOWER_MAP.get(canon.lower())
        if actual is None:
            unknown.append(raw)
        elif actual not in resolved:
            resolved.append(actual)

    if unknown:
        valid = sorted(set(DAYFILE_COLS) | set(COLUMN_ALIASES.keys()))
        raise ValueError(f"Columnas desconocidas: {unknown}. Válidas: {valid}")

    if "fecha" in resolved:
        resolved.remove("fecha")
    return ["fecha", *resolved]


_cached_dayfile_by_year: dict[int, list[dict]] = {}
_dayfile_cache_time: float = 0.0
_dayfile_lock = asyncio.Lock()


async def _fetch_all_dayfile_raw() -> list[dict]:
    rows, start = [], 0
    while True:
        j = await _json("/api/data/dayfile", {"draw": 1, "start": start, "length": 500, "search[value]": ""})
        data = j.get("data", [])
        rows += data
        start += 500
        if not data or start >= j.get("recordsTotal", 0):
            break
    out = []
    for r in rows:
        d = dict(zip(DAYFILE_COLS, r))
        raw_date = d.get("fecha", "")
        dt_obj = None
        for fmt in ("%d/%m/%y", "%d/%m/%Y", "%Y-%m-%d"):
            try:
                dt_obj = datetime.datetime.strptime(raw_date, fmt).date()
                break
            except (ValueError, TypeError):
                continue
        if dt_obj is None:
            continue
        d["_date"] = dt_obj
        d["fecha_orig"] = raw_date
        d["fecha"] = dt_obj.isoformat()
        out.append(d)
    return out


async def _get_dayfile_rows(start_year: int | None = None, end_year: int | None = None) -> list[dict]:
    global _dayfile_cache_time
    now = time.monotonic()
    current_year = _today().year
    async with _dayfile_lock:
        needs_refresh = False
        if not _cached_dayfile_by_year:
            needs_refresh = True
        elif (now - _dayfile_cache_time) > CURRENT_YEAR_TTL_S:
            needs_refresh = True

        if needs_refresh:
            all_rows = await _fetch_all_dayfile_raw()
            by_year: dict[int, list[dict]] = {}
            for r in all_rows:
                y = r["_date"].year
                by_year.setdefault(y, []).append(r)
            _cached_dayfile_by_year.clear()
            _cached_dayfile_by_year.update(by_year)
            _dayfile_cache_time = now

    sy = start_year if start_year is not None else DATA_START_YEAR
    ey = end_year if end_year is not None else current_year
    result: list[dict] = []
    for y in sorted(_cached_dayfile_by_year.keys()):
        if sy <= y <= ey:
            result.extend(_cached_dayfile_by_year[y])
    return result


async def _all_dayfile() -> list[dict]:
    return await _get_dayfile_rows(start_year=2000, end_year=2050)


def _agg(rows: list[dict]) -> dict:
    if not rows:
        return {"dias": 0}

    def ext(col, fn):
        vals = [(_num(r.get(col)), r["_date"]) for r in rows if _num(r.get(col)) is not None]
        if not vals:
            return None
        v = fn(vals, key=lambda x: x[0])
        return {"valor": v[0], "fecha": str(v[1])}

    lluvia = [_num(r["lluvia_total"]) or 0 for r in rows]
    medias = [_num(r["temp_media"]) for r in rows if _num(r["temp_media"]) is not None]
    return {
        "dias": len(rows),
        "desde": str(rows[0]["_date"]), "hasta": str(rows[-1]["_date"]),
        "temp_max_absoluta": ext("temp_max", max), "temp_min_absoluta": ext("temp_min", min),
        "temp_media_del_periodo": round(sum(medias) / len(medias), 2) if medias else None,
        "lluvia_total": round(sum(lluvia), 1), "dias_con_lluvia(>=0.2)": sum(1 for x in lluvia if x >= 0.2),
        "dia_mas_lluvioso": ext("lluvia_total", max),
        "racha_max": ext("racha_max", max), "pres_max": ext("pres_max", max), "pres_min": ext("pres_min", min),
        "horas_sol_total": round(sum(_num(r["horas_sol"]) or 0 for r in rows), 1),
        "uv_max": ext("uv_max", max), "solar_max": ext("solar_max", max),
        "unidades": "las de la estación (ver station_info units)",
    }


def _valid(rows: Iterable[dict], var: str) -> list[tuple[str, float]]:
    out = []
    for r in rows:
        v = _num(r.get(var))
        f = r.get("fecha")
        if v is not None and f:
            out.append((str(f), v))
    return out


MAX_TIE_DATES = 10


def extreme(rows: Iterable[dict], var: str, mode: Literal["min", "max"]) -> dict | None:
    """Devuelve {'valor', 'fecha' (la más antigua), 'fechas' (las empatadas más antiguas, máx. MAX_TIE_DATES)
    y 'n_fechas' (total de empates) cuando hay más de MAX_TIE_DATES}."""
    pairs = _valid(rows, var)
    if not pairs:
        return None
    target = min(v for _, v in pairs) if mode == "min" else max(v for _, v in pairs)
    fechas = sorted(f for f, v in pairs if v == target)
    out = {"valor": round(target, 2), "fecha": fechas[0], "fechas": fechas[:MAX_TIE_DATES]}
    if len(fechas) > MAX_TIE_DATES:  # p. ej. lluvia mínima 0,0 repetida en cientos de días
        out["n_fechas"] = len(fechas)
    return out


def rank(rows: Iterable[dict], var: str, top: int = 10, order: Literal["asc", "desc"] = "desc") -> list[dict]:
    """Ranking con competition ranking (1, 2, 2, 4). Desempate por fecha ascendente."""
    pairs = _valid(rows, var)
    reverse = (order == "desc")
    # Orden estable: primero por fecha asc y después por valor. Así los empates quedan por fecha asc.
    pairs.sort(key=lambda p: p[0])
    pairs.sort(key=lambda p: p[1], reverse=reverse)
    result = []
    prev_val = None
    pos = 0
    for i, (fecha, valor) in enumerate(pairs[:top], start=1):
        if valor != prev_val:
            pos, prev_val = i, valor
        result.append({"posicion": pos, "fecha": fecha, "valor": round(valor, 2)})
    return result


def summarize(rows: list[dict], columns: list[str]) -> dict:
    """Agregados por columna numérica: n, min, max, media y suma (solo columnas aditivas)."""
    fechas = sorted(r["fecha"] for r in rows if r.get("fecha"))
    out: dict[str, Any] = {
        "n_dias": len(rows),
        "desde": fechas[0] if fechas else None,
        "hasta": fechas[-1] if fechas else None,
        "metricas": {},
    }
    non_numeric = {"fecha", "n", "dir_racha", "dir_viento_dominante"}
    for col in columns:
        if col in non_numeric or col.startswith("hora_"):
            continue
        pairs = _valid(rows, col)
        vals = [v for _, v in pairs]
        m: dict[str, Any] = {"unidad": UNITS.get(col, ""), "n_dias_con_dato": len(vals)}
        if vals:
            m["min"] = extreme(rows, col, "min")
            m["max"] = extreme(rows, col, "max")
            m["media"] = round(statistics.fmean(vals), 2)
            if col in ADDITIVE_COLUMNS:
                m["suma"] = round(sum(vals), 2)
        out["metricas"][col] = m
    return out


def compute_year_records(rows: list[dict], year: int, today: datetime.date) -> dict:
    """Récords de un año a partir de las filas del dayfile."""
    tmin = _valid(rows, "temp_min")
    tmax = _valid(rows, "temp_max")
    rain = _valid(rows, "lluvia_total")
    t25, t30 = WARM_THRESHOLDS
    rain_vals = [v for _, v in rain]
    tmin_vals = [v for _, v in tmin]
    tmax_vals = [v for _, v in tmax]
    return {
        "year": year,
        "parcial": year == today.year,
        "n_dias_con_datos": len(rows),
        "temp_min_absoluta": extreme(rows, "temp_min", "min"),
        "temp_max_absoluta": extreme(rows, "temp_max", "max"),
        "racha_max": extreme(rows, "racha_max", "max"),
        "precipitacion": {
            "acumulada_mm": round(sum(rain_vals), 1) if rain_vals else 0.0,
            "dia_mas_lluvioso": extreme(rows, "lluvia_total", "max"),
            "n_dias_con_dato": len(rain_vals),
            "dias_con_lluvia_gte_0_2": sum(1 for v in rain_vals if v >= 0.2),
        },
        "dias_helada": sum(1 for v in tmin_vals if v <= FROST_THRESHOLD),
        "dias_calidos": {
            f"gte_{int(t25)}": sum(1 for v in tmax_vals if v >= t25),
            f"gte_{int(t30)}": sum(1 for v in tmax_vals if v >= t30),
        },
    }


# ---------------------------------------------------------------- tools avanzadas ----

@mcp.tool(annotations=READ_ONLY)
async def dayfile_range(start: str, end: str, columns: list[str] | None = None, max_rows: int = 400, summary_only: bool = False) -> str:
    """Resumen diario (dayfile) entre dos fechas yyyy-mm-dd, con nombres de columna legibles y soporte de alias.
    columns: lista opcional de columnas o alias (ej. ['date', 'MinTemp', 'MaxTemp', 'Rain', 'Wind', 'temp_min']).
    max_rows: máximo de filas a devolver si summary_only=False (máx 400).
    summary_only: si es True, devuelve solo el resumen estadístico agregado de las columnas seleccionadas en vez de las filas individuales."""
    s, e = _check_date(start, "start"), _check_date(end, "end")
    if e < s:
        raise ValueError("end anterior a start")
    cols = resolve_columns(columns)
    all_rows = await _all_dayfile()
    sel = [r for r in all_rows if s <= r["_date"] <= e]
    if summary_only:
        return json.dumps(summarize(sel, cols), ensure_ascii=False)
    limited = sel[: max(1, min(max_rows, 400))]
    return json.dumps([{c: r.get(c) for c in cols} for r in limited], ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def climate_summary(start: str, end: str) -> str:
    """Estadísticas agregadas del periodo [start, end] (yyyy-mm-dd) calculadas del histórico diario:
    temperatura máxima/mínima absoluta con fecha, media, lluvia total, días de lluvia, día más lluvioso,
    racha máxima, presión extrema, horas de sol, UV y radiación máximos."""
    s, e = _check_date(start, "start"), _check_date(end, "end")
    if e < s:
        raise ValueError("end anterior a start")
    rows = [r for r in await _all_dayfile() if s <= r["_date"] <= e]
    return json.dumps(_agg(rows), ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def monthly_summary(year: int) -> str:
    """Resumen mes a mes de un año (temperaturas extremas y media, lluvia, días de lluvia, racha, horas de sol)."""
    if not 2000 <= year <= 2050:
        raise ValueError("year fuera de rango")
    allrows = [r for r in await _all_dayfile() if r["_date"].year == year]
    out = {}
    for m in range(1, 13):
        a = _agg([r for r in allrows if r["_date"].month == m])
        if a["dias"]:
            out[f"{year}-{m:02d}"] = a
    return json.dumps(out, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def compare_periods(start_a: str, end_a: str, start_b: str, end_b: str) -> str:
    """Compara dos periodos (yyyy-mm-dd) con las mismas estadísticas que climate_summary
    (p.ej. este octubre vs. octubre del año pasado)."""
    rows = await _all_dayfile()
    res = {}
    for name, (s, e) in {"A": (start_a, end_a), "B": (start_b, end_b)}.items():
        sd, ed = _check_date(s, f"start_{name}"), _check_date(e, f"end_{name}")
        res[name] = _agg([r for r in rows if sd <= r["_date"] <= ed])
    return json.dumps(res, ensure_ascii=False)


# Series de /api/graphdata (nombres según la documentación de CumulusMX) -> grupo de unidades de units.json
_SERIES_UNIT_GROUP = {
    "intemp": "temp", "dew": "temp", "apptemp": "temp", "feelslike": "temp", "wchill": "temp",
    "heatindex": "temp", "temp": "temp", "mintemp": "temp", "maxtemp": "temp", "avgtemp": "temp",
    "wgust": "wind", "wspeed": "wind", "rfall": "rain", "dailyrain": "rain", "rrate": "rain/h", "press": "press",
}
_SERIES_FIXED_UNIT = {
    "hum": "%", "inhum": "%", "SolarRad": "W/m²", "CurrentSolarMax": "W/m²",
    "bearing": "°", "avgbearing": "°", "sunshours": "h",
}


async def _graph_units() -> dict[str, str]:
    """Unidades configuradas en la estación (/api/graphdata/units.json). Si falla o el formato no es el esperado,
    devuelve {} para que graph_summary siga funcionando sin unidades."""
    try:
        raw = await _json("/api/graphdata/units.json")
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        if isinstance(v, dict):  # formato antiguo de la wiki: {"temp": {"units": "C", "decimals": 1}}
            v = v.get("units")
        if isinstance(v, str) and v:
            out[k] = html.unescape(v)
    return out


def _series_unit(series: str, units: dict[str, str]) -> str | None:
    if series in _SERIES_FIXED_UNIT:
        return _SERIES_FIXED_UNIT[series]
    group = _SERIES_UNIT_GROUP.get(series)
    if group is None:
        return None
    suffix = "/h" if group.endswith("/h") else ""
    base = units.get(group.removesuffix("/h"))
    if not base:
        return None
    if base in ("C", "F"):
        base = "°" + base
    return base + suffix


@mcp.tool(annotations=READ_ONLY)
async def graph_summary(name: str, start: str = "", end: str = "", points: int = 0) -> str:
    """Resumen estadístico de una serie de gráfica (mín, máx, media, último valor, con hora ISO) en vez de miles de puntos.
    name: tempdata, winddata, raindata, pressdata, humdata, solardata, wdirdata, o intvtemp, intvwind, intvrain,
    intvpress, intvhum, intvsolar (estas con start/end 'yyyy-MM-dd' o 'yyyy-MM-dd HH:mm', hora local).
    points>0 devuelve además la serie reducida a ese nº de puntos (máx 300)."""
    if not re.fullmatch(r"[a-z0-9]{3,30}", name):
        raise ValueError("name inválido")
    params = {}
    for k, v in (("start", start), ("end", end)):
        if v:
            if not DATE_RE.match(v):
                raise ValueError(f"{k} con formato inválido")
            params[k] = v
    j = await _json(f"/api/graphdata/{name}.json", params)
    units = await _graph_units()
    out = {}
    for serie, pts in (j.items() if isinstance(j, dict) else []):
        vals = [(p[0], _num(p[1])) for p in pts if isinstance(p, list) and len(p) >= 2 and _num(p[1]) is not None]
        if not vals:
            continue
        iso = lambda ms: datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M")
        mn, mx = min(vals, key=lambda x: x[1]), max(vals, key=lambda x: x[1])
        d = {"n": len(vals), "min": mn[1], "min_en": iso(mn[0]), "max": mx[1], "max_en": iso(mx[0]),
             "media": round(sum(v for _, v in vals) / len(vals), 2), "ultimo": vals[-1][1],
             "ultimo_en": iso(vals[-1][0]), "primero_en": iso(vals[0][0]), "nota_hora": "ISO en UTC"}
        if (unidad := _series_unit(serie, units)):
            d["unidad"] = unidad
        if points > 0:
            step = max(1, len(vals) // min(points, 300))
            d["serie"] = [[iso(t), v] for t, v in vals[::step]]
        out[serie] = d
    return json.dumps(out, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def rankings(
    variable: Literal["temp_min", "temp_max", "lluvia_total", "racha_max", "temp_media", "horas_sol"],
    top: int = 10,
    order: Literal["asc", "desc"] = "desc",
    start_year: int | None = None,
    end_year: int | None = None,
) -> str:
    """Ranking histórico de una variable meteorológica calculada sobre el dayfile.
    variable: 'temp_min', 'temp_max', 'lluvia_total', 'racha_max', 'temp_media', 'horas_sol'.
    top: número de posiciones a devolver (por defecto 10, máx 100).
    order: 'asc' o 'desc' (por defecto 'desc').
    start_year: año de inicio (por defecto CUMULUS_DATA_START_YEAR, 2023 si no se configura).
    end_year: año final (por defecto el año actual)."""
    valid_vars = ("temp_min", "temp_max", "lluvia_total", "racha_max", "temp_media", "horas_sol")
    if variable not in valid_vars:
        raise ValueError(f"variable '{variable}' no válida. Opciones: {list(valid_vars)}")
    if order not in ("asc", "desc"):
        raise ValueError("order debe ser 'asc' o 'desc'")
    current_year = _today().year
    start_year = start_year if start_year is not None else DATA_START_YEAR
    end_y = end_year if end_year is not None else current_year
    if start_year > end_y:
        raise ValueError("start_year no puede ser posterior a end_year")
    top_clamped = max(1, min(top, 100))
    rows = await _get_dayfile_rows(start_year=start_year, end_year=end_y)
    ranked = rank(rows, variable, top=top_clamped, order=order)
    res = {
        "variable": variable,
        "unidad": UNITS.get(variable, ""),
        "orden": order,
        "periodo": {"start_year": start_year, "end_year": end_y},
        "top": ranked,
    }
    return json.dumps(res, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def year_records(year: int) -> str:
    """Récords y estadísticas anuales a partir del dayfile del año indicado.
    Devuelve JSON con las temperaturas extremas absolutas, racha máxima, precipitación acumulada,
    día más lluvioso, días de helada y días cálidos."""
    if not 2000 <= year <= 2050:
        raise ValueError("year fuera de rango (2000-2050)")
    all_rows = await _all_dayfile()
    rows = [r for r in all_rows if r["_date"].year == year]
    if not rows:
        raise ValueError(f"No hay datos registrados en el dayfile para el año {year}")
    res = compute_year_records(rows, year, _today())
    return json.dumps(res, ensure_ascii=False)


# ---------------------------------------------------------------- análisis sobre el histórico ----
# Calculados sobre el dayfile cacheado: no requieren endpoints adicionales de CumulusMX.

_OPS: dict[str, Any] = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "==": operator.eq}
_NON_NUMERIC_COLS = {"fecha", "n", "dir_racha", "dir_viento_dominante"}
MAX_LIST_ITEMS = 366


def _numeric_var(variable: str) -> str:
    """Resuelve alias y exige una columna numérica del dayfile."""
    col = resolve_columns([variable])[-1]
    if col in _NON_NUMERIC_COLS or col.startswith("hora_"):
        raise ValueError(f"'{variable}' no es una columna numérica del dayfile")
    return col


def _select_rows(rows: list[dict], start: str = "", end: str = "") -> list[dict]:
    """Filas del dayfile ordenadas por fecha dentro de [start, end] (ambos opcionales)."""
    s = _check_date(start, "start") if start else None
    e = _check_date(end, "end") if end else None
    if s and e and e < s:
        raise ValueError("end anterior a start")
    sel = [r for r in rows if (s is None or r["_date"] >= s) and (e is None or r["_date"] <= e)]
    return sorted(sel, key=lambda r: r["_date"])


def count_matching(rows: list[dict], col: str, op: str, value: float) -> tuple[int, list[dict]]:
    """(días evaluados con dato, filas que cumplen `col op value`)."""
    fn = _OPS[op]
    evaluated, hits = 0, []
    for r in rows:
        v = _num(r.get(col))
        if v is None:
            continue
        evaluated += 1
        if fn(v, value):
            hits.append(r)
    return evaluated, hits


def compute_streaks(rows: list[dict], col: str, op: str, value: float, min_length: int = 2) -> list[dict]:
    """Rachas de días CONSECUTIVOS que cumplen `col op value`. Un día sin dato o un hueco de fechas corta la racha."""
    fn = _OPS[op]
    runs: list[dict] = []
    cur: list[datetime.date] = []

    def close() -> None:
        if len(cur) >= min_length:
            runs.append({"dias": len(cur), "desde": str(cur[0]), "hasta": str(cur[-1])})

    for r in sorted(rows, key=lambda x: x["_date"]):
        d = r["_date"]
        v = _num(r.get(col))
        if v is not None and fn(v, value):
            if cur and (d - cur[-1]).days == 1:
                cur.append(d)
            else:
                close()
                cur = [d]
        else:
            close()
            cur = []
    close()
    return sorted(runs, key=lambda x: (-x["dias"], x["desde"]))


def _shift_year(d: datetime.date, year: int) -> datetime.date:
    try:
        return d.replace(year=year)
    except ValueError:  # 29 de febrero
        return d.replace(year=year, day=28)


def compute_anomaly(rows: list[dict], col: str, start: datetime.date, end: datetime.date,
                    base_years: list[int], min_coverage: float = 0.8) -> dict:
    """Compara el periodo [start, end] con la misma ventana calendario de `base_years`.
    Columnas aditivas (lluvia, horas de sol...) se SUMAN; el resto se promedia."""
    additive = col in ADDITIVE_COLUMNS
    last = max((r["_date"] for r in rows), default=None)
    if last is None or last < start:
        raise ValueError("No hay datos en el dayfile para el periodo indicado")
    eff_end = min(end, last)  # periodo en curso: se compara solo la parte transcurrida
    span = (eff_end - start).days

    def stat(s: datetime.date, e: datetime.date) -> tuple[float | None, int]:
        vals = [v for r in rows if s <= r["_date"] <= e and (v := _num(r.get(col))) is not None]
        needed = max(1, math.ceil(min_coverage * ((e - s).days + 1)))
        if len(vals) < needed:
            return None, len(vals)
        return (round(sum(vals), 2) if additive else round(statistics.fmean(vals), 2)), len(vals)

    value, n_days = stat(start, eff_end)
    if value is None:
        raise ValueError(f"Datos insuficientes de '{col}' en el periodo (cobertura mínima {int(min_coverage * 100)} %)")

    by_year: dict[int, float] = {}
    for y in base_years:
        s_y = _shift_year(start, y)
        v, _ = stat(s_y, s_y + datetime.timedelta(days=span))
        if v is not None:
            by_year[y] = v
    if not by_year:
        raise ValueError("No hay años de referencia con datos suficientes para comparar")

    normal = round(statistics.fmean(by_year.values()), 2)
    diff = round(value - normal, 2)
    below = sum(1 for v in by_year.values() if v < value)
    above = sum(1 for v in by_year.values() if v > value)
    out: dict[str, Any] = {
        "variable": col,
        "unidad": UNITS.get(col, ""),
        "metodo": "suma" if additive else "media",
        "periodo": {"start": str(start), "end": str(eff_end), "dias_con_dato": n_days,
                    "parcial": eff_end < end},
        "valor": value,
        "normal": normal,
        "diferencia": diff,
        "diferencia_pct": round(100 * diff / normal, 1) if additive and normal else None,
        "percentil": round(100 * below / len(by_year)),
        "posicion_entre_anios": 1 + above,
        "anios_comparados": len(by_year) + 1,
        "por_anio": {str(y): v for y, v in sorted(by_year.items())},
    }
    if len(by_year) < 3:
        out["advertencia"] = "Pocos años de referencia (<3): la 'normal' es poco representativa."
    return out


def check_quality(rows: list[dict], max_items: int = 100) -> dict:
    """Huecos de fechas y valores incoherentes del dayfile (independiente de las unidades)."""
    rows = sorted(rows, key=lambda r: r["_date"])
    if not rows:
        return {"n_dias": 0}
    present = {r["_date"] for r in rows}
    first, last = rows[0]["_date"], rows[-1]["_date"]
    missing = [str(first + datetime.timedelta(days=i)) for i in range((last - first).days + 1)
               if first + datetime.timedelta(days=i) not in present]
    issues: list[dict] = []
    no_temp: list[str] = []

    def flag(r: dict, problem: str) -> None:
        issues.append({"fecha": str(r["_date"]), "problema": problem})

    for r in rows:
        tmin, tmax = _num(r.get("temp_min")), _num(r.get("temp_max"))
        if tmin is None and tmax is None:
            no_temp.append(str(r["_date"]))
        elif tmin is not None and tmax is not None and tmin > tmax:
            flag(r, f"temp_min ({tmin}) > temp_max ({tmax})")
        hmin, hmax = _num(r.get("hum_min")), _num(r.get("hum_max"))
        for name, h in (("hum_min", hmin), ("hum_max", hmax)):
            if h is not None and not 0 <= h <= 100:
                flag(r, f"{name} fuera de 0-100 ({h})")
        if hmin is not None and hmax is not None and hmin > hmax:
            flag(r, f"hum_min ({hmin}) > hum_max ({hmax})")
        for name, h in (("hum_min", hmin), ("hum_max", hmax)):
            if h is not None and h == 0:
                flag(r, f"{name} = 0 % (posible fallo del sensor)")
        pmin, pmax = _num(r.get("pres_min")), _num(r.get("pres_max"))
        if pmin is not None and pmax is not None and pmin > pmax:
            flag(r, f"pres_min ({pmin}) > pres_max ({pmax})")
        for name in ("lluvia_total", "horas_sol"):
            v = _num(r.get(name))
            if v is not None and v < 0:
                flag(r, f"{name} negativo ({v})")

    return {
        "n_dias": len(rows), "desde": str(first), "hasta": str(last),
        "dias_esperados": (last - first).days + 1,
        "dias_faltantes": {"total": len(missing), "fechas": missing[:max_items]},
        "dias_sin_temperatura": {"total": len(no_temp), "fechas": no_temp[:max_items]},
        "valores_incoherentes": {"total": len(issues), "detalle": issues[:max_items]},
        "ok": not missing and not issues and not no_temp,
    }


@mcp.tool(annotations=READ_ONLY)
async def count_days(variable: str, op: Literal[">", ">=", "<", "<=", "=="], value: float,
                     start: str = "", end: str = "", list_days: bool = False) -> str:
    """Cuenta los días del histórico que cumplen una condición sobre una columna del dayfile, con desglose por año.
    Ej.: variable='temp_max', op='>=', value=35 -> días de calor extremo; 'rain' '>=' 0.2 -> días de lluvia;
    'temp_min' '<=' 0 -> días de helada. variable acepta nombres o alias (ver dayfile_range).
    start/end opcionales (yyyy-mm-dd; por defecto todo el histórico). list_days=True lista las fechas (máx 366).
    Compara en las unidades de la estación."""
    col = _numeric_var(variable)
    rows = _select_rows(await _all_dayfile(), start, end)
    evaluated, hits = count_matching(rows, col, op, value)
    by_year: dict[str, int] = {}
    for r in hits:
        by_year[str(r["_date"].year)] = by_year.get(str(r["_date"].year), 0) + 1
    out: dict[str, Any] = {
        "condicion": f"{col} {op} {value:g}", "unidad": UNITS.get(col, ""),
        "periodo": {"desde": str(rows[0]["_date"]) if rows else None, "hasta": str(rows[-1]["_date"]) if rows else None},
        "dias_evaluados": evaluated, "dias_que_cumplen": len(hits),
        "por_anio": dict(sorted(by_year.items())),
    }
    if list_days:
        out["fechas"] = [str(r["_date"]) for r in hits[:MAX_LIST_ITEMS]]
        out["lista_truncada"] = len(hits) > MAX_LIST_ITEMS
    return json.dumps(out, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def find_streaks(variable: str, op: Literal[">", ">=", "<", "<=", "=="], value: float,
                       start: str = "", end: str = "", min_length: int = 2, top: int = 5) -> str:
    """Rachas más largas de días CONSECUTIVOS que cumplen una condición (un día sin dato corta la racha).
    Ej.: sequía = variable='rain', op='<', value=0.2; ola de calor = 'temp_max' '>=' 35; helada = 'temp_min' '<=' 0.
    top: nº de rachas a devolver (1-50). min_length: longitud mínima en días.
    Indica también si hay una racha en curso (termina en el último día con datos)."""
    col = _numeric_var(variable)
    rows = _select_rows(await _all_dayfile(), start, end)
    runs = compute_streaks(rows, col, op, value, max(1, min_length))
    last_day = str(rows[-1]["_date"]) if rows else None
    out = {
        "condicion": f"{col} {op} {value:g}", "unidad": UNITS.get(col, ""),
        "periodo": {"desde": str(rows[0]["_date"]) if rows else None, "hasta": last_day},
        "n_rachas": len(runs),
        "rachas": runs[:max(1, min(top, 50))],
        "racha_en_curso": next((r for r in runs if r["hasta"] == last_day), None),
    }
    return json.dumps(out, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def anomaly(start: str, end: str, variable: str = "temp_media",
                  baseline_start_year: int | None = None, baseline_end_year: int | None = None) -> str:
    """Compara un periodo con la climatología propia de la estación: el mismo tramo del calendario en los demás años.
    Responde a '¿este mes fue más cálido/seco de lo normal?'. variable: nombre o alias (por defecto temp_media);
    lluvia y horas de sol se SUMAN, el resto se promedia. Devuelve valor, normal, diferencia (y % en acumulados),
    percentil, posición entre años y valores por año. Si el periodo está en curso compara solo la parte transcurrida.
    baseline_start_year por defecto = CUMULUS_DATA_START_YEAR. Años con <80 % de datos en la ventana se descartan."""
    s, e = _check_date(start, "start"), _check_date(end, "end")
    if e < s:
        raise ValueError("end anterior a start")
    col = _numeric_var(variable)
    rows = await _all_dayfile()
    lo = baseline_start_year if baseline_start_year is not None else DATA_START_YEAR
    hi = baseline_end_year if baseline_end_year is not None else 2050
    years = sorted({r["_date"].year for r in rows if lo <= r["_date"].year <= hi and r["_date"].year != s.year})
    return json.dumps(compute_anomaly(rows, col, s, e, years), ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def data_quality(start: str = "", end: str = "", max_items: int = 100) -> str:
    """Revisa la calidad del histórico diario: días sin registro, días sin temperatura y valores incoherentes
    (mín > máx, humedad fuera de 0-100, lluvia o sol negativos). Útil para detectar caídas de la estación o
    sensores averiados antes de fiarse de los récords. max_items limita las listas (1-366)."""
    rows = _select_rows(await _all_dayfile(), start, end)
    return json.dumps(check_quality(rows, max(1, min(max_items, MAX_LIST_ITEMS))), ensure_ascii=False)


# ---------------------------------------------------------------- análisis climáticos avanzados ----

RAIN_DAY_MM = 0.2  # umbral de "día de lluvia" (el mismo que usa _agg / year_records)
COMPASS_16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def _month_stat(rows: list[dict], col: str, days: int, how: str, min_coverage: float) -> float | None:
    """Estadístico mensual de una columna; None si hay menos de `min_coverage` de días con dato."""
    vals = [v for r in rows if (v := _num(r.get(col))) is not None]
    if len(vals) < max(1, math.ceil(min_coverage * days)):
        return None
    if how == "sum":
        return sum(vals)
    if how == "mean":
        return statistics.fmean(vals)
    if how == "max":
        return max(vals)
    if how == "min":
        return min(vals)
    if how == "rain_days":
        return float(sum(1 for v in vals if v >= RAIN_DAY_MM))
    raise ValueError(how)


def compute_climatology(rows: list[dict], min_coverage: float = 0.8) -> dict:
    """Normales climáticas por mes del calendario a partir de los meses COMPLETOS (>= min_coverage de días con dato,
    respetando años bisiestos). Para cada mes: medias entre años, extremos absolutos y años récord."""
    by_ym: dict[tuple[int, int], list[dict]] = {}
    for r in rows:
        by_ym.setdefault((r["_date"].year, r["_date"].month), []).append(r)

    per_month: dict[int, list[dict]] = {m: [] for m in range(1, 13)}
    for (y, m), rs in sorted(by_ym.items()):
        days = calendar.monthrange(y, m)[1]
        spec = {"temp_media": ("temp_media", "mean"), "temp_max_media": ("temp_max", "mean"),
                "temp_min_media": ("temp_min", "mean"), "temp_max_abs": ("temp_max", "max"),
                "temp_min_abs": ("temp_min", "min"), "lluvia_total": ("lluvia_total", "sum"),
                "dias_lluvia": ("lluvia_total", "rain_days"), "horas_sol": ("horas_sol", "sum"),
                "racha_max": ("racha_max", "max")}
        rec: dict[str, Any] = {"anio": y}
        for key, (col, how) in spec.items():
            rec[key] = _month_stat(rs, col, days, how, min_coverage)
        if any(v is not None for k, v in rec.items() if k != "anio"):
            per_month[m].append(rec)

    def avg(recs: list[dict], key: str) -> float | None:
        vals = [r[key] for r in recs if r[key] is not None]
        return round(statistics.fmean(vals), 2) if vals else None

    def best(recs: list[dict], key: str, fn) -> dict | None:
        cand = [r for r in recs if r[key] is not None]
        if not cand:
            return None
        b = fn(cand, key=lambda r: r[key])
        return {"anio": b["anio"], "valor": round(b[key], 2)}

    out: dict[str, Any] = {}
    for m, recs in per_month.items():
        if not recs:
            continue
        out[f"{m:02d}"] = {
            "n_anios": len(recs),
            "temp_media": avg(recs, "temp_media"), "temp_max_media": avg(recs, "temp_max_media"),
            "temp_min_media": avg(recs, "temp_min_media"),
            "lluvia_total_media": avg(recs, "lluvia_total"), "dias_lluvia_media": avg(recs, "dias_lluvia"),
            "horas_sol_media": avg(recs, "horas_sol"),
            "temp_max_absoluta": best(recs, "temp_max_abs", max), "temp_min_absoluta": best(recs, "temp_min_abs", min),
            "racha_max": best(recs, "racha_max", max),
            "mes_mas_calido": best(recs, "temp_media", max), "mes_mas_frio": best(recs, "temp_media", min),
            "mes_mas_lluvioso": best(recs, "lluvia_total", max), "mes_mas_seco": best(recs, "lluvia_total", min),
        }
    return out


def compute_frost_dates(rows: list[dict], year: int, threshold: float, hemisphere: str = "north",
                        today: datetime.date | None = None) -> dict:
    """Última helada de primavera y primera de otoño de un año (helada = temp_min <= threshold).
    Hemisferio norte: primavera = ene-jun, otoño = jul-dic. Hemisferio sur: primera y última helada del año natural."""
    yr_rows = [r for r in rows if r["_date"].year == year]
    valid = [(r["_date"], t) for r in yr_rows if (t := _num(r.get("temp_min"))) is not None]
    frost = sorted(d for d, t in valid if t <= threshold)
    if hemisphere == "north":
        spring = [d for d in frost if d.month <= 6]
        autumn = [d for d in frost if d.month >= 7]
        last_spring = spring[-1] if spring else None
        first_autumn = autumn[0] if autumn else None
    else:
        last_spring = frost[-1] if frost else None
        first_autumn = frost[0] if frost else None
    free = None
    if hemisphere == "north" and last_spring and first_autumn:
        free = (first_autumn - last_spring).days - 1
    year_days = 366 if calendar.isleap(year) else 365
    partial = len(valid) < 0.9 * year_days or (today is not None and year == today.year)
    return {
        "anio": year, "n_dias_con_dato": len(valid), "dias_helada": len(frost), "parcial": partial,
        "ultima_helada_primavera": str(last_spring) if last_spring else None,
        "primera_helada_otono": str(first_autumn) if first_autumn else None,
        "periodo_sin_heladas_dias": free,
    }


def _mean_month_day(dates: list[str]) -> str | None:
    """Fecha media (MM-DD) de una lista de fechas ISO, promediando el día del año."""
    if not dates:
        return None
    doys = [datetime.date.fromisoformat(d).timetuple().tm_yday for d in dates]
    ref = datetime.date(2001, 1, 1) + datetime.timedelta(days=round(statistics.fmean(doys)) - 1)
    return ref.strftime("%m-%d")


def compute_degree_days(rows: list[dict], base: float, upper: float | None = None,
                        method: str = "average") -> list[tuple[datetime.date, float]]:
    """Grados-día de crecimiento por día. 'average': max(0, (tmax+tmin)/2 - base).
    'modified': antes de promediar, tmin se sube hasta `base` y tmax se limita a `upper` (método habitual en agronomía)."""
    out: list[tuple[datetime.date, float]] = []
    for r in sorted(rows, key=lambda x: x["_date"]):
        tmin, tmax = _num(r.get("temp_min")), _num(r.get("temp_max"))
        if tmin is None or tmax is None:
            continue
        if method == "modified":
            tmax_c = max(min(tmax, upper) if upper is not None else tmax, base)
            tmin_c = max(tmin, base)
            dd = (tmax_c + tmin_c) / 2 - base
        else:
            dd = (tmax + tmin) / 2 - base
        out.append((r["_date"], max(0.0, dd)))
    return out


def compass_sector(degrees: float) -> str:
    return COMPASS_16[int((degrees % 360) / 22.5 + 0.5) % 16]


def compute_wind_rose(rows: list[dict]) -> dict:
    """Frecuencia por sector (16 rumbos) de la dirección dominante diaria y racha máxima por sector de la racha."""
    dom: collections.Counter = collections.Counter()
    gust_n: collections.Counter = collections.Counter()
    gust_max: dict[str, float] = {}
    for r in rows:
        d = _num(r.get("dir_viento_dominante"))
        if d is not None:
            dom[compass_sector(d)] += 1
        gd, gv = _num(r.get("dir_racha")), _num(r.get("racha_max"))
        if gd is not None and gv is not None:
            sec = compass_sector(gd)
            gust_n[sec] += 1
            gust_max[sec] = max(gust_max.get(sec, gv), gv)
    total = sum(dom.values())
    sectors = [{"sector": s, "dias": dom[s], "pct": round(100 * dom[s] / total, 1) if total else 0.0}
               for s in COMPASS_16]
    top = max(COMPASS_16, key=lambda s: dom[s]) if total else None
    return {
        "dias_con_direccion": total, "sector_mas_frecuente": top, "sectores": sectors,
        "rachas_por_sector": [{"sector": s, "dias_con_racha_maxima_en_este_sector": gust_n[s],
                               "racha_max": round(gust_max[s], 1)} for s in COMPASS_16 if gust_n[s]],
    }


def percentile_in_history(rows: list[dict], col: str, value: float, month: int, exclude: datetime.date,
                          min_samples: int = 5) -> dict | None:
    """Posición de `value` entre los valores del mismo mes del calendario en el resto del histórico."""
    vals = [v for r in rows if r["_date"].month == month and r["_date"] != exclude
            and (v := _num(r.get(col))) is not None]
    if len(vals) < min_samples:
        return None
    return {"valor": value, "percentil": round(100 * sum(1 for v in vals if v < value) / len(vals)),
            "posicion": 1 + sum(1 for v in vals if v > value), "de": len(vals) + 1}


def _ongoing_run(rows: list[dict], col: str, op: str, value: float, last: datetime.date) -> int:
    """Días consecutivos, hasta el último día con datos, que cumplen la condición (0 si no hay racha en curso)."""
    for run in compute_streaks(rows, col, op, value, min_length=1):
        if run["hasta"] == str(last):
            return run["dias"]
    return 0


@mcp.tool(annotations=READ_ONLY)
async def climatology(start_year: int | None = None, end_year: int | None = None) -> str:
    """Normales climáticas de la estación por mes del calendario (enero-diciembre), calculadas con los meses completos
    del histórico (>= 80 % de días con dato): temperatura media/máx/mín, lluvia total y días de lluvia medios, horas de
    sol, extremos absolutos y el año más cálido/frío/lluvioso/seco de cada mes. Sirve para preguntas como
    '¿cuánto llueve normalmente en abril?' y como referencia de anomaly. start_year por defecto = CUMULUS_DATA_START_YEAR."""
    lo = start_year if start_year is not None else DATA_START_YEAR
    hi = end_year if end_year is not None else _today().year
    if lo > hi:
        raise ValueError("start_year no puede ser posterior a end_year")
    rows = [r for r in await _all_dayfile() if lo <= r["_date"].year <= hi]
    if not rows:
        raise ValueError(f"No hay datos en el dayfile entre {lo} y {hi}")
    out = {"periodo": {"start_year": lo, "end_year": hi}, "anios_con_datos": sorted({r["_date"].year for r in rows}),
           "meses": compute_climatology(rows),
           "nota": "Solo cuentan los meses con >= 80 % de días con dato; unidades de la estación."}
    return json.dumps(out, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def frost_dates(threshold: float | None = None, start_year: int | None = None, end_year: int | None = None,
                      hemisphere: Literal["north", "south"] = "north") -> str:
    """Fechas de heladas por año: última helada de primavera, primera de otoño, días de helada y periodo sin heladas
    (días entre ambas, hemisferio norte). Helada = temp_min <= threshold (por defecto CUMULUS_FROST_THRESHOLD, 0 °C).
    Incluye fecha media (MM-DD) de las dos, calculada solo con años completos: útil para planificar siembras y
    trasplantes. Los años incompletos se marcan 'parcial'. Hemisferio norte: primavera = ene-jun, otoño = jul-dic."""
    thr = threshold if threshold is not None else FROST_THRESHOLD
    lo = start_year if start_year is not None else DATA_START_YEAR
    hi = end_year if end_year is not None else _today().year
    if lo > hi:
        raise ValueError("start_year no puede ser posterior a end_year")
    rows = await _all_dayfile()
    years = sorted({r["_date"].year for r in rows if lo <= r["_date"].year <= hi})
    if not years:
        raise ValueError(f"No hay datos en el dayfile entre {lo} y {hi}")
    per_year = [compute_frost_dates(rows, y, thr, hemisphere, _today()) for y in years]
    complete = [y for y in per_year if not y["parcial"]]
    out = {
        "umbral": thr, "hemisferio": hemisphere, "anios": per_year,
        "fecha_media": {
            "ultima_helada_primavera": _mean_month_day([y["ultima_helada_primavera"] for y in complete
                                                        if y["ultima_helada_primavera"]]),
            "primera_helada_otono": _mean_month_day([y["primera_helada_otono"] for y in complete
                                                     if y["primera_helada_otono"]]),
            "anios_completos": len(complete),
        },
    }
    return json.dumps(out, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def degree_days(start: str = "", end: str = "", base: float = 10.0, upper: float | None = None,
                      method: Literal["average", "modified"] = "average") -> str:
    """Grados-día de crecimiento (GDD) acumulados con temp_min/temp_max diarias, en las unidades de la estación.
    base: temperatura base del cultivo (10 por defecto; 0, 5, 10... según cultivo). method='average': max(0, media - base);
    'modified': tmin se sube a base y tmax se limita a `upper` antes de promediar (recomendado con upper, p. ej. 30).
    Por defecto desde el 1 de enero del año actual hasta el último día con datos. Devuelve total, por mes y por año.
    (CumulusMX ya trae grados-día de calefacción/refrigeración en las columnas grados_dia_calef / grados_dia_refr.)"""
    if upper is not None and upper <= base:
        raise ValueError("upper debe ser mayor que base")
    s = start or f"{_today().year}-01-01"
    rows = _select_rows(await _all_dayfile(), s, end)
    series = compute_degree_days(rows, base, upper, method)
    by_month: dict[str, float] = {}
    by_year: dict[str, float] = {}
    for d, v in series:
        by_month[f"{d.year}-{d.month:02d}"] = by_month.get(f"{d.year}-{d.month:02d}", 0.0) + v
        by_year[str(d.year)] = by_year.get(str(d.year), 0.0) + v
    out = {
        "base": base, "techo": upper, "metodo": method,
        "periodo": {"desde": str(series[0][0]) if series else None, "hasta": str(series[-1][0]) if series else None},
        "dias_evaluados": len(series), "total": round(sum(v for _, v in series), 1),
        "por_mes": {k: round(v, 1) for k, v in by_month.items()},
        "por_anio": {k: round(v, 1) for k, v in by_year.items()},
    }
    return json.dumps(out, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def wind_rose(start: str = "", end: str = "") -> str:
    """Rosa de los vientos del histórico diario (16 rumbos): cuántos días domina cada dirección (dir_viento_dominante) y la
    racha máxima registrada con cada dirección de racha (dir_racha). start/end opcionales (yyyy-mm-dd; por defecto todo).
    Un rumbo de 0 puede significar calma en CumulusMX: interprétalo con cautela."""
    rows = _select_rows(await _all_dayfile(), start, end)
    out = compute_wind_rose(rows)
    out["periodo"] = {"desde": str(rows[0]["_date"]) if rows else None, "hasta": str(rows[-1]["_date"]) if rows else None}
    return json.dumps(out, ensure_ascii=False)


BRIEFING_TAGS = ["temp", "feelslike", "dew", "hum", "press", "presstrend", "wlatest", "wgust", "currentwdir", "rrate",
                 "rfall", "SolarRad", "UV", "tempTH", "TtempTH", "tempTL", "TtempTL", "DataStopped", "time", "date",
                 "tempunit", "pressunit", "windunit", "rainunit"]


@mcp.tool(annotations=READ_ONLY)
async def daily_briefing() -> str:
    """Resumen del día en una sola llamada: condiciones ahora y extremos de hoy; AYER en contexto (percentil de su Tmax,
    Tmín y lluvia frente al mismo mes de otros años); mes en curso frente a lo normal (temperatura y lluvia acumulada);
    y rachas en curso (días sin lluvia, con Tmax >= 30 y de helada). Combina datos en vivo con el histórico diario,
    que llega hasta el último día cerrado ('datos_hasta')."""
    tags_text, rows_or_err = await asyncio.gather(_get(_tags_path(BRIEFING_TAGS)), _all_dayfile(), return_exceptions=True)
    out: dict[str, Any] = {"fecha_servidor": str(_today())}
    try:
        tags = json.loads(tags_text) if isinstance(tags_text, str) else {}
    except ValueError:
        tags = {}
        out["aviso_ahora"] = str(tags_text)[:200]
    out["ahora"] = {k: (n if (n := _num(v)) is not None else _clean(v)) if isinstance(v, str) else v
                    for k, v in tags.items()}
    if isinstance(rows_or_err, Exception) or not rows_or_err:
        out["aviso_historico"] = f"Sin histórico disponible: {rows_or_err}" if isinstance(rows_or_err, Exception) \
            else "El dayfile está vacío"
        return json.dumps(out, ensure_ascii=False)

    rows = rows_or_err
    today = _today()
    last = max(r["_date"] for r in rows)
    out["datos_hasta"] = str(last)
    if (today - last).days > 2:
        out["aviso_historico"] = f"El histórico llega solo hasta {last}: puede estar desactualizado."

    last_row = next(r for r in rows if r["_date"] == last)
    context: dict[str, Any] = {}
    for col in ("temp_max", "temp_min", "lluvia_total"):
        v = _num(last_row.get(col))
        context[col] = percentile_in_history(rows, col, v, last.month, last) if v is not None else None
    out["ultimo_dia_cerrado_en_contexto"] = {"fecha": str(last), **context}

    month_start = today.replace(day=1)
    years = sorted({r["_date"].year for r in rows if r["_date"].year != today.year and r["_date"].year >= DATA_START_YEAR})
    month: dict[str, Any] = {}
    for col in ("temp_media", "lluvia_total"):
        try:
            a = compute_anomaly(rows, col, month_start, today, years)
            month[col] = {k: a[k] for k in ("valor", "normal", "diferencia", "diferencia_pct", "percentil",
                                            "posicion_entre_anios", "anios_comparados") if k in a}
        except ValueError as e:
            month[col] = {"aviso": str(e)}
    out["mes_en_curso"] = month

    stale = (today - last).days > 2
    out["rachas_en_curso"] = None if stale else {
        "dias_sin_lluvia": _ongoing_run(rows, "lluvia_total", "<", RAIN_DAY_MM, last),
        "dias_con_tmax_ge_30": _ongoing_run(rows, "temp_max", ">=", WARM_THRESHOLDS[1], last),
        "dias_de_helada": _ongoing_run(rows, "temp_min", "<=", FROST_THRESHOLD, last),
    }
    return json.dumps(out, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def today_all(strip_html: bool = True) -> str:
    """Hoy vs ayer de TODAS las magnitudes (temp, hum, rain, wind, pressure, solar) en una sola llamada."""
    keys = ("temp", "hum", "rain", "wind", "pressure", "solar")
    texts = await asyncio.gather(*(_get(f"/api/todayyest/{k}.json") for k in keys))
    res = {k: (json.loads(t) if t.startswith("{") else t) for k, t in zip(keys, texts)}
    s = json.dumps(res, ensure_ascii=False)
    return _clean(s) if strip_html else s


@mcp.tool(annotations=READ_ONLY)
async def records_all(scope: Literal["alltime", "thismonth", "thisyear", "month"],
                      month: int = 0,
                      strip_html: bool = True) -> str:
    """Todos los récords (temperatura, humedad, presión, viento, lluvia) del ámbito elegido en una llamada.
    Si scope='month', indicar month (1-12)."""
    keys = ("temperature", "humidity", "pressure", "wind", "rain")
    if scope == "month" and not 1 <= month <= 12:
        raise ValueError("month debe estar entre 1 y 12")
    paths = [f"/api/records/month/{month}/{k}.json" if scope == "month" else f"/api/records/{scope}/{k}.json"
             for k in keys]
    texts = await asyncio.gather(*(_get(p) for p in paths))
    res = {}
    for k, t in zip(keys, texts):
        try:
            res[k] = json.loads(t)
        except ValueError:
            res[k] = t
    s = json.dumps(res, ensure_ascii=False)
    return _clean(s) if strip_html else s


@mcp.tool(annotations=READ_ONLY)
async def snapshot() -> str:
    """Foto rápida de la estación: temperatura, sensación, humedad, rocío, presión y tendencia, viento/racha/dirección,
    lluvia (hoy/mes/año/tasa), sol/UV, extremos de hoy, amanecer/atardecer, luna y estado de la estación.
    Los números llegan con punto decimal (formato 'rc' de CumulusMX); las unidades van en los tags *unit."""
    tags = ["temp", "tempunit", "feelslike", "dew", "hum", "press", "presstrend", "pressunit", "wlatest", "wgust",
            "bearing", "currentwdir", "windunit", "rrate", "rfall", "rmonth", "ryear", "rainunit", "SolarRad", "UV",
            "tempTH", "TtempTH", "tempTL", "TtempTL", "wspeedTH", "pressTH", "pressTL", "humTH", "humTL",
            "sunrise", "sunset", "daylength", "moonphase", "DataStopped", "version", "build", "stationtype", "time", "date"]
    return await _get(_tags_path(tags))


@mcp.tool(annotations=READ_ONLY)
async def render_template(template: str) -> str:
    """Evalúa una plantilla de webtags con modificadores, p.ej.
    '<#temp dp=2> <#tempH> <#time format="HH:mm"> <#RecentOutsideTemp d=1>'. Solo se admiten etiquetas <#nombre ...> y texto simple."""
    if len(template) > 1000:
        raise ValueError("plantilla demasiado larga (máx 1000)")
    if not re.fullmatch(r"[A-Za-z0-9_ .,:;%/\-=\"'<>#()\[\]]*", template) or "<#" not in template:
        raise ValueError("plantilla con caracteres no permitidos o sin etiquetas <#...>")
    return await _post("/api/tags/process.txt", content=template.encode(), headers={"Content-Type": "text/plain"})


@mcp.tool(annotations=READ_ONLY)
async def logfile(start: str, end: str, offset: int = 0, length: int = 100, extra: bool = False) -> str:
    """Log de intervalo (datos cada pocos minutos) entre dos fechas yyyy-mm-dd, paginado (length máx 300).
    extra=True para el log de sensores extra."""
    s, e = _check_date(start, "start"), _check_date(end, "end")
    if e < s or (e - s).days > 31:
        raise ValueError("rango inválido (máx 31 días)")
    p = {"from": start, "to": end, "draw": 1, "start": max(0, offset), "length": max(1, min(length, 300)),
         "search[value]": ""}
    return await _get("/api/data/extralogfile" if extra else "/api/data/logfile", p)


@mcp.tool(annotations=READ_ONLY)
async def recent_data(offset: int = 0, length: int = 100) -> str:
    """Tabla de datos recientes de la estación (últimas horas), paginada (length máx 300)."""
    return await _get("/api/data/recentdata", {"draw": 1, "start": max(0, offset),
                                               "length": max(1, min(length, 300)), "search[value]": ""})


@mcp.tool(annotations=READ_ONLY)
async def diary(date: str = "") -> str:
    """Diario meteorológico: si das date (yyyy-mm-dd) devuelve esa entrada; sin fecha, el resumen de días con anotación."""
    if date:
        _check_date(date, "date")
        return await _get("/api/data/diarydata", {"date": date})
    return await _get("/api/data/diarysummary")


SIMPLE_REPORTS: dict[str, str] = {
    "temperature": "simpleTemperature", "rainfall": "simpleRainfall", "windrun": "simpleWindRun",
    "sunshine": "simpleSunshine", "et": "simpleET", "drydays": "simpleDryDays", "wetdays": "simpleWetDays",
}

EXTRA_SENSORS: tuple[str, ...] = (
    "temp", "hum", "dew", "soiltemp", "soilmoisture", "soilec", "leaf8", "airqual", "lightning", "usertemp",
    "laserdepth", "laserdistance", "snow24h", "snowseason", "airLinkCountsOut", "airLinkAqiOut", "airLinkPctOut",
    "airLinkCountsIn", "airLinkAqiIn", "airLinkPctIn", "co2sensor", "bgt",
)


@mcp.tool(annotations=READ_ONLY)
async def simple_report(
    report: Literal["temperature", "rainfall", "windrun", "sunshine", "et", "drydays", "wetdays"],
    text: bool = True,
) -> str:
    """Informes resumen de CumulusMX calculados sobre todo el histórico diario (mes a mes por año):
    temperature, rainfall, windrun, sunshine, et (evapotranspiración), drydays y wetdays (días secos/lluviosos
    según el umbral de lluvia de la estación). text=True devuelve texto plano; False, HTML.
    Respeta las unidades y decimales configurados en CumulusMX."""
    if report not in SIMPLE_REPORTS:
        raise ValueError(f"report debe ser uno de {sorted(SIMPLE_REPORTS)}")
    return await _get(f"/api/reports/{SIMPLE_REPORTS[report]}", {"format": "text"} if text else None)


@mcp.tool(annotations=READ_ONLY)
async def extra_sensors(sensor: Literal[
    "temp", "hum", "dew", "soiltemp", "soilmoisture", "soilec", "leaf8", "airqual", "lightning", "usertemp",
    "laserdepth", "laserdistance", "snow24h", "snowseason", "airLinkCountsOut", "airLinkAqiOut", "airLinkPctOut",
    "airLinkCountsIn", "airLinkAqiIn", "airLinkPctIn", "co2sensor", "bgt",
]) -> str:
    """Valores actuales de los sensores extra de CumulusMX: temp/hum/dew (extra), soiltemp, soilmoisture, soilec,
    leaf8 (humectación foliar), airqual, lightning, usertemp, laserdepth/laserdistance, snow24h/snowseason,
    airLink* (calidad del aire Davis AirLink, exterior 'Out' / interior 'In'), co2sensor y bgt (globo negro).
    Solo devuelve datos de los sensores que estén configurados en la estación."""
    if sensor not in EXTRA_SENSORS:
        raise ValueError(f"sensor debe ser uno de {list(EXTRA_SENSORS)}")
    return await _get(f"/api/extra/{sensor}.json")


ALLOWED_GET = re.compile(
    r"^/api/(data/(currentdata|dayfile|logfile|extralogfile|recentdata|diarydata|diarysummary)"
    r"|tags/process\.json|graphdata/[a-z0-9]+\.json|dailygraphdata/[a-z0-9]+\.json"
    r"|records/[a-z0-9/]+\.json|todayyest/[a-z]+\.json|extra/[A-Za-z0-9_.]+|info/[a-z]+\.json"
    r"|reports/[A-Za-z0-9]+)$")


@mcp.tool(annotations=READ_ONLY)
async def api_get(path: str, query: dict[str, str] | None = None) -> str:
    """Acceso genérico de SOLO LECTURA a endpoints GET de CumulusMX por lista blanca (p.ej. path='/api/extra/xyz').
    Prohibido: settings, utils, genreports, uploadreport, mysqlcache, errorlog, station/*."""
    if ".." in path or not ALLOWED_GET.fullmatch(path):
        raise ValueError("ruta no permitida (solo lectura, lista blanca)")
    q = {str(k)[:50]: str(v)[:200] for k, v in (query or {}).items()}
    return await _get(path, q or None)


@mcp.tool(annotations=READ_ONLY)
async def server_status() -> str:
    """Diagnóstico: comprueba que CumulusMX responde y devuelve su versión."""
    ver = await _get("/api/info/version.json")
    return json.dumps({"mcp": "cumulusmx-mcp", "mcp_version": __version__, "cumulusmx_version": ver},
                      ensure_ascii=False)


# ---------------------------------------------------------------- utilidades ----

ALL_ALARMS = (
    "DataStopped", "BatteryLow", "Sensor", "Spike", "WindHigh", "WindGust",
    "RainRate", "Rainfall", "PressUp", "PressDown", "PressHigh", "PressLow",
    "TempUp", "TempDown", "TempLow", "TempHigh", "Upgrade", "Firmware",
    "Thirdparty", "MySQL", "IsRaining", "Record", "FTP", "Error",
)


@write_tool
async def reloaddayfile() -> str:
    """Fuerza a CumulusMX a recargar dayfile.txt desde el disco e invalida la caché local en memoria."""
    if (err := _check_write_enabled()):
        return err
    async with _dayfile_lock:
        _cached_dayfile_by_year.clear()
        global _dayfile_cache_time
        _dayfile_cache_time = 0.0
    res = await _get("/api/utils/reloaddayfile")
    return f"Caché local de dayfile invalidada. CumulusMX: {res}"


@write_tool
async def clear_alarms(alarm: str = "all") -> str:
    """Restablece una alarma específica (p.ej. 'BatteryLow', 'AlarmSensor') o todas las alarmas en CumulusMX."""
    if (err := _check_write_enabled()):
        return err
    target = alarm.strip()
    if target.lower() == "all":
        results = []
        for name in ALL_ALARMS:
            alarm_id = f"Alarm{name}"
            r = await _post("/api/utils/clearalarm.txt", content=alarm_id.encode("utf-8"), headers={"Content-Type": "text/plain"})
            results.append(f"{name}: {r.strip()}")
        return "Alarmas restablecidas:\n" + "\n".join(results)
    else:
        alarm_id = target if target.startswith("Alarm") else f"Alarm{target}"
        return await _post("/api/utils/clearalarm.txt", content=alarm_id.encode("utf-8"), headers={"Content-Type": "text/plain"})


@write_tool
async def clear_error_log() -> str:
    """Borra el registro de errores acumulado en CumulusMX."""
    if (err := _check_write_enabled()):
        return err
    return await _post("/api/utils/clearerrorlog.json")


@write_tool
async def trigger_ftp(
    dailygraphs: bool = False,
    noaa: bool = False,
    graphs: bool = False,
    logfiles: bool = False,
) -> str:
    """Dispara inmediatamente una subida o copia local por FTP/Web en CumulusMX."""
    if (err := _check_write_enabled()):
        return err
    payload = json.dumps({
        "dailygraphs": dailygraphs,
        "noaa": noaa,
        "graphs": graphs,
        "logfiles": logfiles,
    })
    return await _post("/api/utils/ftpnow.json", content=payload, headers={"Content-Type": "application/json"})


@write_tool
async def purgemysql() -> str:
    """Purga la cola de comandos pendientes/fallidos y la caché MySQL en CumulusMX."""
    if (err := _check_write_enabled()):
        return err
    return await _get("/api/utils/purgemysql")


# ---------------------------------------------------------------- informes ----

@write_tool
async def generate_reports(
    report_type: Literal["year", "month", "all"] = "month",
    year: int | None = None,
    month: int | None = None,
) -> str:
    """Genera informes NOAA anuales o mensuales en CumulusMX y los guarda en el servidor.
    report_type='all' genera todos los informes que falten (sin year/month)."""
    if (err := _check_write_enabled()):
        return err
    if report_type == "all":
        return await _get("/api/genreports/all")
    if year is None:
        year = _today().year
    if not (2000 <= year <= 2050):
        raise ValueError(f"year fuera de rango (2000..2050): {year}")
    if report_type == "year":
        return await _get(f"/api/genreports/noaayear?year={year}")
    elif report_type == "month":
        if month is None:
            month = _today().month
        if not (1 <= month <= 12):
            raise ValueError(f"month fuera de rango (1..12): {month}")
        return await _get(f"/api/genreports/noaamonth?year={year}&month={month}")
    else:
        raise ValueError("report_type debe ser 'year', 'month' o 'all'")


@write_tool
async def upload_reports(
    report_type: Literal["year", "month"] = "month",
    year: int | None = None,
    month: int | None = None,
) -> str:
    """Sube informes NOAA anuales o mensuales mediante el método de subida/FTP configurado en CumulusMX."""
    if (err := _check_write_enabled()):
        return err
    if year is None:
        year = _today().year
    if not (2000 <= year <= 2050):
        raise ValueError(f"year fuera de rango (2000..2050): {year}")
    if report_type == "year":
        return await _get(f"/api/uploadreport/noaayear?year={year}")
    elif report_type == "month":
        if month is None:
            month = _today().month
        if not (1 <= month <= 12):
            raise ValueError(f"month fuera de rango (1..12): {month}")
        return await _get(f"/api/uploadreport/noaamonth?year={year}&month={month}")
    else:
        raise ValueError("report_type debe ser 'year' o 'month'")


# ---------------------------------------------------------------- diagnóstico ----

@mcp.tool(annotations=READ_ONLY)
async def get_error_log() -> str:
    """Obtiene el registro de errores y avisos del sistema en CumulusMX."""
    return await _get("/api/data/errorlog.json")


# ---------------------------------------------------------------- edición ----

@write_tool
async def edit_current_conditions(text: str) -> str:
    """Actualiza el texto descriptivo de las condiciones meteorológicas actuales de la estación."""
    if (err := _check_write_enabled()):
        return err
    return await _post(
        "/api/edit/currcond",
        content=text.encode("utf-8"),
        headers={"Content-Type": "text/plain; charset=utf-8"},
    )


@write_tool
async def edit_rain_today(raintoday: float, midnightrain: float | None = None) -> str:
    """Modifica los valores acumulados de lluvia de hoy y lluvia desde medianoche en la estación."""
    if (err := _check_write_enabled()):
        return err
    if midnightrain is None:
        try:
            curr = await _json("/api/edit/raintodayeditdata.json")
            midnightrain = float(curr.get("midnightrain", raintoday))
        except Exception:
            midnightrain = raintoday
    data = {
        "raintoday": f"{float(raintoday):.3f}",
        "midnightrain": f"{float(midnightrain):.3f}",
    }
    return await _post("/api/edit/raintoday", data=data)


@write_tool
async def edit_diary(
    date: str,
    entry: str = "",
    snow24h: float | None = None,
    snow_depth: float | None = None,
    thunder: bool = False,
    hail: bool = False,
    fog: bool = False,
    gales: bool = False,
    time_str: str = "00:00:00",
) -> str:
    """Crea o actualiza una entrada en el diario meteorológico (diary) de CumulusMX."""
    if (err := _check_write_enabled()):
        return err
    d = _check_date(date, "date")
    payload = {
        "Date": d.strftime("%Y-%m-%d"),
        "Time": time_str,
        "Entry": entry,
        "Snow24h": snow24h,
        "SnowDepth": snow_depth,
        "Thunder": thunder,
        "Hail": hail,
        "Fog": fog,
        "Gales": gales,
    }
    return await _post("/api/edit/diarydata", content=json.dumps(payload), headers={"Content-Type": "application/json"})


@write_tool
async def delete_diary(date: str) -> str:
    """Elimina la entrada del diario meteorológico para una fecha específica (yyyy-mm-dd)."""
    if (err := _check_write_enabled()):
        return err
    d = _check_date(date, "date")
    payload = {"Date": d.strftime("%Y-%m-%d")}
    return await _post("/api/edit/diarydelete", content=json.dumps(payload), headers={"Content-Type": "application/json"})


# ---------------------------------------------------------------- configuración ----

@mcp.tool(annotations=READ_ONLY)
async def get_station_settings() -> str:
    """Obtiene la configuración actual de la estación meteorológica en CumulusMX."""
    return await _get("/api/settings/stationdata.json")


@write_tool
async def update_station_settings(settings: dict[str, Any] | str) -> str:
    """Actualiza la configuración de la estación meteorológica en CumulusMX."""
    if (err := _check_write_enabled()):
        return err
    new_data = _normalize_settings_payload(settings)
    try:
        current_data = await _json("/api/settings/stationdata.json")
        if isinstance(current_data, dict):
            merged = _deep_merge(dict(current_data), new_data)
        else:
            merged = new_data
    except Exception:
        merged = new_data
    json_str = json.dumps(merged)
    body = "json=" + urllib.parse.quote(json_str)
    return await _post(
        "/api/setsettings/updatestationconfig.json",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


@mcp.tool(annotations=READ_ONLY)
async def get_calibration_settings() -> str:
    """Obtiene los factores de calibración y offsets actuales de los sensores en CumulusMX."""
    return await _get("/api/settings/calibrationdata.json")


@write_tool
async def update_calibration_settings(settings: dict[str, Any] | str) -> str:
    """Actualiza los factores y offsets de calibración de los sensores en CumulusMX."""
    if (err := _check_write_enabled()):
        return err
    new_data = _normalize_settings_payload(settings)
    try:
        current_data = await _json("/api/settings/calibrationdata.json")
        if isinstance(current_data, dict):
            merged = _deep_merge(dict(current_data), new_data)
        else:
            merged = new_data
    except Exception:
        merged = new_data
    json_str = json.dumps(merged)
    body = "json=" + urllib.parse.quote(json_str)
    return await _post(
        "/api/setsettings/updatecalibrationconfig.json",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


@mcp.tool(annotations=READ_ONLY)
async def get_alarm_settings() -> str:
    """Obtiene la configuración actual de alarmas y umbrales en CumulusMX."""
    res = await _get("/api/settings/alarms.json")
    if "ERROR HTTP 404" in res:
        return await _get("/api/settings/alarmdata.json")
    return res


@write_tool
async def update_alarm_settings(settings: dict[str, Any] | str) -> str:
    """Actualiza la configuración de alarmas y umbrales en CumulusMX."""
    if (err := _check_write_enabled()):
        return err
    new_data = _normalize_settings_payload(settings)
    try:
        current_data = await _json("/api/settings/alarms.json")
        if isinstance(current_data, dict):
            if "data" in new_data or "units" in new_data or "email" in new_data:
                merged = _deep_merge(dict(current_data), new_data)
            else:
                current_copy = dict(current_data)
                current_data_section = current_copy.get("data", {})
                if isinstance(current_data_section, dict):
                    current_copy["data"] = _deep_merge(dict(current_data_section), new_data)
                else:
                    current_copy["data"] = new_data
                merged = current_copy
        else:
            merged = new_data if "data" in new_data else {"data": new_data}
    except Exception:
        merged = new_data if "data" in new_data else {"data": new_data}

    return await _post(
        "/api/setsettings/updatealarmconfig.json",
        content=urllib.parse.quote(json.dumps(merged)),  # CumulusMX aplica UrlDecode al cuerpo
        headers={"Content-Type": "application/json"},
    )


@mcp.tool(annotations=READ_ONLY)
async def get_noaa_settings() -> str:
    """Obtiene la configuración de generación de informes NOAA en CumulusMX."""
    return await _get("/api/settings/noaadata.json")


@write_tool
async def test_email(
    from_email: str,
    dest_email: str,
    use_html: bool = False,
    use_bcc: bool = False,
) -> str:
    """Envía un correo de prueba desde CumulusMX para verificar la configuración SMTP de alarmas."""
    if (err := _check_write_enabled()):
        return err
    payload = json.dumps({
        "fromEmail": from_email,
        "destEmail": dest_email,
        "useHtml": use_html,
        "useBcc": use_bcc,
    })
    return await _post("/api/setsettings/testemail.json", content=payload, headers={"Content-Type": "application/json"})


# ---------------------------------------------------------------- cobertura completa de la API de CumulusMX ----
# Nombres, rutas y formatos de cuerpo verificados en el código fuente de CumulusMX 5.2.0 (Api.cs, DataEditor.cs,
# Settings/*.cs). Las herramientas de escritura se publican solo con ENABLE_WRITE_TOOLS (por defecto true).

FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}
JSON_HEADERS = {"Content-Type": "application/json"}

# sección -> (GET /api/settings/<get>, POST /api/setsettings/<post>, estilo de cuerpo)
#  "form": cuerpo `json=<JSON codificado>` (el servidor salta los 5 primeros caracteres)
#  "raw":  JSON codificado sin prefijo (solo alarmas)
SETTINGS_SECTIONS: dict[str, tuple[str, str, str]] = {
    "program": ("programdata.json", "updateprogramconfig.json", "form"),
    "station": ("stationdata.json", "updatestationconfig.json", "form"),
    "internet": ("internetdata.json", "updateinternetconfig.json", "form"),
    "thirdparty": ("thirdpartydata.json", "updatethirdpartyconfig.json", "form"),
    "extrasensors": ("extrasensordata.json", "updateextrasensorconfig.json", "form"),
    "extrawebfiles": ("extrawebfiles.json", "updateextrawebfiles.json", "form"),
    "calibration": ("calibrationdata.json", "updatecalibrationconfig.json", "form"),
    "language": ("langdata.json", "updatelanguage.json", "form"),
    "noaa": ("noaadata.json", "updatenoaaconfig.json", "form"),
    "mysql": ("mysqldata.json", "updatemysqlconfig.json", "form"),
    "alarms": ("alarms.json", "updatealarmconfig.json", "raw"),
    "useralarms": ("useralarms.json", "updateuseralarms.json", "form"),
    "wizard": ("wizard.json", "wizard.json", "form"),
    "customlogs_interval": ("customlogsintvl.json", "updatecustomlogsintvl.json", "form"),
    "customlogs_daily": ("customlogsdaily.json", "updatecustomlogsdaily.json", "form"),
    "display": ("displayoptions.json", "updatedisplay.json", "form"),
    "httpfiles": ("httpfiles.json", "updatehttpfiles.json", "form"),
    "mqtt": ("mqttdata.json", "updatemqttconfig.json", "form"),
}
SettingsSection = Literal[
    "program", "station", "internet", "thirdparty", "extrasensors", "extrawebfiles", "calibration", "language",
    "noaa", "mysql", "alarms", "useralarms", "wizard", "customlogs_interval", "customlogs_daily", "display",
    "httpfiles", "mqtt",
]

# CUMULUS_MASK_SECRETS=true oculta contraseñas/claves en las lecturas de ajustes (por defecto se devuelven tal cual)
CUMULUS_MASK_SECRETS = os.environ.get("CUMULUS_MASK_SECRETS", "false").strip().lower() in ("1", "true", "yes", "on")
SECRET_KEY_RE = re.compile(r"(pass(word)?|pwd|secret|token|api[-_]?key|psk)$", re.IGNORECASE)


def _mask_secrets_in(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: ("***" if isinstance(v, str) and v and SECRET_KEY_RE.search(str(k)) else _mask_secrets_in(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask_secrets_in(v) for v in obj]
    return obj


def _maybe_mask(text: str) -> str:
    if not CUMULUS_MASK_SECRETS:
        return text
    try:
        return json.dumps(_mask_secrets_in(json.loads(text)), ensure_ascii=False)
    except ValueError:
        return text


def _diff_paths(old: Any, new: Any, prefix: str = "") -> dict[str, dict]:
    """Campos que cambian entre dos estructuras JSON: {ruta: {antes, despues}} (secretos siempre enmascarados)."""
    out: dict[str, dict] = {}
    if isinstance(old, dict) and isinstance(new, dict):
        for k in sorted(set(old) | set(new), key=str):
            path = f"{prefix}.{k}" if prefix else str(k)
            if k not in old or k not in new:
                out[path] = {"antes": old.get(k), "despues": new.get(k)}
            else:
                out.update(_diff_paths(old[k], new[k], path))
    elif old != new:
        out[prefix] = {"antes": old, "despues": new}
    if prefix == "":
        return _mask_secrets_in_diff(out)
    return out


def _mask_secrets_in_diff(diff: dict[str, dict]) -> dict[str, dict]:
    masked = {}
    for path, change in diff.items():
        if SECRET_KEY_RE.search(path.rsplit(".", 1)[-1]):
            change = {k: ("***" if isinstance(v, str) and v else v) for k, v in change.items()}
        masked[path] = change
    return masked


def _encode_settings_body(style: str, payload: dict) -> str:
    encoded = urllib.parse.quote(json.dumps(payload, ensure_ascii=False))
    return "json=" + encoded if style == "form" else encoded


async def _settings_update(section: str, settings: dict[str, Any] | str, dry_run: bool = False,
                           replace: bool = False) -> str:
    """Lee la sección actual, fusiona los campos indicados (o la sustituye con replace=True) y la envía con el formato
    de cuerpo que espera CumulusMX. Si no puede leer la configuración actual NO escribe a ciegas."""
    if (err := _check_write_enabled()):
        return err
    if section not in SETTINGS_SECTIONS:
        raise ValueError(f"section debe ser una de {sorted(SETTINGS_SECTIONS)}")
    partial = _normalize_settings_payload(settings)
    get_path, post_path, style = SETTINGS_SECTIONS[section]
    current = await _json(f"/api/settings/{get_path}")
    if not isinstance(current, dict):
        raise ValueError(f"CumulusMX devolvió una configuración inesperada para '{section}'; no se modifica nada")
    if replace:
        merged = partial
    elif section == "alarms" and not any(k in partial for k in ("data", "units", "email")):
        section_data = current.get("data") if isinstance(current.get("data"), dict) else {}
        merged = {**current, "data": _deep_merge(section_data, partial)}
    else:
        merged = _deep_merge(current, partial)
    changes = _diff_paths(current, merged)
    result: dict[str, Any] = {"seccion": section, "campos_modificados": changes, "modo": "sustitucion" if replace else "fusion"}
    if dry_run:
        result["aplicado"] = False
        result["nota"] = "Vista previa: no se ha enviado nada a CumulusMX."
        return json.dumps(result, ensure_ascii=False)
    if not changes:
        result["aplicado"] = False
        result["nota"] = "Sin cambios: la configuración ya tiene esos valores."
        return json.dumps(result, ensure_ascii=False)
    headers = JSON_HEADERS if style == "raw" else FORM_HEADERS
    response = await _post(f"/api/setsettings/{post_path}", content=_encode_settings_body(style, merged), headers=headers)
    result["aplicado"] = True
    result["respuesta"] = response
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def get_settings(section: SettingsSection) -> str:
    """Lee cualquier sección de configuración de CumulusMX (misma estructura que acepta update_settings):
    program, station, internet (FTP/web), thirdparty (Wunderground, etc.), extrasensors, extrawebfiles, calibration,
    language, noaa, mysql, alarms, useralarms, wizard, customlogs_interval, customlogs_daily, display, httpfiles, mqtt.
    ATENCIÓN: puede incluir contraseñas y claves (FTP, MySQL, MQTT, SMTP...). Con CUMULUS_MASK_SECRETS=true se enmascaran."""
    if section not in SETTINGS_SECTIONS:
        raise ValueError(f"section debe ser una de {sorted(SETTINGS_SECTIONS)}")
    return _maybe_mask(await _get(f"/api/settings/{SETTINGS_SECTIONS[section][0]}"))


@mcp.tool(annotations=READ_ONLY)
async def get_mysql_settings() -> str:
    """Configuración MySQL de CumulusMX: server (host, port, user, pass, database, advanced.sslMode/tlsVers), options
    (updateonedit, bufferonerror), monthly / realtime / dayfile (enabled, table, retención...) y los comandos
    personalizados (customseconds, customminutes, customrollover, customtimed, customstart). Incluye la contraseña salvo
    que CUMULUS_MASK_SECRETS=true."""
    return _maybe_mask(await _get("/api/settings/mysqldata.json"))


@mcp.tool(annotations=READ_ONLY)
async def get_mqtt_settings() -> str:
    """Configuración MQTT de CumulusMX: servidor, puerto, credenciales, TLS, publicación de datos en tiempo real y a
    intervalos (plantillas y topics). Incluye la contraseña salvo que CUMULUS_MASK_SECRETS=true."""
    return _maybe_mask(await _get("/api/settings/mqttdata.json"))


@write_tool
async def update_settings(section: SettingsSection, settings: dict[str, Any] | str, dry_run: bool = False,
                          replace: bool = False) -> str:
    """Modifica CUALQUIER sección de configuración de CumulusMX. Lee la configuración actual, fusiona en profundidad
    solo los campos que indicas (replace=True envía `settings` tal cual, sustituyendo la sección) y la envía con el formato
    que exige cada endpoint. Devuelve los campos modificados (antes/después; secretos enmascarados).
    dry_run=True solo muestra los cambios sin aplicarlos: úsalo para confirmar con el usuario antes de escribir.
    Obtén la estructura con get_settings(section). Si no se puede leer la configuración actual no se escribe nada.
    Algunos cambios requieren reiniciar CumulusMX para tener efecto."""
    return await _settings_update(section, settings, dry_run, replace)


@write_tool
async def update_mysql_settings(settings: dict[str, Any] | str, dry_run: bool = False) -> str:
    """Modifica la configuración MySQL (fusión parcial). Ejemplo: {"server": {"host": "db", "port": 3306, "user": "u",
    "pass": "p", "database": "cumulus"}, "monthly": {"enabled": true, "table": "Monthly"}, "dayfile": {"enabled": true},
    "realtime": {"enabled": false}}. Tras activar tablas usa mysql_table(action='create', ...). dry_run=True = vista previa."""
    return await _settings_update("mysql", settings, dry_run)


@write_tool
async def update_mqtt_settings(settings: dict[str, Any] | str, dry_run: bool = False) -> str:
    """Modifica la configuración MQTT (fusión parcial). Parte de get_mqtt_settings para ver la estructura exacta
    (server, port, username, password, TLS, topics y plantillas). dry_run=True = vista previa."""
    return await _settings_update("mqtt", settings, dry_run)


@write_tool
async def update_noaa_settings(settings: dict[str, Any] | str, dry_run: bool = False) -> str:
    """Modifica la configuración de informes NOAA (nombre del sitio, ciudad, formatos de fichero, umbrales de calor/frío,
    subida...). Fusión parcial a partir de get_noaa_settings. dry_run=True = vista previa."""
    return await _settings_update("noaa", settings, dry_run)


@write_tool
async def mysql_table(action: Literal["create", "update"], table: Literal["monthly", "dayfile", "realtime"]) -> str:
    """Crea (create) o actualiza la estructura (update) de las tablas MySQL de CumulusMX: monthly, dayfile o realtime.
    Úsalo después de configurar la conexión con update_mysql_settings."""
    if action not in ("create", "update") or table not in ("monthly", "dayfile", "realtime"):
        raise ValueError("action: create|update; table: monthly|dayfile|realtime")
    if (err := _check_write_enabled()):
        return err
    return await _post(f"/api/setsettings/{action}{table}sql.json")


@mcp.tool(annotations=READ_ONLY)
async def mysql_cache(start: int = 0, length: int = 100, search: str = "") -> str:
    """Comandos SQL pendientes en la caché de reintentos de MySQL (fallos de conexión). Paginado (length máx 500).
    Requiere las credenciales de CumulusMX si la API está protegida (CUMULUS_USER / CUMULUS_PASSWORD)."""
    return await _get("/api/data/mysqlcache.json", {"draw": 1, "start": max(0, start),
                                                    "length": max(1, min(length, 500)), "search[value]": search[:100]})


@mcp.tool(annotations=READ_ONLY)
async def diary_export() -> str:
    """Exporta todo el diario meteorológico en CSV (texto)."""
    return await _get("/api/data/diaryexport.txt")


@mcp.tool(annotations=READ_ONLY)
async def get_current_conditions_text() -> str:
    """Texto de 'condiciones actuales' que CumulusMX publica (el que cambia edit_current_conditions)."""
    return await _get("/api/edit/currentcond.json")


@mcp.tool(annotations=READ_ONLY)
async def records_edit_data(scope: Literal["alltime", "monthly", "thismonth", "thisyear"],
                            source: Literal["records", "dayfile", "logfile"] = "records") -> str:
    """Datos de la pantalla de edición de récords de CumulusMX. source='records': valores de récord actuales (editables con
    edit_records); 'dayfile': candidatos calculados desde el dayfile; 'logfile': desde los logs de intervalo (más lento)."""
    suffix = {"records": "", "dayfile": "dayfile", "logfile": "logfile"}[source]
    return await _get(f"/api/edit/{scope}records{suffix}.json")


# ---- datos por intervalo / diarios con selección de campos (intervaldata / dailydata)

LOG_FILE_FIELDS = [
    "DateTime", "TS", "Temperature", "Humidity", "DewPoint", "WindSpeed", "RecentHighGust", "AverageWindBearing",
    "RainfallRate", "RainfallSoFar", "SeaLevelPressure", "RainfallCounter", "InsideTemperature", "InsideHumidity",
    "CurrentGust", "WindChill", "HeatIndex", "UvIndex", "SolarRadiation", "Evapotranspiration",
    "AnnualEvapotranspiration", "ApparentTemperature", "MaxSolarRadiation", "HoursOfSunshine", "WindBearing",
    "Rg-11Rain", "RainSinceMidnight", "FeelsLike", "Humidex", "BGT", "WBGT",
]
EXTRA_LOG_FIELDS = [
    "DateTime", "TS",
    *[f"Temp{i}" for i in range(1, 11)], *[f"Hum{i}" for i in range(1, 11)], *[f"Dewpoint{i}" for i in range(1, 11)],
    "SoilTemp1", "SoilTemp2", "SoilTemp3", "SoilTemp4", "SoilMoist1", "SoilMoist2", "SoilMoist3", "SoilMoist4",
    "na1", "na2", "LeafWet1", "LeafWet2",
    *[f"SoilTemp{i}" for i in range(5, 17)], *[f"SoilMoist{i}" for i in range(5, 17)],
    "AQ1Pm", "AQ2Pm", "AQ3Pm", "AQ4Pm", "AQ1PmAvg", "AQ2PmAvg", "AQ3PmAvg", "AQ4PmAvg",
    *[f"UserTemp{i}" for i in range(1, 9)],
    "CO2", "CO2Avg", "CO2Pm25", "CO2Pm25Avg", "CO2Pm10", "CO2Pm10Avg", "CO2Temp", "CO2Hum",
    "LaserDist1", "LaserDist2", "LaserDist3", "LaserDist4", "Snow24h",
    *[f"Temp{i}" for i in range(11, 17)], *[f"Hum{i}" for i in range(11, 17)], *[f"Dewpoint{i}" for i in range(11, 17)],
    "AQ1Pm10", "AQ2Pm10", "AQ3Pm10", "AQ4Pm10", *[f"SoilEC{i}" for i in range(1, 17)],
]
DAYFILE_FIELD_NAMES = [
    "Date", "HighWindGust", "HighGustBearing", "HighGustTime", "MinTemperature", "MinTempTime", "MaxTemperature",
    "MaxTempTime", "MinPressure", "MinPressureTime", "MaxPressure", "MaxPressureTime", "MaxRainfallRate",
    "MaxRainRateTime", "TotalRainfallToday", "AvgTemperatureToday", "TotalWindRun", "HighAverageWindSpeed",
    "HighAvgWindSpeedTime", "LowHumidity", "LowHumidityTime", "HighHumidity", "HighHumidityTime",
    "TotalEvapotranspiration", "TotalHoursOfSunshine", "HighHeatIndex", "HighHeatIndexTime", "HighApparentTemperature",
    "HighAppTempTime", "LowApparentTemperature", "LowAppTempTime", "High1hRain", "High1hRainTime", "LowWindChill",
    "LowWindChillTime", "HighDewPoint", "HighDewPointTime", "LowDewPoint", "LowDewPointTime", "DominantWindBearing",
    "HeatingDegreeDays", "CoolingDegreeDays", "HighSolarRad", "HighSolarRadTime", "HighUv-I", "HighUv-ITime",
    "HighFeelsLike", "HighFeelsLikeTime", "LowFeelsLike", "LowFeelsLikeTime", "HighHumidex", "HighHumidexTime",
    "ChillHours", "High24hRain", "High24hRainTime", "HighBgt", "HighBgtTime", "HighWbgt", "HighWbgtTime",
]
CUMULUS_TIMEZONE = os.environ.get("CUMULUS_TIMEZONE", "").strip()


def _field_indices(fields: list[str], kind: str) -> list[int]:
    """Nombres (o índices numéricos) -> índices de campo de CumulusMX. En 'interval' los campos del log de sensores
    extra suman 1000, como espera la API."""
    if not fields:
        raise ValueError("fields no puede estar vacío")
    lookup: dict[str, int] = {}
    if kind == "interval":
        for i, n in enumerate(EXTRA_LOG_FIELDS):
            lookup.setdefault(n.lower(), 1000 + i)
        for i, n in enumerate(LOG_FILE_FIELDS):
            lookup[n.lower()] = i  # el log principal prevalece si un nombre existe en ambos
    else:
        for i, n in enumerate(DAYFILE_FIELD_NAMES):
            lookup[n.lower()] = i
    out: list[int] = []
    for f in fields:
        key = str(f).strip().lower()
        if key.isdigit():
            out.append(int(key))
        elif key in lookup:
            out.append(lookup[key])
        else:
            raise ValueError(f"campo desconocido '{f}'. Válidos: {sorted({n for n in lookup})[:60]}...")
    return out


def _to_epoch(value: str, end_of_day: bool = False) -> int:
    """Segundos Unix tal cual, o una fecha/hora de la estación ('yyyy-mm-dd' / 'yyyy-mm-dd HH:MM') convertida con
    CUMULUS_TIMEZONE (zona IANA de la estación, p. ej. Europe/Madrid)."""
    v = str(value).strip()
    if re.fullmatch(r"\d{9,11}", v):
        return int(v)
    if not CUMULUS_TIMEZONE:
        raise ValueError("Para usar fechas legibles define CUMULUS_TIMEZONE (zona IANA de la estación, p. ej. "
                         "Europe/Madrid) o pasa segundos Unix.")
    import zoneinfo
    if DATE_RE.match(v):
        fmt = "%Y-%m-%d %H:%M" if " " in v else "%Y-%m-%d"
    else:
        raise ValueError(f"fecha inválida '{value}': usa yyyy-mm-dd, 'yyyy-mm-dd HH:MM' o segundos Unix")
    dt = datetime.datetime.strptime(v, fmt)
    if end_of_day and fmt == "%Y-%m-%d":
        dt = dt.replace(hour=23, minute=59, second=59)
    return int(dt.replace(tzinfo=zoneinfo.ZoneInfo(CUMULUS_TIMEZONE)).timestamp())


_NUM_CELL_RE = re.compile(r"^-?\d+([.,]\d+)?$")


def _parse_table(raw: str, max_rows: int) -> dict[str, Any] | str:
    """Respuesta de intervaldata/dailydata ([["Date/Time", nombres...], [fecha, valores...], ...]) -> campos + filas con
    números reales (acepta coma decimal regional). Si no tiene el formato esperado devuelve el texto original."""
    try:
        rows = json.loads(raw)
    except ValueError:
        return raw
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], list):
        return raw

    def cell(c: Any) -> Any:
        if isinstance(c, str) and _NUM_CELL_RE.match(c):
            f = float(c.replace(",", "."))
            return int(f) if re.fullmatch(r"-?\d+", c) else f
        return c

    body = [[r[0], *[cell(c) for c in r[1:]]] for r in rows[1:] if isinstance(r, list) and r]
    return {"campos": rows[0][1:], "n_filas": len(body), "filas": body[:max_rows], "truncado": len(body) > max_rows}


@mcp.tool(annotations=READ_ONLY)
async def interval_data(start: str, end: str, fields: list[str], max_rows: int = 500) -> str:
    """Datos de los logs de intervalo (cada pocos minutos) de un rango, con SOLO los campos que pidas (sin paginar).
    fields: nombres del log principal (Temperature, Humidity, DewPoint, WindSpeed, RecentHighGust, RainfallRate,
    SeaLevelPressure, UvIndex, SolarRadiation, FeelsLike, Humidex, BGT...) o del log de sensores extra (Temp1, SoilTemp1,
    SoilMoist1, LeafWet1, CO2, ...), o índices numéricos. start/end: segundos Unix, o 'yyyy-mm-dd' / 'yyyy-mm-dd HH:MM' si
    el servidor tiene CUMULUS_TIMEZONE (zona horaria de la estación). Devuelve campos y filas con números reales."""
    s, e = _to_epoch(start), _to_epoch(end, end_of_day=True)
    if e < s:
        raise ValueError("end anterior a start")
    idx = ",".join(str(i) for i in _field_indices(fields, "interval"))
    raw = await _get("/api/data/intervaldata.json", {"from": s, "to": e, "data": idx})
    out = _parse_table(raw, max(1, min(max_rows, 2000)))
    return raw if isinstance(out, str) else json.dumps(out, ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY)
async def daily_data(start: str, end: str, fields: list[str], max_rows: int = 500) -> str:
    """Datos diarios (dayfile) de un rango con SOLO los campos oficiales de CumulusMX que pidas (nombres como
    MaxTemperature, MinTemperature, TotalRainfallToday, HighWindGust, TotalHoursOfSunshine, HighBgt... o índices).
    Alternativa a dayfile_range con los nombres originales; mismas reglas de fecha que interval_data."""
    s, e = _to_epoch(start), _to_epoch(end, end_of_day=True)
    if e < s:
        raise ValueError("end anterior a start")
    idx = ",".join(str(i) for i in _field_indices(fields, "daily"))
    raw = await _get("/api/data/dailydata.json", {"from": s, "to": e, "data": idx})
    out = _parse_table(raw, max(1, min(max_rows, 2000)))
    return raw if isinstance(out, str) else json.dumps(out, ensure_ascii=False)


# ---- consultas y gráficas personalizadas

QUERY_PROPERTIES = (
    "HighGust", "WindRun", "HighAvgWind", "LowTemp", "HighTemp", "AvgTemp", "HighHeatIndex", "HighAppTemp",
    "LowAppTemp", "LowWindChill", "HighDewPoint", "LowDewPoint", "HighFeelsLike", "LowFeelsLike", "HighHumidex",
    "LowPress", "HighPress", "HighRainRate", "TotalRain", "HighHourlyRain", "HighRain24h", "LowHumidity",
    "HighHumidity", "SunShineHours", "HighSolar", "HighUv", "ET", "HeatingDegreeDays", "CoolingDegreeDays",
    "ChillHours", "HighBgt", "HighWbgt",
)
QUERY_FUNCTIONS = ("min", "max", "sum", "avg", "count")
QUERY_OPERATORS = (">", "<", ">=", "<=", "=", "<>")


@mcp.tool(annotations=READ_ONLY)
async def records_query(dataname: str, function: Literal["min", "max", "sum", "avg", "count"], period: str = "ThisYear",
                        start: str = "", end: str = "", where: Literal[">", "<", ">=", "<=", "=", "<>"] | None = None,
                        value: float = 0.0, countfunction: Literal["min", "max", "sum", "avg", "count"] | None = None) -> str:
    """Consulta del dayfile ejecutada por el propio CumulusMX (misma función que 'Consultas' de su web). dataname:
    HighTemp, LowTemp, AvgTemp, TotalRain, HighGust, WindRun, HighAvgWind, HighHeatIndex, HighAppTemp, LowAppTemp,
    LowWindChill, HighDewPoint, LowDewPoint, HighFeelsLike, LowFeelsLike, HighHumidex, LowPress, HighPress, HighRainRate,
    HighHourlyRain, HighRain24h, LowHumidity, HighHumidity, SunShineHours, HighSolar, HighUv, ET, HeatingDegreeDays,
    CoolingDegreeDays, ChillHours, HighBgt, HighWbgt. function: min|max|sum|avg|count. period: 'ThisYear', 'ThisMonth',
    'ThisDay', 'Year-N', 'Month-N', 'Day-N' (N periodos atrás) o 'User' con start/end (formato de CumulusMX, p. ej.
    dd/MM/yyyy). where+value filtran días (p. ej. where='>=' value=30 con function='count'). Devuelve {value, time}."""
    if dataname not in QUERY_PROPERTIES:
        raise ValueError(f"dataname debe ser uno de {list(QUERY_PROPERTIES)}")
    if function not in QUERY_FUNCTIONS or (countfunction is not None and countfunction not in QUERY_FUNCTIONS):
        raise ValueError(f"function/countfunction deben ser uno de {list(QUERY_FUNCTIONS)}")
    if where is not None and where not in QUERY_OPERATORS:
        raise ValueError(f"where debe ser uno de {list(QUERY_OPERATORS)}")
    if not re.fullmatch(r"[A-Za-z]{3,10}(-?\d{1,4})?", period):
        raise ValueError("period inválido (ThisYear, ThisMonth, ThisDay, Year-N, Month-N, Day-N o User)")
    if period == "User" and not (start and end):
        raise ValueError("period='User' requiere start y end")
    body = {"dataname": dataname, "function": function, "where": where, "value": value, "startsel": period,
            "start": start or None, "end": end or None, "countfunction": countfunction}
    return await _post("/api/records/query/dayfile.json", content="json=" + urllib.parse.quote(json.dumps(body)),
                       headers=FORM_HEADERS)


@mcp.tool(annotations=READ_ONLY)
async def graph_select(kind: Literal["chart", "period"], series: list[str], from_date: str, to_date: str,
                       colours: list[str] | None = None) -> str:
    """Gráfica personalizada de CumulusMX ('Select-a-Chart' y 'Select-a-Period'): devuelve las series pedidas entre dos
    fechas. series: nombres de series disponibles (consúltalos con graph_data('availabledata')). from_date/to_date:
    yyyy-mm-dd. colours: opcional (colores hexadecimales, uno por serie)."""
    if not series:
        raise ValueError("series no puede estar vacío")
    for d, n in ((from_date, "from_date"), (to_date, "to_date")):
        _check_date(d, n)
    payload = {"series": series, "colours": colours or ["#000000"] * len(series), "fromDate": from_date, "toDate": to_date}
    path = "selectachart" if kind == "chart" else "selectaperiod"
    return await _post(f"/api/graphdata/{path}.json", content=urllib.parse.quote(json.dumps(payload)), headers=FORM_HEADERS)


# ---- tiempo real (WebSocket)

@mcp.tool(annotations=READ_ONLY)
async def live_sample(seconds: float = 10.0, max_messages: int = 3) -> str:
    """Escucha el WebSocket de tiempo real de CumulusMX (/ws/) y devuelve los primeros mensajes (el primero llega al
    conectar; los siguientes en cada actualización de la estación, normalmente cada pocos segundos). Cada mensaje es el
    mismo JSON que current_data. seconds máx 60, max_messages máx 20."""
    seconds = max(1.0, min(float(seconds), 60.0))
    max_messages = max(1, min(int(max_messages), 20))
    try:
        import websockets
    except ImportError:  # pragma: no cover
        raise ValueError("Falta la librería 'websockets' (pip install websockets)")
    url = BASE.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/ws/"
    headers = {}
    if (auth := _get_auth()):
        headers["Authorization"] = "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
    messages: list[Any] = []

    async def collect() -> None:
        async with websockets.connect(url, additional_headers=headers or None, open_timeout=10) as ws:
            while len(messages) < max_messages:
                raw = await ws.recv()
                try:
                    messages.append(json.loads(raw))
                except (TypeError, ValueError):
                    messages.append(raw)

    try:
        await asyncio.wait_for(collect(), timeout=seconds)
        timed_out = False
    except asyncio.TimeoutError:
        timed_out = True
    except Exception as e:  # conexión rechazada, 403, etc.
        if not messages:
            return json.dumps({"error": f"No se pudo leer el WebSocket {url}: {type(e).__name__}: {e}"}, ensure_ascii=False)
        timed_out = False
    return json.dumps({"url": url, "n_mensajes": len(messages), "tiempo_agotado": timed_out, "mensajes": messages},
                      ensure_ascii=False)


# ---- edición de datos históricos (DataEditor) y diario

def _editor_payload(action: str, lines: list[int] | None = None, data: list[list[str]] | None = None, **extra: Any) -> str:
    if action not in ("Edit", "Delete"):
        raise ValueError("action debe ser 'Edit' o 'Delete'")
    body: dict[str, Any] = {"action": action}
    if lines is not None:
        body["lines"] = lines
    if data is not None:
        body["data"] = data
    body.update(extra)
    return json.dumps(body, ensure_ascii=False)


@write_tool
async def edit_dayfile(action: Literal["Edit", "Delete"], line: int, values: list[str]) -> str:
    """Edita o borra UNA línea del dayfile.txt (corrección de datos históricos). line: número de línea (1 = primera; es el
    primer valor que devuelve la tool dayfile). values: los campos de esa línea tal como aparecen en dayfile.txt (fecha
    dd/MM/yy primero, sin el número de línea). CumulusMX comprueba que la fecha coincide con la de la línea indicada.
    Actualiza también SQLite y, si está activado 'update on edit', MySQL. HAZ COPIA DE SEGURIDAD antes de editar."""
    if line < 1 or not values:
        raise ValueError("line debe ser >= 1 y values no puede estar vacío")
    if (err := _check_write_enabled()):
        return err
    _cached_dayfile_by_year.clear()
    return await _post("/api/edit/dayfile", content=_editor_payload(action, [line], [[str(v) for v in values]]),
                       headers=JSON_HEADERS)


@write_tool
async def edit_datalog(action: Literal["Edit", "Delete"], line: int, values: list[str], extra: bool = False) -> str:
    """Edita o borra una línea de un log mensual de intervalo (extra=True: log de sensores extra). El fichero se deduce del
    segundo campo (TS, segundos Unix) de values; line es la línea dentro de ese fichero (1 = primera). values: todos los
    campos de la línea del log. CumulusMX comprueba que el TS coincide con el de la línea. HAZ COPIA DE SEGURIDAD."""
    if line < 1 or not values:
        raise ValueError("line debe ser >= 1 y values no puede estar vacío")
    if (err := _check_write_enabled()):
        return err
    return await _post("/api/edit/datalogs", content=_editor_payload(action, [line], [[str(v) for v in values]], extra=extra),
                       headers=JSON_HEADERS)


@write_tool
async def edit_recent_data(action: Literal["Edit", "Delete"], values: list[str]) -> str:
    """Edita o borra un registro de la tabla de datos recientes (SQLite) de CumulusMX. values: los campos del registro en
    el orden CSV de la tabla (consúltalo con recent_data)."""
    if not values:
        raise ValueError("values no puede estar vacío")
    if (err := _check_write_enabled()):
        return err
    return await _post("/api/edit/recentdata", content=_editor_payload(action, data=[[str(v) for v in values]]),
                       headers=JSON_HEADERS)


@write_tool
async def edit_mysql_cache(action: Literal["Edit", "Delete"], keys: list[int], statements: list[str] | None = None) -> str:
    """Edita o borra comandos SQL pendientes en la caché de reintentos de MySQL (ver mysql_cache). keys: claves de los
    comandos; statements: nuevo SQL (solo para Edit, uno por clave)."""
    if not keys:
        raise ValueError("keys no puede estar vacío")
    if action == "Edit" and (not statements or len(statements) != len(keys)):
        raise ValueError("Edit requiere un statement por cada clave")
    if (err := _check_write_enabled()):
        return err
    extra: dict[str, Any] = {"keys": keys}
    if statements is not None:
        extra["statements"] = statements
    return await _post("/api/edit/mysqlcache", content=_editor_payload(action, **extra), headers=JSON_HEADERS)


RECORD_FIELDS_COMMON = (
    "highTemp", "lowTemp", "highDewPoint", "lowDewPoint", "highApparentTemp", "lowApparentTemp", "highFeelsLike",
    "lowFeelsLike", "highHumidex", "lowWindChill", "highHeatIndex", "highMinTemp", "lowMaxTemp", "highDailyTempRange",
    "lowDailyTempRange", "highHumidity", "lowHumidity", "highBarometer", "lowBarometer", "highGust", "highWind",
    "highWindRun", "highRainRate", "highHourlyRain", "highDailyRain", "highRain24h", "highMonthlyRain",
    "longestDryPeriod", "longestWetPeriod", "highBgt", "highWbgt",
)
RECORD_EDIT_FIELDS: dict[str, tuple[str, ...]] = {
    "alltime": RECORD_FIELDS_COMMON,
    "monthly": RECORD_FIELDS_COMMON,
    "thisyear": RECORD_FIELDS_COMMON,
    "thismonth": tuple(f for f in RECORD_FIELDS_COMMON if f != "highMonthlyRain"),
}


async def _station_decimal_separator() -> str:
    """Separador decimal que usa la estación (CumulusMX parsea los valores editados con su cultura regional)."""
    text = await _get("/api/tags/process.json?temp")
    m = re.search(r'"temp":"-?\d+([.,])\d', text)
    return m.group(1) if m else "."


@write_tool
async def edit_records(scope: Literal["alltime", "monthly", "thismonth", "thisyear"], field: str, value: float, time: str,
                       month: int | None = None) -> str:
    """Corrige un récord de la estación. scope: alltime | monthly (requiere month 1-12) | thismonth | thisyear. field:
    highTemp, lowTemp, highDewPoint, lowDewPoint, highApparentTemp, lowApparentTemp, highFeelsLike, lowFeelsLike,
    highHumidex, lowWindChill, highHeatIndex, highMinTemp, lowMaxTemp, highDailyTempRange, lowDailyTempRange,
    highHumidity, lowHumidity, highBarometer, lowBarometer, highGust, highWind, highWindRun, highRainRate,
    highHourlyRain, highDailyRain, highRain24h, highMonthlyRain (no en thismonth), longestDryPeriod, longestWetPeriod,
    highBgt, highWbgt. time: 'dd/MM/yyyy HH:mm' (récords con hora), 'dd/MM/yyyy' (récords diarios) o 'MMM yyyy' (lluvia
    mensual), en el formato regional del servidor. El valor se envía con el separador decimal de la estación."""
    if scope not in RECORD_EDIT_FIELDS:
        raise ValueError(f"scope debe ser uno de {sorted(RECORD_EDIT_FIELDS)}")
    if field not in RECORD_EDIT_FIELDS[scope]:
        raise ValueError(f"field '{field}' no válido para {scope}. Válidos: {list(RECORD_EDIT_FIELDS[scope])}")
    if scope == "monthly" and not (month and 1 <= month <= 12):
        raise ValueError("scope='monthly' requiere month entre 1 y 12")
    if (err := _check_write_enabled()):
        return err
    sep = await _station_decimal_separator()
    name = f"{month}-{field}" if scope == "monthly" else field
    body = f"name={name}&value={str(value).replace('.', sep)}&time={urllib.parse.quote_plus(time)}"
    return await _post(f"/api/edit/{scope}", content=body, headers=FORM_HEADERS)


@write_tool
async def diary_upload(csv_content: str) -> str:
    """Importa entradas al diario meteorológico desde texto CSV: la primera línea es la cabecera (se ignora) y cada línea
    siguiente un registro, con el mismo formato que devuelve diary_export."""
    if not csv_content or "\n" not in csv_content.strip():
        raise ValueError("csv_content debe incluir la cabecera y al menos un registro")
    if (err := _check_write_enabled()):
        return err
    return await _post("/api/edit/diaryupload", content=csv_content, headers={"Content-Type": "text/plain; charset=utf-8"})


@write_tool
async def diary_automate(sensor: int) -> str:
    """Configura qué sensor láser de nieve rellena automáticamente el diario (1-4). 0 desactiva la automatización."""
    if not 0 <= sensor <= 4:
        raise ValueError("sensor debe estar entre 0 y 4")
    if (err := _check_write_enabled()):
        return err
    return await _post("/api/edit/diaryautomate", content=str(sensor), headers={"Content-Type": "text/plain"})


# ---- entrada de datos de estaciones HTTP (Ecowitt, Wunderground, Ambient, JSON)

STATION_INPUT_PROTOCOLS: dict[str, tuple[str, ...]] = {
    "ecowitt": ("GET", "POST"), "ecowittextra": ("GET", "POST"), "json": ("POST",), "jsonextra": ("POST",),
    "wunderground": ("GET",), "ambient": ("GET",), "ambientextra": ("GET",),
}


@write_tool
async def station_input(protocol: Literal["ecowitt", "ecowittextra", "json", "jsonextra", "wunderground", "ambient",
                                          "ambientextra"],
                        method: Literal["GET", "POST"] = "POST", body: str = "", query: dict[str, str] | None = None,
                        content_type: str = "application/x-www-form-urlencoded") -> str:
    """Envía una lectura a la entrada HTTP de CumulusMX, como haría una estación o consola (/station/<protocolo>): útil para
    probar y depurar la configuración de una estación 'HTTP Ecowitt', 'HTTP Ambient' o 'JSON'. POST: ecowitt, ecowittextra,
    json, jsonextra (cuerpo en body). GET: ecowitt, ecowittextra, wunderground, ambient, ambientextra (parámetros en
    query). Falla con 503 si la estación configurada no es de ese tipo. CAUTELA: los datos enviados se registran como
    observaciones reales."""
    allowed = STATION_INPUT_PROTOCOLS.get(protocol)
    if allowed is None or method not in allowed:
        raise ValueError(f"{protocol} admite {STATION_INPUT_PROTOCOLS.get(protocol)}")
    if (err := _check_write_enabled()):
        return err
    path = f"/station/{protocol}"
    if method == "GET":
        return await _get(path, {str(k)[:60]: str(v)[:300] for k, v in (query or {}).items()} or None)
    return await _post(path, content=body, headers={"Content-Type": content_type})


# ---------------------------------------------------------------- resources ----

@mcp.resource("weather://current")
async def current_weather_resource() -> str:
    """Condiciones meteorológicas actuales en tiempo real."""
    return await _get("/api/data/currentdata")


@mcp.resource("weather://today")
async def today_weather_resource() -> str:
    """Resumen de datos y extremos de hoy y ayer."""
    return await today_all(strip_html=True)


@mcp.resource("weather://station")
async def station_info_resource() -> str:
    """Información general, versión y unidades de la estación meteorológica."""
    ver = await _get("/api/info/version.json")
    units = await _get("/api/info/units.json")
    return json.dumps({
        "version": json.loads(ver) if ver.startswith("{") else ver,
        "units": json.loads(units) if units.startswith("{") else units,
    }, ensure_ascii=False)


@mcp.resource("weather://records/alltime")
async def records_alltime_resource() -> str:
    """Récords meteorológicos históricos absolutos de la estación."""
    return await records_all(scope="alltime", strip_html=True)


# ---------------------------------------------------------------- prompts ----

@mcp.prompt("resumen_diario")
def resumen_diario_prompt(date: str = "") -> str:
    """Genera un análisis y resumen detallado del tiempo para una fecha dada (o de hoy)."""
    target = date if date else "el día de hoy"
    return (
        f"Por favor analiza los datos meteorológicos de la estación para {target}.\n"
        f"Consulta las condiciones actuales o el dayfile para esa fecha y resume:\n"
        f"- Temperaturas mínima, máxima y media\n"
        f"- Precipitación acumulada e intensidad\n"
        f"- Viento predominante y rachas máximas registradas\n"
        f"- Evolución de humedad y presión atmosférica\n"
        f"- Cualquier récord o valor anómalo destacable"
    )


@mcp.prompt("analisis_extremos")
def analisis_extremos_prompt(variable: str = "temp_max", top: int = 10) -> str:
    """Genera una comparativa histórica de eventos meteorológicos extremos."""
    return (
        f"Analiza los valores extremos históricos de la variable '{variable}' (top {top}).\n"
        f"Utiliza la herramienta 'rankings' y examina cuándo se produjeron estos extremos,\n"
        f"la distribución temporal de las efemérides y si se concentran en periodos u olas específicas."
    )


@mcp.prompt("comparativa_periodos")
def comparativa_periodos_prompt(start_a: str, end_a: str, start_b: str, end_b: str) -> str:
    """Compara climatológicamente dos periodos distintos."""
    return (
        f"Realiza una comparativa meteorológica detallada entre el periodo A ({start_a} a {end_a}) "
        f"y el periodo B ({start_b} a {end_b}).\n"
        f"Usa 'compare_periods' y resalta las diferencias en temperaturas medias y extremas, "
        f"precipitación total acumulada, días de lluvia y rachas de viento."
    )


@mcp.prompt("resumen_anual")
def resumen_anual_prompt(year: int) -> str:
    """Genera un informe climatológico anual completo de la estación."""
    return (
        f"Elabora un resumen climatológico detallado del año {year}.\n"
        f"Usa 'year_records' y 'monthly_summary' para revisar los hitos del año: "
        f"temperaturas extremas absolutas, días de helada, días cálidos (>=25°C y >=30°C), "
        f"lluvia acumulada y meses más lluviosos o secos."
    )


@mcp.prompt("resumen_mensual")
def resumen_mensual_prompt(year: int, month: int) -> str:
    """Informe de un mes concreto frente a la climatología propia de la estación."""
    return (
        f"Elabora el informe climático de {year}-{month:02d}.\n"
        f"1) Usa 'anomaly' (temp_media, temp_max, temp_min y lluvia_total) para comparar el mes con los mismos meses de otros años.\n"
        f"2) Usa 'climate_summary' para los extremos del mes y 'find_streaks' para la racha seca y la de calor más largas.\n"
        f"3) Usa 'count_days' para los días de lluvia (>= 0,2 mm) y de helada.\n"
        f"Concluye si fue un mes cálido/frío y seco/húmedo respecto a lo normal, con cifras y percentiles."
    )


@mcp.prompt("heladas_y_cultivo")
def heladas_y_cultivo_prompt(base_temp: float = 10.0) -> str:
    """Ayuda a planificar siembras: fechas de heladas y grados-día acumulados."""
    return (
        f"Ayúdame a planificar el huerto con los datos de mi estación.\n"
        f"Usa 'frost_dates' para decirme la fecha media de la última helada de primavera y de la primera de otoño, "
        f"y cuánto varían de un año a otro (sé prudente: recomienda una fecha segura, no la media).\n"
        f"Usa 'degree_days' con base {base_temp} desde el 1 de enero para ver cuántos grados-día llevamos este año "
        f"frente a años anteriores, y qué implica para el desarrollo de los cultivos."
    )


@mcp.prompt("auditoria_datos")
def auditoria_datos_prompt() -> str:
    """Revisa si el histórico de la estación es fiable antes de analizarlo."""
    return (
        "Audita la calidad de los datos de la estación.\n"
        "Usa 'data_quality' sobre todo el histórico y 'get_error_log' para ver errores recientes. "
        "Lista los días faltantes (agrupa los consecutivos en rangos), los valores incoherentes y la humedad 0 % "
        "(posible fallo del sensor), y dime qué conclusiones del histórico no deberían fiarse hasta corregirlo."
    )


def check_cumulus_connection() -> tuple[bool, str]:
    """Comprueba la conectividad con CumulusMX al arrancar e informa en el log."""
    auth = _get_auth()
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{BASE}/api/info/version.json", auth=auth)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    ver = data.get("Version", "desconocida")
                    bld = data.get("Build", "")
                    return True, f"CumulusMX v{ver} (Build {bld}) responde correctamente"
                except Exception:
                    return True, "CumulusMX responde correctamente (HTTP 200)"
            elif resp.status_code in (401, 403):
                return False, f"HTTP {resp.status_code}: Error de autenticacion (revisa CUMULUS_USER / CUMULUS_PASSWORD)"
            else:
                return False, f"HTTP {resp.status_code} al consultar version"
    except httpx.ConnectError:
        return False, f"No se pudo conectar a {BASE} (Connection refused o host no alcanzable)"
    except httpx.TimeoutException:
        return False, f"Tiempo de espera agotado al conectar a {BASE} (timeout > 5s)"
    except Exception as exc:
        return False, f"Error de conexion: {exc}"


if __name__ == "__main__":
    app = mcp.http_app(path="/mcp", middleware=[
        Middleware(StripPrefix, prefix=STRIP_PREFIX),
        Middleware(BearerAuth),
    ])
    import uvicorn
    # Silencia los tracebacks excesivos de validacion interna de FastMCP
    for name in ("fastmcp", "mcp", "FastMCP"):
        logging.getLogger(name).setLevel(logging.WARNING)

    is_write_enabled = os.environ.get("ENABLE_WRITE_TOOLS", "true").strip().lower() not in ("false", "0", "no", "off")
    user = os.environ.get("CUMULUS_USER")

    logger.info("=" * 60)
    logger.info("Iniciando cumulusmx-mcp v%s en http://0.0.0.0:%s", __version__, PORT)
    logger.info("URL backend CumulusMX: %s", BASE)
    logger.info("Herramientas de escritura (ENABLE_WRITE_TOOLS): %s", "publicadas" if WRITE_TOOLS_ENABLED
                else "ocultas (modo solo lectura)")
    logger.info("Timeout/reintentos hacia CumulusMX: %ss / %d", CUMULUS_TIMEOUT, CUMULUS_RETRIES)
    logger.info("Basic Auth hacia CumulusMX: %s", f"configurada (usuario: {user})" if user else "desactivada")
    logger.info("Bearer Auth para clientes MCP: %s", "activada" if TOKEN else "desactivada")
    logger.info("Prefijo strip (MCP_STRIP_PREFIX): %s", STRIP_PREFIX or "(ninguno)")
    logger.info("Nivel de log (LOG_LEVEL): %s", LOG_LEVEL)
    logger.info("Endpoint de salud (sin auth): http://0.0.0.0:%s/health", PORT)
    
    ok_cumulus, cumulus_msg = check_cumulus_connection()
    if ok_cumulus:
        logger.info("✓ Comprobacion CumulusMX: %s", cumulus_msg)
    else:
        logger.warning("⚠ Comprobacion CumulusMX: %s", cumulus_msg)
    logger.info("=" * 60)

    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level=LOG_LEVEL)
