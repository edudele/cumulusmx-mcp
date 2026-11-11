"""MCP para CumulusMX (lectura, escritura, informes y mantenimiento). Habla con su API HTTP por la red interna."""
import asyncio
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
__version__ = "1.4.0"

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


mcp = FastMCP("cumulusmx-mcp", version=__version__)


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


def _clip(t: str) -> str:
    if len(t) > MAX_CHARS:
        return t[:MAX_CHARS] + f"\n…[truncado: {len(t)} caracteres; usa rangos más pequeños o downsample]"
    return t


async def _get(path: str, params: dict | None = None) -> str:
    try:
        r = await _api_request("GET", path, params=params)
        r.raise_for_status()
        return _clip(r.text)
    except httpx.HTTPStatusError as e:
        return f"ERROR HTTP {e.response.status_code} en {path}: {e.response.text[:300]}"
    except httpx.HTTPError as e:
        return f"ERROR de conexión con CumulusMX ({type(e).__name__}): {e}"


async def _json(path: str, params: dict | None = None):
    try:
        r = await _api_request("GET", path, params=params)
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
async def station_info(kind: Literal["version", "units", "alarms", "snowinfo", "timeformat", "wsport", "displaylangs"]) -> str:
    """Información de la estación: versión, unidades, alarmas..."""
    return await _get(f"/api/info/{kind}.json")


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


@mcp.tool(annotations=WRITE_ACTION)
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


@mcp.tool(annotations=WRITE_ACTION)
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


@mcp.tool(annotations=WRITE_ACTION)
async def clear_error_log() -> str:
    """Borra el registro de errores acumulado en CumulusMX."""
    if (err := _check_write_enabled()):
        return err
    return await _post("/api/utils/clearerrorlog.json")


@mcp.tool(annotations=WRITE_ACTION)
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


@mcp.tool(annotations=WRITE_ACTION)
async def purgemysql() -> str:
    """Purga la cola de comandos pendientes/fallidos y la caché MySQL en CumulusMX."""
    if (err := _check_write_enabled()):
        return err
    return await _get("/api/utils/purgemysql")


# ---------------------------------------------------------------- informes ----

@mcp.tool(annotations=WRITE_ACTION)
async def generate_reports(
    report_type: Literal["year", "month"] = "month",
    year: int | None = None,
    month: int | None = None,
) -> str:
    """Genera informes NOAA anuales o mensuales en CumulusMX y los guarda en el servidor."""
    if (err := _check_write_enabled()):
        return err
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
        raise ValueError("report_type debe ser 'year' o 'month'")


@mcp.tool(annotations=WRITE_ACTION)
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

@mcp.tool(annotations=WRITE_ACTION)
async def edit_current_conditions(text: str) -> str:
    """Actualiza el texto descriptivo de las condiciones meteorológicas actuales de la estación."""
    if (err := _check_write_enabled()):
        return err
    return await _post(
        "/api/edit/currcond",
        content=text.encode("utf-8"),
        headers={"Content-Type": "text/plain; charset=utf-8"},
    )


@mcp.tool(annotations=WRITE_ACTION)
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


@mcp.tool(annotations=WRITE_ACTION)
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


@mcp.tool(annotations=WRITE_ACTION)
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


@mcp.tool(annotations=WRITE_ACTION)
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


@mcp.tool(annotations=WRITE_ACTION)
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


@mcp.tool(annotations=WRITE_ACTION)
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
        content=json.dumps(merged),
        headers={"Content-Type": "application/json"},
    )


@mcp.tool(annotations=READ_ONLY)
async def get_noaa_settings() -> str:
    """Obtiene la configuración de generación de informes NOAA en CumulusMX."""
    return await _get("/api/settings/noaadata.json")


@mcp.tool(annotations=WRITE_ACTION)
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
    logger.info("Herramientas de escritura (ENABLE_WRITE_TOOLS): %s", is_write_enabled)
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
